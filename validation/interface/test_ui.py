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
        expect(page.locator("#overlay g")).to_have_count(3)
        # measured in-page in one step: the overlay redraws often, so elements get replaced
        heights = page.evaluate(
            "[...document.querySelectorAll('#overlay text')].map(t => t.getBBox().height)"
        )
        assert max(heights) > 8  # readable, not microscopic
        page.locator(".open.left").click()
        page.locator(".open.right").click()
        left = page.locator("#left")
        left.locator("textarea").fill("UI referral: 3 y/o, dehydration")
        left.locator(".add-vitals").click()
        left.locator("input[data-code=heart-rate]").fill("150")
        left.locator("button[data-p=URGENT]").click()
        left.locator(".send").click()
        mine = left.locator(".msg.out", has_text="UI referral")
        expect(mine).to_contain_text("In transit")
        expect(mine).to_contain_text("Timing not guaranteed")
        right = page.locator("#right")
        expect(right.locator(".msg.in", has_text="UI referral")).to_contain_text(
            "Heart rate 150", timeout=20_000
        )
        expect(mine).to_contain_text("Delivered", timeout=20_000)
        mine.locator(".follow").click()
        timeline = page.locator("#journey .timeline")
        expect(timeline).to_contain_text("Ambulance 01 took", timeout=10_000)
        expect(timeline).to_contain_text("Rebuilt and opened at Hospital")
        expect(page.locator("#journey .journey-graph")).to_be_visible()
        OUT.mkdir(exist_ok=True)
        page.screenshot(path=OUT / "demo.png", full_page=True)


@pytest.mark.req("REQ-SAFE-01", "REQ-SAFE-03")
def test_expired_message_shows_not_delivered_alert(tmp_path: Path, page: Page) -> None:
    f = tmp_path / "s.yaml"
    f.write_text(STRANDED)
    sim = LiveSim(build(load_scenario(str(f)), 1, tmp_path / "run"), speed=3600)
    for url in serve(sim):
        page.goto(url)
        page.locator(".open.left").click()
        left = page.locator("#left")
        left.locator("textarea").fill("nobody can carry this")
        left.locator("button[data-p=CRITICAL]").click()  # 6 h lifetime = 6 s here
        left.locator(".send").click()
        expect(left.locator(".alert")).to_contain_text(
            "NOT DELIVERED — use another channel", timeout=20_000
        )
        page.screenshot(path=OUT / "not-delivered.png")


@pytest.mark.req("REQ-SAFE-03")
def test_language_switch_translates_the_page_and_flags_draft_swahili(
    tmp_path: Path, page: Page
) -> None:
    sim = LiveSim(build(load_scenario("skeleton"), 1, tmp_path), speed=60)
    for url in serve(sim):
        page.goto(url)
        expect(page.locator(".banner")).to_contain_text("Not for emergencies")
        expect(page.locator(".draft")).to_be_hidden()
        page.select_option("#lang", "sw")
        expect(page.locator(".banner")).to_contain_text("Si kwa dharura")
        expect(page.locator("html")).to_have_attribute("lang", "sw")
        expect(page.locator(".draft")).to_be_visible()  # machine-drafted, not yet reviewed
        expect(page.locator("#clock")).to_contain_text("Siku 1")
        page.reload()  # remembered
        expect(page.locator(".banner")).to_contain_text("Si kwa dharura")
        page.select_option("#lang", "en")
        expect(page.locator(".views button").first).to_have_text("Map")
