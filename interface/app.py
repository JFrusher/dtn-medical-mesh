"""FastAPI app for the endpoint UI and live map (spec §10). Backend-agnostic: the emulator
plugs in from simulation/live.py; a site-node backend replaces it on real hardware."""

import asyncio
import base64
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from endpoint.keystore import LockedOut, SignInError
from interface.telemetry import Telemetry

STATIC = Path(__file__).parent / "static"
TICK_S = 0.25  # how often the backend advances and the map refreshes
IDLE_LOCK_S = 300.0  # a staff session locks after 5 minutes without use (spec §10.2)
ADMIN = "*admin*"  # session scope of the hub admin console (no endpoint has this name)


@dataclass
class Session:
    endpoint: str
    staff: str
    last_used: float


class VitalIn(BaseModel):
    code: str
    value: float


class AttachmentIn(BaseModel):
    title: str
    content_type: str
    data_b64: str


class PatientIn(BaseModel):
    local_id: str
    name: str
    age: int
    sex: str


class ReferralIn(BaseModel):
    reason: str
    diagnosis: str = ""
    history: str = ""
    treatment: str = ""


class ResultIn(BaseModel):
    test: str
    conclusion: str


class MessageIn(BaseModel):
    to: str
    text: str = ""
    priority: str = "ROUTINE"
    vitals: list[VitalIn] = Field(default_factory=list)
    attachments: list[AttachmentIn] = Field(default_factory=list)
    in_response_to: str | None = None
    patient: PatientIn | None = None
    referral: ReferralIn | None = None
    decision: str | None = None  # accepted | declined | needs-info, answering in_response_to
    result: ResultIn | None = None


class SignIn(BaseModel):
    staff: str
    pin: str


class AdminSignIn(BaseModel):
    operator: str
    pin: str


class AdminAction(BaseModel):
    action: str
    target: str = ""


class ControlIn(BaseModel):
    speed: float | None = None
    paused: bool | None = None


class Backend(Protocol):
    def telemetry(self) -> Telemetry: ...
    def endpoints(self) -> list[dict[str, Any]]: ...
    def messages(self, endpoint: str) -> list[dict[str, Any]]: ...
    def patients(self, endpoint: str) -> list[dict[str, Any]]: ...
    def add_patient(self, endpoint: str, patient: PatientIn) -> None: ...
    def journeys(self) -> list[dict[str, Any]]: ...
    def journey(self, bundle_hex: str) -> dict[str, Any]: ...
    def sign_in(self, endpoint: str, staff: str, pin: str) -> None: ...  # SignInError, LockedOut
    def admin_sign_in(self, operator: str, pin: str) -> None: ...  # SignInError, LockedOut
    def admin_state(self) -> dict[str, Any]: ...
    def admin_act(self, operator: str, action: str, target: str) -> None: ...
    def send(
        self, endpoint: str, msg: MessageIn, attachments: list[tuple[str, str, bytes]], author: str
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
    sessions: dict[str, Session] = {}

    def session(name: str, authorization: str | None, active: bool = True) -> Session:
        """The signed-in staff member for this endpoint, or 401 (the app shows sign-in).
        Background polls pass active=False so they never hold off the idle lock."""
        token = (authorization or "").removeprefix("Bearer ")
        s = sessions.get(token)
        now = time.monotonic()
        if s is None or s.endpoint != name or now - s.last_used > IDLE_LOCK_S:
            sessions.pop(token, None)
            raise HTTPException(status_code=401, detail="sign in")
        if active:
            s.last_used = now
        return s

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    async def demo() -> FileResponse:
        return FileResponse(STATIC / "demo.html")

    @app.get("/admin")
    async def admin_page() -> FileResponse:
        return FileResponse(STATIC / "admin.html")

    @app.post("/api/admin/session")
    async def admin_sign_in(body: AdminSignIn) -> dict[str, str]:
        try:
            backend.admin_sign_in(body.operator, body.pin)
        except SignInError as e:
            raise HTTPException(status_code=401, detail="wrong PIN") from e
        except LockedOut as e:
            wait = e.until - time.monotonic()
            raise HTTPException(status_code=429, detail={"retry_after_s": wait}) from e
        token = secrets.token_urlsafe(24)
        sessions[token] = Session(ADMIN, body.operator, time.monotonic())
        return {"token": token, "operator": body.operator}

    @app.get("/api/admin")
    async def admin_state(
        authorization: str | None = Header(None), x_active: str | None = Header(None)
    ) -> dict[str, Any]:
        session(ADMIN, authorization, x_active == "1")
        return backend.admin_state()

    @app.post("/api/admin/actions")
    async def admin_act(
        body: AdminAction, authorization: str | None = Header(None)
    ) -> dict[str, str]:
        s = session(ADMIN, authorization)
        try:
            backend.admin_act(s.staff, body.action, body.target)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        return {"ok": "true"}

    @app.get("/api/telemetry")
    async def telemetry() -> Telemetry:
        return backend.telemetry()

    @app.get("/api/endpoints")
    async def endpoints() -> list[dict[str, Any]]:
        return backend.endpoints()

    @app.post("/api/endpoints/{name}/session")
    async def sign_in(name: str, body: SignIn) -> dict[str, str]:
        try:
            backend.sign_in(_known(backend, name), body.staff, body.pin)
        except SignInError as e:
            raise HTTPException(status_code=401, detail="wrong PIN") from e
        except LockedOut as e:
            wait = e.until - time.monotonic()
            raise HTTPException(status_code=429, detail={"retry_after_s": wait}) from e
        token = secrets.token_urlsafe(24)
        sessions[token] = Session(name, body.staff, time.monotonic())
        return {"token": token, "staff": body.staff}

    @app.delete("/api/session")
    async def sign_out(authorization: str | None = Header(None)) -> dict[str, str]:
        sessions.pop((authorization or "").removeprefix("Bearer "), None)
        return {"ok": "true"}

    @app.get("/api/endpoints/{name}/messages")
    async def messages(
        name: str, authorization: str | None = Header(None), x_active: str | None = Header(None)
    ) -> list[dict[str, Any]]:
        session(_known(backend, name), authorization, x_active == "1")
        return backend.messages(name)

    @app.post("/api/endpoints/{name}/messages")
    async def send(
        name: str, msg: MessageIn, authorization: str | None = Header(None)
    ) -> dict[str, str]:
        s = session(_known(backend, name), authorization)
        try:
            files = [
                (a.title, a.content_type, base64.b64decode(a.data_b64)) for a in msg.attachments
            ]
            return {"id": backend.send(name, msg, files, s.staff)}
        except ValueError as e:  # unknown recipient, over 32 MB, bad vital: tell the user
            raise HTTPException(status_code=422, detail=str(e)) from e

    @app.get("/api/endpoints/{name}/patients")
    async def patients(
        name: str, authorization: str | None = Header(None), x_active: str | None = Header(None)
    ) -> list[dict[str, Any]]:
        session(_known(backend, name), authorization, x_active == "1")
        return backend.patients(name)

    @app.post("/api/endpoints/{name}/patients")
    async def add_patient(
        name: str, patient: PatientIn, authorization: str | None = Header(None)
    ) -> dict[str, str]:
        session(_known(backend, name), authorization)
        try:
            backend.add_patient(name, patient)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        return {"ok": "true"}

    @app.get("/api/journeys")
    async def journeys() -> list[dict[str, Any]]:
        """For the emulator's operator view (map and journey), not the staff app."""
        return backend.journeys()

    @app.get("/api/journey/{bundle_id}")
    async def journey(bundle_id: str) -> dict[str, Any]:
        try:
            return backend.journey(bundle_id)
        except (KeyError, ValueError) as e:
            raise HTTPException(status_code=404, detail=f"no message {bundle_id!r}") from e

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
