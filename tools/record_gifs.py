"""Record the README GIFs from the real emulator UI (headless Chromium + ffmpeg).

    uv run python tools/record_gifs.py            # all
    uv run python tools/record_gifs.py hero chat  # some

Writes docs/media/<name>.gif, a screen recording of the actual demo, scripted. The film job
writes docs/media/hero.webp, frame by frame.
"""

import math
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import uvicorn
from playwright.sync_api import Page, ViewportSize, sync_playwright

from interface.app import create_app
from simulation.live import DEMO_PIN, LiveSim
from simulation.mobility import Route
from simulation.runner import build
from simulation.scenario import load_scenario

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs/media"
VIEW: ViewportSize = {"width": 1280, "height": 760}


@contextmanager
def serve(sim: LiveSim) -> Iterator[str]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(sim), port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join()


def record(name: str, sim: LiveSim, script: Callable[[Page], None], width: int = 960) -> None:
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg is required")
    with tempfile.TemporaryDirectory() as tmp, serve(sim) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport=VIEW, record_video_dir=tmp, record_video_size=VIEW)
        opened = time.monotonic()
        page = context.new_page()
        page.goto(url)
        page.wait_for_selector("#overlay g")
        time.sleep(1.5)  # map tiles settle
        start = time.monotonic() - opened
        script(page)
        length = time.monotonic() - opened - start
        video = page.video
        context.close()
        browser.close()
        assert video is not None
        webm = video.path()
        gif = OUT / f"{name}.gif"
        palette = (
            f"fps=12,scale={width}:-1:flags=lanczos,split[a][b];"
            "[a]palettegen=max_colors=96:stats_mode=diff[p];"
            "[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle"
        )
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-ss",
                f"{start:.2f}",
                "-t",
                f"{length:.2f}",
                "-i",
                str(webm),
                "-vf",
                palette,
                str(gif),
            ],
            check=True,
        )
        print(f"{gif.relative_to(ROOT)}: {length:.0f} s, {gif.stat().st_size / 1e6:.1f} MB")


def live(scenario: str, start_s: float, speed: float) -> LiveSim:
    sim = LiveSim(build(load_scenario(scenario), 1, Path(tempfile.mkdtemp())), speed=speed)
    sim.r.world.scheduler.run_until(start_s)
    return sim


def map_tour() -> None:
    """The whole district: vehicles on real roads, contacts, pieces moving, counts climbing."""
    record("map", live("arusha", 7 * 3600, 240), lambda page: time.sleep(14))  # morning rounds


def chat() -> None:
    """Murieti writes a critical referral; the ambulance carries it; the hospital opens it."""
    sim = live("arusha", 0, 90)
    w = sim.r.world
    route = w.nodes["AMBULANCE"].mobility
    assert isinstance(route, Route) and route.stops
    murieti_stop = route.waypoints[route.stops[1]]  # snapped to the road, ~25 m from the clinic
    t = 3600.0  # first moment after 1 h that the ambulance is parked there
    while math.dist(route.position(t), murieti_stop) > 1:
        t += 10
        assert t < 86400, "ambulance never reaches Murieti"
    w.scheduler.run_until(t + 30)
    sim.control(None, True)

    def script(page: Page) -> None:
        page.locator(".open.left").click()
        left = page.locator("#left")
        time.sleep(0.6)
        left.locator("input[type=password]").type(DEMO_PIN, delay=120)
        left.get_by_role("button", name="Sign in").click()
        time.sleep(0.6)
        left.get_by_role("tab", name="New referral").click()
        left.locator("[name=reason]").type("34 weeks, BP 160/110, headache", delay=35)
        left.locator("[name=diagnosis]").type("Pre-eclampsia", delay=35)
        left.locator("input[data-code=bp-systolic]").type("160", delay=60)
        left.locator("input[data-code=heart-rate]").type("104", delay=60)
        left.locator("button[data-p=CRITICAL]").click()
        time.sleep(0.4)
        left.locator(".send").click()
        page.request.post(page.url + "api/control", data={"paused": False, "speed": 90})
        hospital = sim.r.endpoints["hospital"]
        deadline = time.monotonic() + 60
        while not any("34 weeks" in r.message.summary() for r in hospital.inbox):  # watch the map
            assert time.monotonic() < deadline, "referral never reached the hospital"
            time.sleep(0.2)
        time.sleep(1.5)
        left.locator(".msg.out", has_text="34 weeks").locator(".follow").click()
        time.sleep(4)

    record("chat", sim, script)


def stress() -> None:
    """Loss, power cuts, clock faults and three attackers; nothing is lost silently."""
    record("stress", live("stress", 2400, 600), lambda page: time.sleep(14))


def film() -> None:
    """The journey film: stepped frame by frame and encoded in the browser, so no ffmpeg."""
    from tools.film import film as shoot  # noqa: PLC0415  # tools.film imports this module

    shoot()


def film_encode() -> None:
    """Encode the film again from the frames already on disk."""
    from tools.film import encode  # noqa: PLC0415  # tools.film imports this module

    encode()


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = {
        "map": map_tour,
        "chat": chat,
        "stress": stress,
        "film": film,
        "film-encode": film_encode,
    }
    everything = [job for job in jobs if job != "film-encode"]
    for name in sys.argv[1:] or everything:
        jobs[name]()
