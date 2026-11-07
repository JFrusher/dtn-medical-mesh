"""FastAPI app for the endpoint UI and live map (spec §10). Backend-agnostic: the emulator
plugs in from simulation/live.py; a site-node backend replaces it on real hardware."""

import asyncio
import base64
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Protocol

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from interface.telemetry import Telemetry

STATIC = Path(__file__).parent / "static"
TICK_S = 0.25  # how often the backend advances and the map refreshes


class VitalIn(BaseModel):
    code: str
    value: float


class AttachmentIn(BaseModel):
    title: str
    content_type: str
    data_b64: str


class MessageIn(BaseModel):
    to: str
    text: str = ""
    priority: str = "ROUTINE"
    vitals: list[VitalIn] = Field(default_factory=list)
    attachments: list[AttachmentIn] = Field(default_factory=list)
    in_response_to: str | None = None


class ControlIn(BaseModel):
    speed: float | None = None
    paused: bool | None = None


class Backend(Protocol):
    def telemetry(self) -> Telemetry: ...
    def endpoints(self) -> list[dict[str, Any]]: ...
    def messages(self, endpoint: str) -> list[dict[str, Any]]: ...
    def send(
        self, endpoint: str, msg: MessageIn, attachments: list[tuple[str, str, bytes]]
    ) -> str: ...
    def control(self, speed: float | None, paused: bool | None) -> None: ...
    def advance(self, real_seconds: float) -> None: ...


def create_app(backend: Backend) -> FastAPI:
    """Every handler is async so all backend access runs on the event loop's single thread,
    interleaved with the clock task: no data races, no thread pool."""

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async def clock() -> None:
            while True:
                backend.advance(TICK_S)
                await asyncio.sleep(TICK_S)

        task = asyncio.create_task(clock())
        yield
        task.cancel()

    app = FastAPI(title="dtn-medical-mesh", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    async def demo() -> FileResponse:
        return FileResponse(STATIC / "demo.html")

    @app.get("/api/telemetry")
    async def telemetry() -> Telemetry:
        return backend.telemetry()

    @app.get("/api/endpoints")
    async def endpoints() -> list[dict[str, Any]]:
        return backend.endpoints()

    @app.get("/api/endpoints/{name}/messages")
    async def messages(name: str) -> list[dict[str, Any]]:
        return backend.messages(_known(backend, name))

    @app.post("/api/endpoints/{name}/messages")
    async def send(name: str, msg: MessageIn) -> dict[str, str]:
        try:
            files = [
                (a.title, a.content_type, base64.b64decode(a.data_b64)) for a in msg.attachments
            ]
            return {"id": backend.send(_known(backend, name), msg, files)}
        except ValueError as e:  # unknown recipient, over 32 MB, bad vital: tell the user
            raise HTTPException(status_code=422, detail=str(e)) from e

    @app.post("/api/control")
    async def control(c: ControlIn) -> dict[str, str]:
        backend.control(c.speed, c.paused)
        return {"ok": "true"}

    @app.websocket("/ws")
    async def stream(ws: WebSocket) -> None:
        await ws.accept()
        try:
            while True:
                await ws.send_json(backend.telemetry())
                await asyncio.sleep(TICK_S * 2)
        except WebSocketDisconnect:
            return

    return app


def _known(backend: Backend, name: str) -> str:
    if name not in {e["name"] for e in backend.endpoints()}:
        raise HTTPException(status_code=404, detail=f"no endpoint {name!r}")
    return name
