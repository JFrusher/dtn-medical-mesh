"""Browser smoke test of the demo page (Playwright, headless Chromium)."""

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import Page, expect, sync_playwright

from interface.app import create_app
from simulation.live import LiveSim
from simulation.runner import build
from simulation.scenario import load_scenario

OUT = Path(__file__).parents[2] / "out"
STRANDED = """
name: stranded
duration_s: 100000
nodes:
  - {id: CLINIC, role: site, sa: SA_C, at: [0, 0]}
  - {id: HOSPITAL, role: hub, sa: SA_H, at: [9000, 0]}
endpoints: [{name: clinic, node: CLINIC}, {name: hospital, node: HOSPITAL}]
"""


def serve(sim: LiveSim) -> Iterator[str]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(sim), port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join()


@pytest.fixture
def page() -> Iterator[Page]:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser.new_page(viewport={"width": 1400, "height": 900})
        browser.close()


@pytest.mark.req("REQ-SAFE-03", "REQ-SAFE-04")
def test_demo_sends_and_shows_delivery(tmp_path: Path, page: Page) -> None:
    sim = LiveSim(build(load_scenario("skeleton"), 1, tmp_path), speed=600)
    for url in serve(sim):
        page.goto(url)
        expect(page.locator(".banner")).to_contain_text("Not for emergencies")
        expect(page.locator("#map g")).to_have_count(3)
        label = page.locator("#map text").first
        assert label.bounding_box()["height"] > 8  # type: ignore[index]  # readable, not microscopic
        left = page.locator("#left")
        left.locator("textarea").fill("UI referral: 3 y/o, dehydration")
        left.locator("summary").click()
        left.locator("input[data-code=heart-rate]").fill("150")
        left.locator("button[data-p=URGENT]").click()
        left.locator(".send").click()
        mine = left.locator(".msg.out", has_text="UI referral")
        expect(mine).to_contain_text("in transit")
        expect(mine).to_contain_text("Timing not guaranteed")
        right = page.locator("#right")
        expect(right.locator(".msg.in", has_text="UI referral")).to_contain_text(
            "Heart rate 150", timeout=20_000
        )
        expect(mine).to_contain_text("delivered", timeout=20_000)
        OUT.mkdir(exist_ok=True)
        page.screenshot(path=OUT / "demo.png", full_page=True)


@pytest.mark.req("REQ-SAFE-01", "REQ-SAFE-03")
def test_expired_message_shows_not_delivered_alert(tmp_path: Path, page: Page) -> None:
    f = tmp_path / "s.yaml"
    f.write_text(STRANDED)
    sim = LiveSim(build(load_scenario(str(f)), 1, tmp_path / "run"), speed=3600)
    for url in serve(sim):
        page.goto(url)
        left = page.locator("#left")
        left.locator("textarea").fill("nobody can carry this")
        left.locator("button[data-p=CRITICAL]").click()  # 6 h lifetime = 6 s here
        left.locator(".send").click()
        expect(left.locator(".alert")).to_contain_text(
            "NOT DELIVERED — use another channel", timeout=20_000
        )
        page.screenshot(path=OUT / "not-delivered.png")
