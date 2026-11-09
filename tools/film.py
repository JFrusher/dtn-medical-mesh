"""The journey film: one real referral followed from keyboard to hospital and back.

    python tools/record_gifs.py film

Every event on screen comes from the emulator: handovers, refusals, rebuild and confirmation.
The schedule is staged for the film (see write_film_scenario). If the run stops telling the
story, the film refuses to render instead of showing something that did not happen.
"""

import asyncio
import base64
import math
import shutil
import struct
import tempfile
import time
from collections.abc import Callable, Iterable
from concurrent.futures import Future
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, TypeVar

import yaml
from playwright.sync_api import Locator, Page, Route, sync_playwright

from interface.app import MessageIn, ReferralIn
from node.bundle import Receipt
from node.core import NodeCore
from simulation.live import DEMO_PIN, LiveSim
from simulation.runner import RunResult, build
from simulation.scenario import SCENARIO_DIR, load_scenario
from simulation.virtual_hw import SIM_EPOCH
from simulation.world import World
from tools.record_gifs import OUT, ROOT, serve

T = TypeVar("T")

REFERRAL = "28F, 34 weeks, BP 160/110, headache"
ORIGIN, DEST = "MURIETI", "HOSPITAL"
SEED, SEND_AT_S = 7, 37800.0
KISONGO_AT = [-3.3719, 36.57195]
# Metres along the ambulance's road, measured back from Murieti.
LIGHT_M, BIKE_TURN_M, MEET_M, ROADSIDE_M = 70, 200, 1500, 3500
DRAWER_PX = 380
FADE_FRAMES = 6
SIGN_IN_FRAMES = 15
FRAME_SIZE = (1280, 720)
WEBP_QUALITY = 0.75  # about half the size of 0.9, at the cost of slightly softer road detail
CLUTTER_M = 400
WARM_PAPER = ":root{--paper:#efe5d0;--surface:#fbf6ea;--rule:#dccfb8;--road:#cdbf9f}"
_HOLDING = {
    "OUTREACH_BIKE": "Now on the outreach motorbike",
    "AMBULANCE": "Now on the ambulance",
}


@dataclass(frozen=True)
class Hop:
    t: float
    sender: str
    receiver: str
    slots: frozenset[int]


def road_index(road: list[list[float]], metres: float) -> int:
    """The waypoint `metres` along a [lat, lon] road (or the last one)."""
    travelled = 0.0
    for i, (a, b) in enumerate(zip(road, road[1:], strict=False)):
        north = (b[0] - a[0]) * 110540
        east = (b[1] - a[1]) * 111320 * math.cos(math.radians(a[0]))
        travelled += math.hypot(north, east)
        if travelled > metres:
            return i
    return len(road) - 1


def _round_trip(way: list[list[float]], speed: int, start_s: float) -> dict[str, Any]:
    """Drive `way`, wait, drive back, wait: no random delays, so the timing is exact."""
    points = way + way[-2:0:-1]
    return {
        "waypoints": points,
        "speed": speed,
        "dwell_s": 600,
        "start_s": start_s,
        "stops": [0, len(way) - 1],
    }


def write_film_scenario(directory: Path) -> Path:
    """Arusha's real roads and the stress scenario's lossy radio, with a staged schedule.

    The relay is the only physical path. The motorbike only rides between the clinic's street
    light and a meeting point, and the ambulance only drives between that point and the
    hospital. A forging carrier loads an older message at Kisongo, then parks on the
    ambulance's road. It needs that message because it forges copies of what it holds, and
    only a piece the receiver has not seen is checked and refused. Events are all real.
    """
    raw = yaml.safe_load((SCENARIO_DIR / "arusha.yaml").read_text())
    radio = yaml.safe_load((SCENARIO_DIR / "stress.yaml").read_text())["radio"]
    radio.pop("bit_flip_per_frame", None)  # so refused pieces come from the forger alone
    raw["name"] = "arusha-film"
    raw["radio"] = radio
    raw["workload"] = []
    raw["messages"] = [
        {
            "at_s": SEND_AT_S - 1200,
            "from": "kisongo",
            "to": "usa-river",
            "priority": "ROUTINE",
            "text": "Vitals",
        }
    ]
    nodes = {n["id"]: n for n in raw["nodes"]}
    ambulance = nodes["AMBULANCE"]["route"]
    from_murieti = ambulance["waypoints"][: ambulance["stops"][1] + 1][::-1]
    meet = road_index(from_murieti, MEET_M)
    turn = road_index(from_murieti, BIKE_TURN_M)
    nodes["LIGHT_MOSHI_ROAD"]["at"] = from_murieti[road_index(from_murieti, LIGHT_M)]
    nodes["AMBULANCE"]["route"] = _round_trip(from_murieti[meet:], 11, SEND_AT_S + 800)
    nodes["OUTREACH_BIKE"]["route"] = _round_trip(from_murieti[turn : meet + 1], 8, SEND_AT_S - 60)
    roadside = from_murieti[road_index(from_murieti, ROADSIDE_M)]
    forger = {
        "id": "FORGER",
        "role": "mule",
        "route": {
            "waypoints": [KISONGO_AT, roadside],
            "speed": 10,
            "dwell_s": 3600,
            "start_s": SEND_AT_S - 3600,
        },
        "hostile": {"attacks": ["forge_shards"]},
    }
    keep = {"AMBULANCE", "OUTREACH_BIKE"}
    raw["nodes"] = [n for n in raw["nodes"] if n["role"] != "mule" or n["id"] in keep] + [forger]
    path = directory / "film.yaml"
    path.write_text(yaml.safe_dump(raw))
    return path


def hops_for(receipts: Iterable[tuple[float, str, Receipt]], bundle_id: bytes) -> list[Hop]:
    hops = []
    for t, receiver, r in receipts:
        slots = frozenset(s[32] for s in r.shard_ids if s[:32] == bundle_id)
        if slots:
            hops.append(Hop(t, r.sender, receiver, slots))
    return hops


def first_chain(hops: list[Hop], origin: str, dest: str) -> list[str]:
    """Who first brought the message to dest, who first brought it to them, back to the origin."""
    chain = [dest]
    while chain[0] != origin:
        arrivals = [h for h in hops if h.receiver == chain[0] and h.sender not in chain]
        if not arrivals:
            break
        chain.insert(0, min(arrivals, key=lambda h: h.t).sender)
    return chain


def is_relay(chain: list[str]) -> bool:
    return chain == [ORIGIN, "LIGHT_MOSHI_ROAD", "OUTREACH_BIKE", "AMBULANCE", DEST]


def legs(hops: list[Hop], chain: list[str]) -> list[tuple[str, float]]:
    """Who holds the message, from when."""
    return [(n, min(h.t for h in hops if h.receiver == n)) for n in chain[1:-1]]


def refusal_window(
    samples: list[tuple[float, int]], start: float, end: float, length: float
) -> tuple[float, float, int] | None:
    """(first refusal in start..end, end of the shot, pieces refused in between)."""
    for (_, before), (t, count) in zip(samples, samples[1:], strict=False):
        if count > before and start <= t <= end:
            stop = min(t + length, end)
            return t, stop, [c for s, c in samples if s <= stop][-1] - before
    return None


def forged_count(world: World) -> int:
    return sum(
        n.core.dropped["bad_signature"] + n.core.dropped["corrupt"]
        for n in world.nodes.values()
        if n.up and n.core_class is NodeCore
    )


def verify_cast(chain: list[str], forged_delta: int, opened: str | None, acked: str | None) -> None:
    """Stop the render when the run no longer tells the story the film shows."""
    if not is_relay(chain):
        raise RuntimeError(f"run no longer takes the relay: {chain}")
    if forged_delta <= 0:
        raise RuntimeError("run refused no forged frames during the attack shot")
    if not opened or not acked:
        raise RuntimeError(f"run did not finish: opened {opened}, confirmed {acked}")


@dataclass(frozen=True)
class Shot:
    name: str
    frames: int


FPS = 15
SHOTS = (
    Shot("opening", 45),
    Shot("send", 75),
    Shot("bytes", 45),
    Shot("travel", 105),
    Shot("attack", 45),
    Shot("arrive", 60),
    Shot("journey", 45),
    Shot("card", 45),
)
_CAPTIONS = {
    "opening": ("A clinic outside Arusha has no signal.", ""),
    "bytes": (
        "Sealed for the hospital and cut into 12 pieces.",
        "Ed25519, X25519, XChaCha20-Poly1305. Zeros are padding, which hides the length",
    ),
    "travel": ("Vehicles already on the road carry the pieces but can't read them.", ""),
    "attack": (
        "Other devices refused {n} forged pieces from a fake carrier.",
        "Header signatures and Merkle proofs",
    ),
    "arrive": ("The hospital needs any 4 of the 12 to open it.", "Reed-Solomon erasure coding"),
    "journey": ("A signed confirmation comes back to the clinic.", ""),
}


def caption(shot: str, forged: int | None = None) -> tuple[str, str] | None:
    """(headline, note), or None for shots without a caption."""
    if shot not in _CAPTIONS:
        return None
    headline, note = _CAPTIONS[shot]
    if "{n}" in headline:
        if not forged or forged < 1:
            raise ValueError("the attack caption needs a real refused count")
        headline = headline.format(n=f"{forged:,}")
    return headline, note


def lit_slots(hops: list[Hop], dest: str, progress: float) -> list[int]:
    """Pieces shown as arrived at dest. A receipt can carry 12, so they light one at a time."""
    order: list[int] = []
    for h in sorted((h for h in hops if h.receiver == dest), key=lambda h: h.t):
        order += [s for s in sorted(h.slots) if s not in order]
    return order[: round(progress * len(order))]


def sim_time(frame: int, beats: list[tuple[int, float]]) -> float:
    """Simulated time on a film frame: straight lines between (frame, sim_t) beats."""
    for (f0, t0), (f1, t1) in zip(beats, beats[1:], strict=False):
        if f0 <= frame <= f1:
            return t0 + (t1 - t0) * (frame - f0) / (f1 - f0)
    return beats[-1][1] if frame > beats[-1][0] else beats[0][1]


def typing_frames(text: str) -> list[str]:
    """What the message box holds on each frame: one mistyped digit, fixed, and one pause."""
    frames: list[str] = []
    for i in range(1, len(text) + 1):
        typed = text[:i]
        if typed.endswith("BP 16"):  # reaches for 9, sees it, goes back
            frames += [typed, typed + "9"]
        frames.append(typed)
        if typed.endswith("34 weeks,"):
            frames += [typed] * 8
    return frames


def ease(x: float) -> float:
    """Smoothstep: 0 to 1 with no sudden start or stop."""
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def _send(sim: LiveSim) -> bytes:
    msg = MessageIn(to="hospital", priority="CRITICAL", referral=ReferralIn(reason=REFERRAL))
    return bytes.fromhex(sim.send("murieti", msg, [], "rehearsal"))


@dataclass(frozen=True)
class Cast:
    """How the run goes, learned from a rehearsal so each shot can be timed before it is shot."""

    hops: list[Hop]
    chain: list[str]
    legs: list[tuple[str, float]]
    opened_t: float
    acked_t: float
    attack_t: float
    attack_end_t: float
    forged: int

    def first_into(self, node: str) -> float:
        return min(h.t for h in self.hops if h.receiver == node)


def _opened_t(r: RunResult, bid: bytes) -> float | None:
    return next((m.at_s for e in r.endpoints.values() for m in e.inbox if m.bundle_id == bid), None)


def _acked_t(r: RunResult, bid: bytes) -> float | None:
    done = r.endpoints["murieti"].sent[bid].delivered_ms
    return done / 1000 - SIM_EPOCH if done else None


def rehearse(scenario: Path) -> Cast:
    sim = LiveSim(build(load_scenario(str(scenario)), SEED, Path(tempfile.mkdtemp())))
    r, scheduler = sim.r, sim.r.world.scheduler
    scheduler.run_until(SEND_AT_S)
    bid = _send(sim)
    samples = [(SEND_AT_S, forged_count(r.world))]
    t = SEND_AT_S
    while t < SEND_AT_S + 3 * 3600 and _acked_t(r, bid) is None:
        t += 10
        scheduler.run_until(t)
        samples.append((t, forged_count(r.world)))
    hops = hops_for(r.world.receipts, bid)
    chain = first_chain(hops, ORIGIN, DEST)
    opened, acked = _opened_t(r, bid), _acked_t(r, bid)
    if not is_relay(chain) or opened is None or acked is None:
        verify_cast(chain, 1, str(opened) if opened else None, str(acked) if acked else None)
    assert opened is not None and acked is not None
    into = dict(legs(hops, chain))
    arrival = min(h.t for h in hops if h.receiver == DEST)
    attack = refusal_window(samples, into["AMBULANCE"], arrival, 120)
    if attack is None:
        raise RuntimeError("run refused no forged pieces on the ambulance's last leg")
    return Cast(hops, chain, legs(hops, chain), opened, acked, *attack)


class FilmSim(LiveSim):
    """Time moves only when the film says. All access runs on the server's event loop."""

    loop: asyncio.AbstractEventLoop | None = None

    def advance(self, real_seconds: float) -> None:
        self.loop = self.loop or asyncio.get_running_loop()

    def on_loop(self, fn: Callable[[], T]) -> T:
        while self.loop is None:
            time.sleep(0.01)
        done: Future[T] = Future()

        def call() -> None:
            try:
                done.set_result(fn())
            except Exception as e:  # noqa: BLE001  # re-raised on the film's thread
                done.set_exception(e)

        self.loop.call_soon_threadsafe(call)
        return done.result()

    def to(self, t: float) -> None:
        self.on_loop(lambda: self.r.world.scheduler.run_until(t))


_SETTLE = """() => new Promise(resolve => {
  const m = window.netmap.map;
  const done = () => requestAnimationFrame(() => requestAnimationFrame(resolve));
  if (m.loaded() && m.areTilesLoaded() && !m.isMoving()) done();
  else { m.once("idle", done); setTimeout(done, 5000); }
})"""
_CAMERA = """([id, zoom, left, right, k]) => {
  const m = window.netmap.map, n = window.netmap.t.nodes.find(v => v.id === id);
  const [lng, lat] = window.netmap.lnglat([n.x, n.y]);
  const c = m.getCenter(), p = m.getPadding(), mix = (a, b) => a + (b - a) * k;
  m.jumpTo({ center: [mix(c.lng, lng), mix(c.lat, lat)], zoom: mix(m.getZoom(), zoom),
    padding: { left: mix(p.left, left), right: mix(p.right, right), top: 0, bottom: 0 } });
}"""
_SELECT = """async id => window.netmap.select(await (await fetch(`/api/journey/${id}`)).json())"""


@dataclass(frozen=True)
class View:
    node: str
    zoom: float
    left: int = 0
    right: int = 0
    k: float = 0.16  # share of the way to the target per frame, so the camera trails and settles


def _positions(sim: LiveSim, *nodes: str) -> list[tuple[float, float]]:
    return [sim.r.world.position(n) for n in nodes]


def _film_view(telemetry: Any) -> Any:
    """Hide the forger's staged route, and its load so the map labels it by name."""
    for node in telemetry["nodes"]:
        if node["id"] == "FORGER":
            node["route"], node["shards"] = None, 0
    return telemetry


class Stage:
    def __init__(self, page: Page, sim: FilmSim, frames: Path) -> None:
        self.page, self.sim, self.frames, self.count = page, sim, frames, 0
        self.caption_age = FADE_FRAMES

    def _frame(self, view: View | None) -> Path:
        telemetry = _film_view(self.sim.on_loop(self.sim.telemetry))
        self.page.evaluate("t => window.render(t)", telemetry)
        if view:
            self.page.evaluate(_CAMERA, [view.node, view.zoom, view.left, view.right, view.k])
        self.page.evaluate(_SETTLE)
        if self.caption_age < FADE_FRAMES:
            self.caption_age += 1
            self.film("panelOpacity", ease(self.caption_age / FADE_FRAMES))
        path = self.frames / f"{self.count:05d}.png"
        self.page.screenshot(path=path)
        self.count += 1
        return path

    def snap(self, view: View | None = None, repeat: int = 1) -> None:
        """Draw the emulator's current state and photograph it `repeat` frames long."""
        for remaining in range(repeat, 0, -1):
            path = self._frame(view)
            if view is None and self.caption_age >= FADE_FRAMES:  # nothing else can move
                for _ in range(remaining - 1):
                    shutil.copy(path, self.frames / f"{self.count:05d}.png")
                    self.count += 1
                return

    def film(self, method: str, *args: Any) -> None:
        self.page.evaluate("([m, a]) => window.film[m](...a)", [method, list(args)])

    def say(self, shot: str, forged: int | None = None, right: bool = False) -> None:
        pair = caption(shot, forged)
        assert pair is not None
        self.film("caption", pair[0], pair[1], right)
        self.caption_age = 0

    def caption_out(self, frame: int, total: int) -> None:
        if total - frame <= FADE_FRAMES:
            self.film("panelOpacity", ease((total - frame - 1) / FADE_FRAMES))


def _open_page(page: Page, url: str) -> None:
    def add_map_handle(route: Route) -> None:
        response = route.fetch()
        marker = "window.render = render;"
        assert marker in response.text()
        body = response.text().replace(marker, f"{marker} window.netmap = map;")
        route.fulfill(response=response, body=body)

    def warm_paper(route: Route) -> None:
        response = route.fetch()
        route.fulfill(response=response, body=response.text() + WARM_PAPER)

    page.add_init_script("window.WebSocket = function () { return {}; };")  # no live redraws
    page.route(url + "/", add_map_handle)
    page.route("**/static/style.css", warm_paper)
    page.goto(url + "/")
    page.wait_for_function("window.render && window.netmap")


def _submit_pin(drawer: Locator) -> None:
    drawer.locator(".signin .send").click()
    drawer.locator(".tabs").wait_for()


def _shoot(stage: Stage, cast: Cast) -> bytes:
    """Everything from the first frame to the end card. Returns the film message's bundle id."""
    page, sim = stage.page, stage.sim
    frames_of = {s.name: s.frames for s in SHOTS}
    left, right = page.locator("#left"), page.locator("#right")

    # Opening: paper fades to the whole district, then the camera settles on the clinic.
    stage.film("clear")
    stage.say("opening")
    opening, slide_at = frames_of["opening"], frames_of["opening"] - 8
    for i in range(opening):
        stage.film("layer", "paper", 1 - ease((i + 1) / 8))
        if i == slide_at:
            stage.film("drawer", "left", 0)
            page.locator(".open.left").click()
        if i >= slide_at:
            stage.film("drawer", "left", ease((i - slide_at + 1) / 8))
        stage.caption_out(i, opening)
        stage.snap(View(ORIGIN, 14.0, left=DRAWER_PX if i >= slide_at else 0, k=0.07))
    stage.film("clear")

    beside_chat = View(ORIGIN, 14.0, left=DRAWER_PX)
    pin = left.locator("input[type=password]")
    for typed in range(1, len(DEMO_PIN) + 1):
        pin.fill(DEMO_PIN[:typed])
        stage.snap(beside_chat, 2)
    _submit_pin(left)
    left.locator("[data-tab=referral]").click()
    stage.snap(beside_chat, SIGN_IN_FRAMES - 2 * len(DEMO_PIN))
    typing = typing_frames(REFERRAL)
    for text in typing:
        left.locator("[name=reason]").fill(text)
        stage.snap(beside_chat)
    left.locator("button[data-p=CRITICAL]").click()
    stage.snap(beside_chat, 8)
    left.locator(".send").click()
    left.locator(".msg.out", has_text="34 weeks").wait_for()
    stage.snap(beside_chat, frames_of["send"] - SIGN_IN_FRAMES - len(typing) - 8)

    murieti = sim.r.endpoints["murieti"]
    bid = sim.on_loop(lambda: list(murieti.sent)[-1])
    sim.to(SEND_AT_S + 2)
    held = sim.on_loop(lambda: list(sim.r.world.nodes[ORIGIN].core.shards.values()))
    shards = sorted((s for s in held if s.header.bundle_id == bid), key=lambda s: s.index)
    if len(shards) != 12:
        raise RuntimeError(f"expected 12 pieces at the clinic, found {len(shards)}")
    # The ciphertext dissolves in and its rows rule themselves one at a time.
    stage.film("hex", [s.data[:24].hex(" ") for s in shards])
    stage.say("bytes")
    for i in range(frames_of["bytes"]):
        stage.film("layer", "hex", ease((i + 1) / 8))
        stage.film("hexRows", (i + 1) // 1.5)
        stage.caption_out(i, frames_of["bytes"])
        stage.snap()
    stage.film("drawer", "left", 1)
    left.locator(".close").click()

    # The row numbers travel down to become the piece counter while the map dissolves in.
    route, travel = cast.legs, frames_of["travel"]
    each = travel // len(route)
    beats = [(i * each, t) for i, (_, t) in enumerate(route)] + [(travel, cast.attack_t - 1)]
    stage.say("travel")
    stage.film("slots", [])
    pair = caption("travel")
    assert pair is not None
    for i in range(travel):
        sim.to(sim_time(i, beats))
        holder = route[min(i // each, len(route) - 1)][0]
        stage.film("caption", pair[0], _HOLDING.get(holder, "Waiting in a street light"))
        if i < 12:
            stage.film("hexToSlots", ease((i + 1) / 12))
        elif i == 12:
            stage.film("hexDone")
        at_clinic = math.dist(*sim.on_loop(partial(_positions, sim, holder, ORIGIN)))
        if at_clinic < CLUTTER_M:  # several holders on one spot: their labels would pile up
            page.evaluate("window.netmap.select(null)")
        else:
            page.evaluate(_SELECT, bid.hex())
        stage.caption_out(i, travel)
        stage.snap(View(holder, 14.5, k=1.0 if i == 0 else 0.16))

    beats = [(0, cast.attack_t), (frames_of["attack"], cast.attack_end_t)]
    stage.say("attack", cast.forged)
    stage.film("slots", None)
    for i in range(frames_of["attack"]):
        sim.to(sim_time(i, beats))
        stage.caption_out(i, frames_of["attack"])
        stage.snap(View("FORGER", 14.5))

    page.evaluate("window.netmap.select(null)")
    stage.film("drawer", "right", 0)
    page.locator(".open.right").click()
    right.locator("input[type=password]").fill(DEMO_PIN)
    _submit_pin(right)
    beside_hospital = View(DEST, 14.0, right=DRAWER_PX)
    arrived = [h for h in cast.hops if h.t <= cast.opened_t + 1]
    if len(lit_slots(arrived, DEST, 1.0)) < 4:
        raise RuntimeError("hospital shows fewer than 4 pieces at the moment it opens")
    stage.say("arrive")
    stage.film("slots", [])
    for i in range(20):
        stage.film("drawer", "right", ease((i + 1) / 8))
        sim.to(sim_time(i, [(0, cast.attack_end_t), (19, cast.first_into(DEST) - 1)]))
        stage.snap(beside_hospital)
    for i in range(25):
        stage.film("slots", lit_slots(arrived, DEST, (i + 1) / 25))
        stage.snap(beside_hospital)
    sim.to(cast.opened_t + 1)
    right.locator(".msg", has_text="34 weeks").wait_for()
    tail = frames_of["arrive"] - 45
    for i in range(tail):  # the hospital reads it, then the panel slides away
        leaving = i >= tail - 6
        stage.film("drawer", "right", 1 - ease((i - (tail - 6) + 1) / 6))
        stage.caption_out(i, tail)
        stage.snap(View(DEST, 14.0, right=0 if leaving else DRAWER_PX))
    stage.film("drawer", "right", 1)
    right.locator(".close").click()
    stage.film("clear")

    # The journey view dissolves in over the map.
    sim.to(cast.acked_t + 1)
    page.evaluate("document.getElementById('journey').style.opacity = 0")
    page.locator(".open.left").click()
    if left.locator("input[type=password]").is_visible():  # long enough away to have locked
        left.locator("input[type=password]").fill(DEMO_PIN)
        _submit_pin(left)
    left.locator(".msg.out", has_text="34 weeks").locator(".follow").click()
    page.locator(".timeline li", has_text="Delivery confirmation").wait_for()
    stage.say("journey", right=True)
    for i in range(frames_of["journey"]):  # hold on the route, then down to the confirmation
        page.evaluate(
            """([o, s]) => { const j = document.getElementById('journey');
              j.style.opacity = o; j.scrollTop = (j.scrollHeight - j.clientHeight) * s; }""",
            [ease((i + 1) / 9), ease((i - 12) / 18)],
        )
        stage.caption_out(i, frames_of["journey"])
        stage.snap()

    # The card dissolves in, then its words fade so the loop restarts from blank paper.
    card = frames_of["card"]
    for i in range(card):
        stage.film("card", ease((i + 1) / 10), 1 - ease((i - (card - 9)) / 8))
        stage.snap()
    return bid


_TO_WEBP = """async ([png, quality]) => {
  const img = new Image();
  img.src = `data:image/png;base64,${png}`;
  await img.decode();
  const canvas = document.createElement("canvas");
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  canvas.getContext("2d", { alpha: false }).drawImage(img, 0, 0);
  const blob = await new Promise(done => canvas.toBlob(done, "image/webp", quality));
  let text = "";
  for (const byte of new Uint8Array(await blob.arrayBuffer())) text += String.fromCharCode(byte);
  return btoa(text);
}"""


def _chunk(name: bytes, payload: bytes) -> bytes:
    return name + struct.pack("<I", len(payload)) + payload + b"\0" * (len(payload) % 2)


def _u24(value: int) -> bytes:
    return value.to_bytes(3, "little")


def _image_chunks(still: bytes) -> bytes:
    """A still WebP's picture data: animation frames may not carry headers or colour profiles."""
    out, i = b"", 12
    while i < len(still):
        size = struct.unpack("<I", still[i + 4 : i + 8])[0]
        end = i + 8 + size + size % 2
        if still[i : i + 4] in {b"VP8 ", b"VP8L", b"ALPH"}:
            out += still[i:end]
        i = end
    return out


def animated_webp(stills: list[bytes], width: int, height: int, fps: int) -> bytes:
    """Pack still WebP files into one looping animation. Browsers play it like a GIF."""
    canvas = bytes([0x02, 0, 0, 0]) + _u24(width - 1) + _u24(height - 1)  # 0x02: animated
    body = b"WEBP" + _chunk(b"VP8X", canvas) + _chunk(b"ANIM", bytes(4) + struct.pack("<H", 0))
    place = bytes(6) + _u24(width - 1) + _u24(height - 1) + _u24(round(1000 / fps)) + b"\x02"
    for still in stills:
        body += _chunk(b"ANMF", place + _image_chunks(still))
    return b"RIFF" + struct.pack("<I", len(body)) + body


def encode() -> None:
    """The README film from the frames on disk, in full colour, using Chromium's encoder."""
    frames = ROOT / "out/film/frames"
    names = sorted(p.name for p in frames.glob("[0-9]*.png"))
    if len(names) != sum(s.frames for s in SHOTS):
        raise RuntimeError(f"expected a full set of frames, found {len(names)}")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        stills = {
            name: base64.b64decode(
                page.evaluate(
                    _TO_WEBP,
                    [base64.b64encode((frames / name).read_bytes()).decode(), WEBP_QUALITY],
                )
            )
            for name in names
        }
        browser.close()
    out = OUT / "hero.webp"
    out.write_bytes(animated_webp([stills[n] for n in names], *FRAME_SIZE, FPS))
    print(f"{out.relative_to(ROOT)}: {out.stat().st_size / 1e6:.1f} MB")


def film() -> None:
    work = Path(tempfile.mkdtemp())
    scenario = write_film_scenario(work)
    cast = rehearse(scenario)
    print(f"cast: {' > '.join(cast.chain)}; {cast.forged} forged frames refused", flush=True)
    sim = FilmSim(build(load_scenario(str(scenario)), SEED, Path(tempfile.mkdtemp())))
    sim.r.world.scheduler.run_until(SEND_AT_S)

    out = ROOT / "out/film"
    frames = out / "frames"
    shutil.rmtree(frames, ignore_errors=True)
    frames.mkdir(parents=True)
    overlay = (Path(__file__).parent / "film_overlay.html").read_text(encoding="utf-8")
    with serve(sim) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            viewport={"width": FRAME_SIZE[0], "height": FRAME_SIZE[1]},
            color_scheme="light",
            reduced_motion="reduce",
        )
        page = context.new_page()
        _open_page(page, url)
        page.evaluate("t => window.render(t)", sim.on_loop(sim.telemetry))
        page.evaluate(_SETTLE)
        page.evaluate(
            "h => document.body.append(document.createRange().createContextualFragment(h))", overlay
        )
        bid = _shoot(Stage(page, sim, frames), cast)
        browser.close()

        hops = hops_for(sim.on_loop(lambda: list(sim.r.world.receipts)), bid)
        opened = sim.on_loop(lambda: _opened_t(sim.r, bid))
        acked = sim.on_loop(lambda: _acked_t(sim.r, bid))
    chain = first_chain(hops, ORIGIN, DEST)
    verify_cast(chain, cast.forged, str(opened) if opened else None, str(acked) if acked else None)
    if chain != cast.chain:
        raise RuntimeError(f"film took {chain}, rehearsal took {cast.chain}")
    encode()
