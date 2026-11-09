# Journey Film GIF Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render a 31-second GIF, in two cuts, that follows one real referral from Murieti through a vehicle relay and a forging attack to the hospital and back.

**Architecture:** A derived Arusha scenario adds a forger and radio loss. A scout finds a seed and send time that produce the relay. The film job then steps that same run frame by frame, drives the real app in Playwright, screenshots each frame, and encodes with gifski.

**Tech Stack:** Python 3.11, Playwright 1.63 (already a dev dependency), PyYAML, gifski 1.14 (`winget install ImageOptim.gifski`).

**Spec:** `docs/superpowers/specs/2026-11-09-journey-film-gif-design.md`

## Global Constraints

- Work on a new branch `feat/journey-film` from `main`. Commit only when Jacob asks.
- Frames: 1280x720, `color_scheme="light"`, `reduced_motion="reduce"`, 15 fps. GIF: 960 px wide, at most 15 MB.
- Warm grade: `body > :not(#film) { filter: sepia(.22) saturate(.92) brightness(1.02); }`. `#film` overlay is ungraded.
- Colours: teal `#0f6e74`, alarm `#b4232c`, ink `#1f2d2b`, panel `#fbf8f1`, muted `#6b7a77`. Font stack copied from `interface/static/style.css`.
- Panel: lower left, solid, 1 px ink border, square corners, no shadow.
- Spec ban list applies to every shot and string: gradients, glow, particles, grain, scramble, shatter, rounded floating tiles, glass, spaced mono caps, two-beat taglines, "not X but Y", groups of three, em dashes, uplifting closers, invented numbers.
- No changes to `node/`, `endpoint/`, `interface/`, or the five published scenarios.
- All backend access from the film runs on the server's event loop (`interface/app.py:60` relies on this).
- Radio settings copied from `simulation/scenarios/stress.yaml`, without `bit_flip_per_frame`, so `bad_signature` and `corrupt` drops come from the forger.

## Review Focus

- A receipt can carry 12 pieces at once: the counter must light slots one at a time, and the referral must not open before 4 are lit.
- Pieces from other messages, or the same piece received twice, must not light a slot.
- If a later code change alters the run, the film must stop with an error, since it would otherwise show a story the run no longer tells.
- An attack shot with zero refused frames must stop the render, with no placeholder number.
- A caption edit that breaks the ban list must fail a test.

---

### Task 1: Derived scenario and scout

**Files:**
- Create: `tools/film.py`
- Test: `validation/test_film.py`

**Interfaces:**
- Produces, in `tools/film.py`:
  - `REFERRAL = "Referral: 28F, 34 weeks, BP 160/110, headache"`
  - `@dataclass(frozen=True) class Hop: t: float; sender: str; receiver: str; slots: frozenset[int]`
  - `write_film_scenario(directory: Path) -> Path`
  - `hops_for(receipts: Iterable[tuple[float, str, Receipt]], bundle_id: bytes) -> list[Hop]` (slot = last byte of each shard id)
  - `first_chain(hops: list[Hop], origin: str, dest: str) -> list[str]`
  - `is_relay(chain: list[str]) -> bool`
  - `forged_count(world: World) -> int` (sum of `dropped["bad_signature"] + dropped["corrupt"]` across honest, up nodes)
  - Constants `SEED: int`, `SEND_AT_S: float`, `ATTACK_AT_S: float`, set in step 6.

- [ ] **Step 1: Write the failing tests**

```python
def hop(t, a, b, *slots): return Hop(t, a, b, frozenset(slots))

def test_first_chain_walks_back_from_earliest_arrival():
    hops = [hop(50, "VAN_EAST", "HOSPITAL", 1), hop(30, "AMBULANCE", "HOSPITAL", 1, 2),
            hop(20, "VAN_WEST", "AMBULANCE", 1), hop(10, "OUTREACH_BIKE", "VAN_WEST", 1),
            hop(5, "MURIETI", "OUTREACH_BIKE", 1)]
    assert first_chain(hops, "MURIETI", "HOSPITAL") == [
        "MURIETI", "OUTREACH_BIKE", "VAN_WEST", "AMBULANCE", "HOSPITAL"]

def test_is_relay():
    assert is_relay(["MURIETI", "OUTREACH_BIKE", "VAN_EAST", "AMBULANCE", "HOSPITAL"])
    assert not is_relay(["MURIETI", "AMBULANCE", "HOSPITAL"])

def test_film_scenario_adds_forger_and_loss_and_keeps_arusha(tmp_path):
    s = load_scenario(str(write_film_scenario(tmp_path)))
    forger = next(n for n in s.nodes if n.config.node_id == "FORGER")
    assert forger.attacks == frozenset({"forge_shards"})
    assert s.radio.loss_in_bad == 0.5
    assert s.radio.bit_flip_per_frame == 0
    assert s.geo == load_scenario("arusha").geo
```

- [ ] **Step 2: Run:** `python -m pytest validation/test_film.py -v`. Expected: FAIL, cannot import `tools.film`.

- [ ] **Step 3: Implement the functions above.** `write_film_scenario` loads `arusha.yaml` with `yaml.safe_load`, merges stress radio settings into `radio`, appends a `FORGER` mule that reuses `VAN_WEST`'s route with `start_s` offset by 300, and writes `film.yaml` to `directory`. `first_chain` picks the earliest hop into `dest`, then repeats from its sender until reaching `origin`.

- [ ] **Step 4: Run the tests.** Expected: PASS.

- [ ] **Step 5: Add `scout() -> None`, run with `python -m tools.film scout`.** For seeds 1 to 20, build the scenario and call `LiveSim.send` for `REFERRAL` (CRITICAL, murieti to hospital) every 600 s from 3600 to 72000. Step in 30 s increments, recording `(t, forged_count)`. For each message print those with `is_relay`, and where `forged_count` rises between the hop into `AMBULANCE` and the first hop into `HOSPITAL`. Then replay each hit with only its one film message, and keep those that still satisfy both conditions.

- [ ] **Step 6: Record the result.** Set `SEED`, `SEND_AT_S`, `ATTACK_AT_S`. If there is no hit, first try changing the forger's route to `AMBULANCE`'s. If there is still none, stop and ask Jacob.

- [ ] **Step 7: Add `verify_cast(chain, forged_delta, opened, acked) -> None`,** raising `RuntimeError` unless `is_relay(chain)`, `forged_delta > 0`, and both `opened` and `acked` are truthy. Add test `test_verify_cast_rejects_changed_run`, with `pytest.raises(RuntimeError)` on a two-node chain and on `forged_delta=0`.

### Task 2: Shot list, copy and counter

**Files:**
- Modify: `tools/film.py`
- Test: `validation/test_film.py`

**Interfaces:**
- Consumes: `Hop` from Task 1.
- Produces:
  - `SHOTS: tuple[Shot, ...]`, where `@dataclass(frozen=True) class Shot: name: str; frames: int`. In order: `opening 45, send 75, bytes 45, travel 105, attack 45, arrive 60, journey 45, card 45`.
  - `caption(shot: str, forged: int | None = None) -> tuple[str, str] | None` returning `(headline, note)`.
  - `lit_slots(hops: list[Hop], dest: str, progress: float) -> list[int]`, with `progress` in 0 to 1.
  - `sim_time(frame: int, beats: list[tuple[int, float]]) -> float` (piecewise linear between `(frame, sim_t)` pairs).

- [ ] **Step 1: Write the failing tests**

```python
def test_runtime_is_31_seconds():
    assert sum(s.frames for s in SHOTS) == 465

def test_copy_obeys_ban_list():
    for shot in SHOTS:
        pair = caption(shot.name, forged=7)
        for line in pair or ():
            assert "\u2014" not in line
            assert not re.search(r"\bnot\b.*\bbut\b", line, re.I)
            assert line.count(".") <= 1
            assert line != line.upper()

def test_attack_caption_needs_a_real_count():
    assert "7" in caption("attack", forged=7)[0]
    with pytest.raises(ValueError):
        caption("attack", forged=0)

def test_slots_light_one_at_a_time_in_arrival_order():
    hops = [Hop(9, "AMBULANCE", "HOSPITAL", frozenset({9, 2, 5, 11}))]
    assert lit_slots(hops, "HOSPITAL", 0.0) == []
    assert lit_slots(hops, "HOSPITAL", 0.5) == [2, 5]
    assert lit_slots(hops, "HOSPITAL", 1.0) == [2, 5, 9, 11]

def test_slots_ignore_duplicates_and_other_receivers():
    hops = [Hop(1, "A", "HOSPITAL", frozenset({3})), Hop(2, "B", "HOSPITAL", frozenset({3})),
            Hop(3, "A", "VAN_WEST", frozenset({7}))]
    assert lit_slots(hops, "HOSPITAL", 1.0) == [3]

def test_sim_time_interpolates():
    assert sim_time(5, [(0, 100.0), (10, 200.0)]) == 150.0
```

- [ ] **Step 2: Run the tests.** Expected: FAIL on missing names.

- [ ] **Step 3: Implement.** Copy, exact:

| Shot | Headline | Note |
|---|---|---|
| opening | A clinic outside Arusha has no signal. | none |
| bytes | Sealed for the hospital and cut into 12 pieces. | Ed25519, X25519, XChaCha20-Poly1305 |
| travel | Vehicles already on the road carry the pieces without being able to read them. | none |
| attack | Honest nodes refused {n} forged frames from a device posing as a carrier. | Header signatures and Merkle proofs |
| arrive | The hospital needs any 4 of the 12 to open it. | Reed-Solomon erasure coding |
| journey | A signed confirmation comes back to the clinic. | none |

`send` and `card` return `None`. `lit_slots` returns the first `round(progress * n)` distinct slots, in sorted order within each hop and hop order across hops, for hops into `dest`.

- [ ] **Step 4: Run the tests.** Expected: PASS.

### Task 3: Capture and encode

**Files:**
- Create: `tools/film_overlay.html`
- Modify: `tools/film.py`, `tools/record_gifs.py:145-156`
- Verify by viewing frames.

**Interfaces:**
- Consumes: everything in Tasks 1 and 2, `serve` from `tools/record_gifs.py`.
- Produces: `film() -> None`, writing `out/film/map-open.gif` and `out/film/drawing-open.gif`.

- [ ] **Step 1: Install gifski, after Jacob's approval:** `winget install ImageOptim.gifski`. Check: `gifski --version`. The film job does not use ffmpeg.

- [ ] **Step 2: Create `tools/film_overlay.html`.** One `#film` root with: caption panel, 12-slot counter (`.in` teal filled, outline otherwise), full-screen hex layout, end card, desk drawing. Exposes `window.film.caption(h, n)`, `.slots(lit)`, `.hex(rows)`, `.card()`, `.drawing(on)`, `.clear()`. Copy the desk `<svg>` and `#pen` filter from the approved mockup (option B). Card text, exact: "Satchel", "Jacob Frusher", "jacob@frusher.co.uk", "github.com/JFrusher/dtn-medical-mesh", and the small note "Emulated, not deployed. Not for emergencies."

- [ ] **Step 3: Add `class FilmSim(LiveSim)`.** `advance` stores `asyncio.get_running_loop()` on first call and otherwise does nothing. `on_loop(fn: Callable[[], T]) -> T` runs `fn` on that loop and blocks for the result (`loop.call_soon_threadsafe` plus `concurrent.futures.Future`). All film stepping and reads go through `on_loop`.

- [ ] **Step 4: Add browser setup.** Context per Global Constraints. Init script replaces `WebSocket` with an inert stub, so `window.render` is the only draw path. `page.route` on the root path rewrites `window.render = render;` to `window.render = render; window.netmap = map;`, which leaves `demo.html` unchanged on disk.

- [ ] **Step 5: Add one function per shot.** Each takes the shot's frame count and calls `snap()`, which draws telemetry through `window.render`, waits for the MapLibre `idle` event when the camera moved, and writes `NNNNN.png`:
  - `opening`: map at Murieti, zoom held. In the drawing cut, render with `film.drawing(true)` to a separate frame folder.
  - `send`: open left drawer (real app split), `textarea.fill` a prefix per frame, one typo corrected, one 8-frame pause, click `button[data-p=CRITICAL]`, click `.send`.
  - `bytes`: read shard `data` from `MURIETI`'s `core.shards` for this `bundle_id`, by `index`. `film.hex` shows the first 24 bytes of each of 12 rows, numbered. Hard cut in and out.
  - `travel`: `window.netmap.select(journey)`. Beats from the real hops. `jumpTo` follows whichever node holds the lead hop.
  - `attack`: step through `ATTACK_AT_S`, `jumpTo` FORGER, caption uses the real `forged_count` delta for the window around the refusal.
  - `arrive`: open right drawer, `film.slots(lit_slots(...))` with progress over the shot. Step past the real opened time only after 4 slots are lit, then the hospital chat shows the message.
  - `journey`: `.msg.out` Follow click equivalent, step to the real `acked` time.
  - `card`: `film.card()`.

- [ ] **Step 6: Add `film()`.** Calls `verify_cast` before encoding. Encodes each cut by running `gifski --fps 15 --width 960 -o <out> <names>` with `cwd` set to the frame folder (relative names stay under the Windows command length limit).

- [ ] **Step 7: Register the job.** Add `"film": lambda: __import__("tools.film").film.film()` to `jobs`. Move the ffmpeg check from `__main__` into `record()`.

- [ ] **Step 8: Render.** Run `python tools/record_gifs.py film`. Expected: two GIFs in `out/film/`, each at most 15 MB. If over, lower gifski `--quality` before lowering width.

- [ ] **Step 9: Check frames.** View the first, middle and last frame of each shot. Confirm captions fit, map tiles loaded, the "Not for emergencies" banner shows in app shots, and nothing on the ban list appears.

### Task 4: Choose the cut and update the README

**Files:**
- Modify: `tools/film.py`, `tools/record_gifs.py:102-104`, `README.md:15,40-57`
- Rename: `docs/media/hero.gif` to `docs/media/map.gif`
- Create: `docs/media/hero.gif` (the chosen cut)

- [ ] **Step 1: Send Jacob both GIFs and wait for his choice.**

- [ ] **Step 2: Set `OPENING = "map"` or `"drawing"` in `tools/film.py`.** After the review, `film()` writes only that cut, to `docs/media/hero.gif`.

- [ ] **Step 3: Rename the old job.** `hero()` becomes `map_tour()`, recording `map`, registered as `"map"`.

- [ ] **Step 4: Update README.** Rewrite the hero `alt` text to describe the film. Add a third "See it work" row with the district map GIF and its existing alt text.

- [ ] **Step 5: Run all checks.**

Run: `python -m ruff check . ; python -m mypy . ; python validation/trace_matrix.py`
Expected: clean, with every test passing.

- [ ] **Step 6: Hold for Jacob's instruction to commit.**
