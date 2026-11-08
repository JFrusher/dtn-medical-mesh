# dtn-medical-mesh — Implementation Plan

Companion to [`PROJECT_SPEC.md`](PROJECT_SPEC.md). Section references (§) point there.

## Status

**Phases 0–7 are implemented and merged.** **Phases 8–11 (spec v0.2) are planned:** pilot-grade emulation, pilot sizing, the staff app, and the admin console with node software. Hardware phases H1/H2 follow.

## How to use this plan

- Each step (`P<phase>.S<nn>`) is **one commit** that Claude Code (or a person) can finish in one go.
- Steps are in dependency order. Do not start a step until the steps it depends on are merged.
- Every step has a **Verify** command that must pass before committing. `uv run ruff check . && uv run mypy .` must also pass on every step (CI enforces it).
- Tests carry requirement tags (`@pytest.mark.req("REQ-…")`) so `validation/trace_matrix.py` can report coverage (§13).
- No physical hardware exists yet. Every hardware dependency sits behind an interface with a **virtual** implementation. Real drivers are added in the Hardware phases (H1/H2) without changing the core.

## Architecture decisions for the emulated build

| Decision | Choice | Why |
|---|---|---|
| Node logic | **Sans-IO core**: `NodeCore.handle(event) -> list[Action]`. No direct clock, radio, disk or randomness access. | The same code runs under the emulator and under the future Pi daemon, and is deterministic and unit-testable. |
| Emulator runtime | **Single process, discrete-event, virtual time** (heap-based scheduler) | Runs 72 h of scenario in seconds; seed-reproducible. |
| Real crash test | One subprocess `SIGKILL` test for `storage.py` | The only honest way to prove WAL durability. |
| Hardware stand-ins | `SecureElement`, `Rtc`, `PowerSource`, `Radio` interfaces with virtual implementations | Real drivers slot in later. |
| Storage at rest | **SQLCipher** (key from the secure element) on top of WAL, `synchronous=FULL` | Same code path on real Pis. |
| Radio model | Disc range per radio, link-setup delay, throughput cap, burst loss, bit flips; LoRa airtime from SF/BW/payload plus duty-cycle budget | Realistic enough to stress the scheduling and custody logic without path-loss tuning. |
| Payload AEAD | **XChaCha20-Poly1305** (PyNaCl/libsodium) | Random nonces are safe; fast in software on every Pi. |
| Encoding | CBOR (`cbor2`) | §6 |
| Erasure | `zfec` | §8, decision log |
| Randomness | Injected `Entropy`; seeded in the emulator, OS CSPRNG in production | Reproducible runs. A seeded RNG must never be reachable from production code (enforced by a test). |
| Tooling | uv, pytest (+ hypothesis), ruff, mypy `--strict`, GitHub Actions | |
| Frontend | Plain HTML/JS/CSS served by FastAPI; map drawn as SVG on a schematic plane; no build step, no CDN | Works offline. |
| Build order | **Walking skeleton first**, then deepen each layer | Something works end to end from Phase 1. |

### Package layout (refines §14)

```
node/
  core.py            # NodeCore: sans-IO state machine
  events.py          # Event and Action dataclasses
  bundle.py          # CBOR header/shard, Merkle
  erasure.py         # zfec wrapper
  storage.py         # SQLCipher/WAL store, eviction, ledger
  routing.py         # epidemic, PRoPHET
  summary.py         # Bloom + exact summaries
  clock.py           # HLC, Bundle Age
  pki.py             # certs, revocation, directory
  hw/                # interfaces + virtual implementations
    secure_element.py  rtc.py  power.py  radio.py  entropy.py
  daemon.py          # asyncio driver for real hardware (Phase H1)
endpoint/            # endpoint-only crypto + FHIR-lite (laptop side, §7.4)
interface/           # FastAPI app, PWA static files, map
simulation/
  scheduler.py  world.py  radio_model.py  faults.py  metrics.py  runner.py
  scenarios/*.yaml
validation/          # all tests + trace_matrix.py
docs/
```

`endpoint/` is separate from `node/` so that the rule "plaintext crypto never on a Pi" is structural: `node/` must never import `endpoint/`, and a test enforces it.

---

## Phase 0 — Foundations

| ID | Step | Files | Verify |
|---|---|---|---|
| P0.S01 | Scaffold the uv project: Python 3.11+, `pyproject.toml` with ruff, mypy strict, pytest (`testpaths=["validation"]`). Add a `LICENSE` file (Apache-2.0) and empty packages `node`, `endpoint`, `interface`, `simulation`. | `pyproject.toml`, `LICENSE`, `*/__init__.py` | `uv run ruff check . && uv run mypy .` (pytest has nothing to collect yet) |
| P0.S02 | Add dependencies: `cryptography`, `pynacl`, `cbor2`, `zfec`, `sqlcipher3` (binary wheel), `fastapi`, `uvicorn`, `pyyaml`, `hypothesis`. Add a smoke test that imports each one and opens an in-memory SQLCipher DB. *If no SQLCipher wheel exists for the platform, stop and report back; don't silently fall back.* | `pyproject.toml`, `uv.lock`, `validation/test_deps.py` | `uv run pytest validation/test_deps.py` |
| P0.S03 | GitHub Actions CI: uv sync, ruff, mypy, pytest. | `.github/workflows/ci.yml` | CI green on push |
| P0.S04 | `docs/requirements.md` with `REQ-*` IDs taken from the spec (security, durability, routing, time, UX, safety). Add a pytest marker `req` registered in `conftest.py`. | `docs/requirements.md`, `validation/conftest.py` | `uv run pytest -q` |
| P0.S05 | Import-boundary test: `node.*` never imports `endpoint.*` or `interface.*`; `node`, `endpoint` and `interface` never import `simulation` (where seeded entropy lives). | `validation/test_boundaries.py` | `uv run pytest validation/test_boundaries.py` |
| P0.S06 | Hardware interfaces as `Protocol`s: `SecureElement` (sign, public_key, read_slot), `Rtc` (read), `PowerSource` (state; power loss arrives as a `PowerLow` event), `Radio` (kind, max_frame_bytes, send; range/bandwidth live in the world model), `Entropy` (random_bytes). Add an `OsEntropy` implementation. | `node/hw/*.py` | `uv run mypy node` |
| P0.S07 | Events and actions: `ContactUp/Down`, `FrameReceived`, `Timer`, `PowerLow`, `Inject`; `SendFrame`, `Store`, `Delete`, `SetTimer`, `Deliver`, `Emit(telemetry)`. Frozen dataclasses; events carry `mono` and `wall` time; drivers apply actions in list order. | `node/events.py` | `uv run mypy node` |

## Phase 1 — Walking skeleton (one message end to end)

Goal: in the emulator, a clinic endpoint sends one text note and the hospital endpoint receives and decrypts it, carried by one mule. Every layer starts at its simplest (k=1, m=0, epidemic, plain SQLite, no PKI). Later phases deepen each one.

| ID | Step | Files | Verify |
|---|---|---|---|
| P1.S01 | Discrete-event scheduler: virtual `now`, `schedule(at, fn)`, `run_until(t)`, deterministic tie-breaking, seeded RNG. | `simulation/scheduler.py`, `validation/sim/test_scheduler.py` | `uv run pytest validation/sim/test_scheduler.py` |
| P1.S02 | Virtual hardware: `VirtualRtc` (reads scheduler time), `SoftwareSecureElement` (P-256 key generated in-process, no export method), `SeededEntropy`, `MainsPower`. | `simulation/virtual_hw.py`, tests | `uv run pytest validation/sim/test_virtual_hw.py` |
| P1.S03 | Minimal bundle: CBOR header (§6.1 subset: id, src_sa, dst_sa, priority, created, lifetime) + one payload blob; encode/decode round-trip. | `node/bundle.py`, tests | `uv run pytest validation/node/test_bundle.py` |
| P1.S04 | Endpoint crypto v0: X25519 + HKDF-SHA256 + XChaCha20-Poly1305 seal/open; Ed25519 inner signature. | `endpoint/crypto.py`, tests (`REQ-SEC-*`) | `uv run pytest validation/endpoint/test_crypto.py` |
| P1.S05 | Minimal storage: plain SQLite `bundles` table; put/get/list/delete. | `node/storage.py`, tests | `uv run pytest validation/node/test_storage.py` |
| P1.S06 | `NodeCore` v0: on `ContactUp`, offer every held bundle the peer lacks (exact ID list); on receive, store; at destination SA, emit `Deliver`. | `node/core.py`, tests | `uv run pytest validation/node/test_core_v0.py` |
| P1.S07 | World and radio v0: nodes with positions; route-following mobility (waypoints and speed); contact when within Wi-Fi range; perfect link. | `simulation/world.py`, `simulation/radio_model.py`, tests | `uv run pytest validation/sim/test_world.py` |
| P1.S08 | Virtual endpoints in the sim: clinic endpoint encrypts → injects into its site node; hospital endpoint receives `Deliver` → decrypts. | `simulation/endpoints.py` | — |
| P1.S09 | Scenario loader (YAML) and `skeleton.yaml` (clinic, mule, hospital); `python -m simulation.runner <scenario> --seed N` prints an event log. | `simulation/runner.py`, `simulation/scenarios/skeleton.yaml` | `uv run python -m simulation.runner skeleton --seed 1` exits 0 and logs `DELIVERED` |
| P1.S10 | End-to-end test of the skeleton, asserting decrypted plaintext equals the original. | `validation/e2e/test_skeleton.py` | `uv run pytest validation/e2e` |
| P1.S11 | Delivery ACK v0: destination emits an ACK; it spreads back; every node purges; origin marks the bundle delivered. | `node/core.py`, tests | `uv run pytest validation/e2e` |

## Phase 2 — Integrity and durability

| ID | Step | Files | Verify |
|---|---|---|---|
| P2.S01 | Full §6.1 header (size bucket, k, m, merkle_root, injector_cert_id placeholder, age). Bump format version. | `node/bundle.py` | `uv run pytest validation/node/test_bundle.py` |
| P2.S02 | Merkle tree over shard hashes; proofs; verify one shard alone. | `node/bundle.py`, tests (hypothesis) | `uv run pytest validation/node/test_merkle.py` |
| P2.S03 | Padding to size buckets (§6.1). | `endpoint/padding.py`, tests | `uv run pytest validation/endpoint/test_padding.py` |
| P2.S04 | `erasure.py` zfec wrapper; property test that decoding works from **every** k-subset of k+m. | `node/erasure.py`, tests | `uv run pytest validation/node/test_erasure.py` |
| P2.S05 | Priority classes table (§6.3) as config, with defaults. | `node/config.py`, tests | `uv run pytest validation/node/test_config.py` |
| P2.S06 | Endpoint pipeline: sign → seal → pad → shard → header. Core now stores and moves **shards**; the destination rebuilds once it holds k. | `endpoint/pipeline.py`, `node/core.py` | `uv run pytest validation/e2e` |
| P2.S07 | Storage v1: WAL, `synchronous=FULL`, schema from §9.2 (`shards`, `bundles`, `acks`, …), one transaction per received shard. | `node/storage.py`, tests | `uv run pytest validation/node/test_storage.py` |
| P2.S08 | **Crash test**: a child process writes shards in a loop and is `SIGKILL`ed at random times (≥ 500 iterations). After each kill, the DB opens, passes `PRAGMA integrity_check`, and every committed shard verifies. | `validation/crash/test_sigkill.py` | `uv run pytest validation/crash -q` |
| P2.S09 | SQLCipher: DB key read from `SecureElement.read_slot`; test that the raw file contains no shard bytes. | `node/storage.py`, tests | `uv run pytest validation/node/test_storage_encrypted.py` |
| P2.S10 | Eviction and refusal (§6.3, §9.2): expired → telemetry → routine (oldest first) → urgent; `CRITICAL` is never evicted for lower classes; refuse when full. | `node/storage.py`, tests | `uv run pytest validation/node/test_eviction.py` |
| P2.S11 | Corrupt-shard rejection in core (Merkle failure → drop and count). | `node/core.py`, tests | `uv run pytest validation/node/test_core_integrity.py` |

## Phase 3 — Security and identity

| ID | Step | Files | Verify |
|---|---|---|---|
| P3.S01 | Certificates: offline root → hub certs → node certs; CBOR, P-256, 14-day validity, revocation by subject. (A real CA command-line tool moves to H1; the emulator provisions with the same `node/pki.py` functions.) | `node/pki.py`, `node/crypto.py`, tests | `uv run pytest validation/node/test_pki.py` |
| P3.S02 | Provisioning in the sim: each virtual node gets its SE key and certificate at scenario start. | `simulation/provision.py` | `uv run pytest validation/e2e` |
| P3.S03 | Outer header signature by the injecting site node's SE; relays drop shards with a missing, invalid or expired signature. | `node/core.py`, tests | `uv run pytest validation/node/test_outer_sig.py` |
| P3.S04 | Peer authentication on `ContactUp` (certificate exchange; check validity and revocation). | `node/core.py`, tests | same |
| P3.S04b | Signed delivery ACKs: signed by a site/hub of the destination SA named in the ACK; forged ACKs purge nothing and cannot block the real bundle. (Added: an unsigned ACK let any node wipe clinical data.) | `node/bundle.py`, `node/core.py`, tests | `uv run pytest validation/node/test_ack.py` |
| P3.S05 | Revocation lists: hub-signed, top priority, spread over the mesh; reject the revoked node's shards and receipts. | `node/pki.py`, `node/core.py`, tests | `uv run pytest validation/node/test_revocation.py` |
| P3.S06 | Short-lived certificates (14 days) renewed only when docked at a hub; expiry is checked against the HLC (stub until P4.S06). | `node/pki.py`, tests | `uv run pytest validation/node/test_cert_renewal.py` |
| P3.S07 | Endpoint directory: hub-signed endpoint public keys and service areas, spread over the mesh. | `node/pki.py`, `endpoint/directory.py`, tests | `uv run pytest validation/endpoint/test_directory.py` |
| P3.S08 | Recipient privacy by trial decryption (§7.5): headers carry no endpoint identity; delivery to every site node in the SA; only the intended endpoint can open it, and the rest ignore it silently. | `simulation/endpoints.py`, tests | `uv run pytest validation/e2e/test_privacy.py` |
| P3.S09 | Bidirectional: hospital replies to the clinic (`Communication.inResponseTo`). | `simulation/endpoints.py`, e2e test | `uv run pytest validation/e2e` |
| P3.S10 | Per-injector rate limits (anti-flood). | `node/core.py`, tests | `uv run pytest validation/node/test_rate_limit.py` |

## Phase 4 — Protocol depth

| ID | Step | Files | Verify |
|---|---|---|---|
| P4.S01 | Bloom filter summary (≈1% false-positive sizing) sent on `ContactUp`. | `node/summary.py`, tests | `uv run pytest validation/node/test_summary.py` |
| P4.S02 | Exact-list follow-up while the link is still up; a test where a Bloom false positive delays a shard but still delivers it. | `node/summary.py`, `node/core.py`, tests | same |
| P4.S03 | Transfer scheduling order (§8.4). | `node/core.py`, tests | `uv run pytest validation/node/test_scheduling.py` |
| P4.S04 | Custody receipts: batched, SE-signed, hash-chained ledger; returned on the same link or later. | `node/core.py`, `node/storage.py`, tests | `uv run pytest validation/node/test_custody.py` |
| P4.S05 | Deletion rules (§8.5): origin deletes only on delivery ACK; relays delete on receipt from a better peer, on ACK, or on TTL. | `node/core.py`, tests | same |
| P4.S06 | HLC (§8.9): merge on every frame, monotonic, survives RTC reset. | `node/clock.py`, tests (hypothesis) | `uv run pytest validation/node/test_hlc.py` |
| P4.S07 | Bundle Age: dwell time added up on monotonic time; TTL = stricter of age and HLC. | `node/clock.py`, `node/core.py`, tests | `uv run pytest validation/node/test_ttl.py` |
| P4.S08 | Expiry alert: origin endpoint state goes to `NOT_DELIVERED` and is never auto-resent. | `endpoint/state.py`, tests | `uv run pytest validation/endpoint/test_expiry.py` |
| P4.S09 | Epidemic routing as an explicit strategy behind the `Router` interface. | `node/routing.py`, tests | `uv run pytest validation/node/test_routing.py` |
| P4.S10 | PRoPHET (RFC 6693 equations: encounter, ageing, transitivity) keyed on SA. | `node/routing.py`, tests | same |
| P4.S11 | Warm-up switch from epidemic to PRoPHET per SA after N encounters. | `node/routing.py`, tests | same |
| ~~P4.S12~~ | **Dropped:** a per-peer shard cap would stop a single mule completing a delivery (mules copy, not move, shards). See spec §8.6 and §17. | — | — |
| P4.S13 | LoRa control channel: frame ≤ 200 B, carries ACKs, receipts, revocations and PRoPHET summaries only; a test that shard frames are rejected on LoRa. | `node/core.py`, tests | `uv run pytest validation/node/test_lora.py` |

## Phase 5 — Emulated real-world pressures

| ID | Step | Files | Verify |
|---|---|---|---|
| P5.S01 | Radio model v1: link-setup delay, throughput cap, contact window from relative speed; transfers cut mid-frame on contact loss. | `simulation/radio_model.py`, tests | `uv run pytest validation/sim/test_radio.py` |
| P5.S02 | Loss model: burst loss (Gilbert-Elliott) and bit flips per link. | `simulation/radio_model.py`, tests | same |
| P5.S03 | LoRa model: airtime from SF/BW/CR/payload, duty-cycle budget per region config, km range. | `simulation/radio_model.py`, tests | same |
| P5.S04 | Virtual RTC faults: drift, reset to epoch, failure. | `simulation/virtual_hw.py`, tests | `uv run pytest validation/sim/test_rtc_faults.py` |
| P5.S05 | Power model: battery capacity, solar input curve, vehicle ignition; UPS low-power signal and hold-up time; hard cut if hold-up is exceeded. | `simulation/virtual_hw.py`, tests | `uv run pytest validation/sim/test_power.py` |
| P5.S06 | Simulated power cut: drop the node's in-memory core, reopen its storage file, rebuild the core from disk. | `simulation/faults.py`, tests | `uv run pytest validation/sim/test_power_cut.py` |
| P5.S07 | Hostile nodes: unregistered injector, revoked node, forged receipts, corrupt shards, replays. | `simulation/faults.py`, tests | `uv run pytest validation/sim/test_hostile.py` |
| P5.S08 | **Invariant checker**: at the end of a run, every injected bundle is `DELIVERED`, `NOT_DELIVERED` (flagged at origin) or still in flight within TTL. Anything else fails as a silent loss. Also checks custody-ledger hash chains. | `simulation/metrics.py`, tests | `uv run pytest validation/sim/test_invariants.py` |
| P5.S09 | Metrics report (§12.4) as JSON and Markdown. | `simulation/metrics.py` | runner writes `out/<scenario>-<seed>/report.md` |
| P5.S10 | Scenarios: `rural-clinics`, `disaster-response`, `island-medevac`, `stress` (§12.3), with workloads covering all payload classes. | `simulation/scenarios/*.yaml` | `uv run python -m simulation.runner stress --seed 1` exits 0 with zero silent losses |
| P5.S11 | CI matrix: `stress` with 5 fixed seeds. | `.github/workflows/ci.yml` | CI green |

## Phase 6 — Interface and live map

| ID | Step | Files | Verify |
|---|---|---|---|
| P6.S01 | FHIR-lite models (§6.4) with schema validation. | `endpoint/fhir.py`, tests | `uv run pytest validation/endpoint/test_fhir.py` |
| P6.S02 | Telemetry JSON schema (§10.3): nodes, links, shards in flight, events. | `interface/telemetry.py`, `docs/telemetry.schema.json`, tests | `uv run pytest validation/interface/test_telemetry.py` |
| P6.S03 | Sim server: `interface/app.py` is an app factory taking a backend; `simulation/live.py` plugs in the emulator (pause, 1×, 60×, 600×) and streams telemetry over WebSocket. `interface/` never imports `simulation/` (P0.S05). | `interface/app.py`, `simulation/live.py` | `uv run pytest validation/interface/test_ws.py` |
| P6.S04 | Endpoint API: send message, list conversations, message status, for a chosen virtual endpoint. | `interface/app.py`, tests | `uv run pytest validation/interface/test_api.py` |
| P6.S05 | PWA chat UI (plain JS): conversations, compose, attach, priority one-tap, status chips, expected-latency and "no guarantee of timing" banner, NOT DELIVERED alert. | `interface/static/*` | Playwright smoke test `validation/interface/test_ui.py` |
| P6.S06 | Vitals quick-entry form → FHIR `Observation`. | `interface/static/*` | same |
| P6.S07 | Map (SVG, no basemap): nodes by role and state, battery, queue, links, LoRa pings, shards moving; sim controls. | `interface/static/map.*` | same + screenshot artifact in CI |
| P6.S08 | Split demo view: clinic chat, map, hospital chat side by side. | `interface/static/demo.html` | same |
| P6.S09 | `README.md`: problem, intended use and exclusions, architecture, quickstart (`uv run dtn-demo rural-clinics`), screenshots, "not certified" notice. | `README.md` | links resolve |

## Phase 7 — Safety and governance documents

| ID | Step | Files | Verify |
|---|---|---|---|
| P7.S01 | `validation/trace_matrix.py`: REQ → tests → pass/fail; prints coverage, never "compliance". | `validation/trace_matrix.py` | `uv run python validation/trace_matrix.py` |
| P7.S02 | Hazard log (§13) linked to REQ IDs and tests. | `docs/hazard_log.md` | trace matrix lists hazard controls |
| P7.S03 | DPIA template and data-flow map. | `docs/dpia_template.md` | — |
| P7.S04 | SOUP list (third-party software, versions, purpose). | `docs/soup.md` | matches `uv.lock` (test) |
| P7.S05 | Architecture and protocol docs (detail of §6–§8). | `docs/architecture.md`, `docs/protocol.md` | — |

**Phase 1–7 exit:** every scenario × 5 seeds shows zero silent losses; the trace matrix shows every P-requirement covered; the demo runs offline from a clean clone with `uv run dtn-demo`.

---

## Spec v0.2 phases (emulated, no hardware needed)

Same rules as above: one commit per step, a verify command, requirement tags. New requirement IDs are added to `docs/requirements.md` in the step that first needs them.

## Phase 8 — Pilot-grade emulation (spec §12.2)

| ID | Step | Files | Verify |
|---|---|---|---|
| P8.S01 | `simulation/assumptions.yaml`: every physical and compute parameter with value, unit and **source** (published / estimate / measured); the scenario loader merges it; the run report lists assumptions used. | `simulation/assumptions.yaml`, `simulation/scenario.py`, `simulation/metrics.py`, tests | `uv run pytest validation/sim/test_assumptions.py` |
| P8.S02 | Path-loss link model: log-distance with log-normal shadowing per contact; received power decides whether a contact forms and its throughput (stepped Wi-Fi rates). Replaces the disc model; existing scenarios keep similar ranges by calibrating defaults. | `simulation/radio_model.py`, tests | `uv run pytest validation/sim/test_radio.py` |
| P8.S03 | BLE discovery before Wi-Fi: advertising interval and scan window give a random discovery delay; Wi-Fi setup starts after discovery. | `simulation/radio_model.py`, `simulation/world.py`, tests | same |
| P8.S04 | Shared airtime: contacts within the same neighbourhood split one channel's capacity. | `simulation/world.py`, tests | same |
| P8.S05 | Terrain data: `tools/build_arusha.py` fetches SRTM elevation (AWS open terrain tiles) for the bbox and extracts OSM building footprints from the map file into a coarse **clutter grid**; both stored in `simulation/geo/arusha/`. | `tools/build_arusha.py`, data files | files exist, sizes logged |
| P8.S06 | Terrain-aware loss: line-of-sight check with Fresnel clearance over the elevation profile, plus clutter loss from building density along the path; applies to Wi-Fi and LoRa in geo scenarios. | `simulation/terrain.py`, tests | `uv run pytest validation/sim/test_terrain.py` |
| P8.S07 | LoRa link budget per spreading factor on the same path model (replaces fixed range). | `simulation/radio_model.py`, tests | `uv run pytest validation/sim/test_radio.py` |
| P8.S08 | Compute cost model: per-operation times per node type (P-256 verify, Ed25519, XChaCha20 per MB, SHA-256 per MB, erasure decode per MB, SQLCipher write) from published Pi benchmarks, marked as assumptions; the world charges a node's busy time before its actions take effect. | `simulation/compute.py`, `simulation/world.py`, tests | `uv run pytest validation/sim/test_compute.py` |
| P8.S09 | Storage throughput and flash-wear counter per node; report peak write rate and projected card life. | `simulation/world.py`, `simulation/metrics.py`, tests | same |
| P8.S10 | Schedules with noise: departure jitter, traffic delay factor, skipped rounds, breakdowns (with repair time), weekly patterns; seeded. Route YAML gains a `schedule` block. | `simulation/mobility.py`, `simulation/scenario.py`, tests | `uv run pytest validation/sim/test_mobility.py` |
| P8.S11 | On-foot couriers: irregular walks between a set of places with random dwell. | `simulation/mobility.py`, tests | same |
| P8.S12 | Power per variant from spec §11.2 and monthly irradiance curve; battery days of autonomy in the report. | `simulation/virtual_hw.py`, tests | `uv run pytest validation/sim/test_power.py` |
| P8.S13 | Re-run all scenarios; update README results and record changes in the spec decision log. | README, spec | all scenarios: zero violations |

## Phase 9 — Pilot sizing (spec §12.3)

| ID | Step | Files | Verify |
|---|---|---|---|
| P9.S01 | Batch runner: run a scenario across N seeds in parallel processes; collect per-message results into CSV. | `simulation/batch.py`, tests | `uv run python -m simulation.batch arusha --seeds 5` |
| P9.S02 | Parameter sweeps: vary vehicle count, relay count and placement, parity, message volume via scenario overrides. | `simulation/batch.py`, tests | sweep of 2 values × 3 seeds runs |
| P9.S03 | Sizing report: delivery-time percentiles with confidence intervals, critical-within-N-hours, storage and battery, overhead; charts as SVG; Markdown summary with the assumptions table. | `simulation/report.py` | report renders for `arusha` |
| P9.S04 | Arusha sizing study committed to `docs/sizing/arusha.md` with a plain-language conclusion and its caveats. | docs | review |

## Phase 10 — Staff app (spec §10.1–10.5)

| ID | Step | Files | Verify |
|---|---|---|---|
| P10.S01 | Translation catalogue (ICU MessageFormat), language switcher, English strings extracted from the current UI; Swahili machine-drafted and **marked unreviewed** until a native clinician checks them. | `interface/static/i18n/*`, tests | UI test switches language |
| P10.S02 | Laptop PIN sign-in: endpoint keys encrypted at rest on the laptop, unlocked by PIN; auto-lock; author recorded in the signed payload. | static, endpoint, tests | UI test |
| P10.S03 | Patients list and patient-grouped messages (name, age, sex, local ID only). | static, `endpoint/fhir.py`, tests | UI test |
| P10.S04 | Structured referral form → FHIR `ServiceRequest`; photo attachments. | static, `endpoint/fhir.py`, tests | UI test |
| P10.S05 | Case status: Accept / Decline / Need information responses as tracked messages. | static, endpoint, tests | UI test end to end |
| P10.S06 | Hospital triage inbox sorted by urgency then age. | static, tests | UI test |
| P10.S07 | Results back to the clinic (FHIR `DiagnosticReport`). | static, endpoint, tests | UI test |
| P10.S08 | Accessibility pass (keyboard, touch targets, screen-reader labels), checked by screenshots and an automated accessibility scan. | static, tests | Playwright + axe scan clean |

## Phase 11 — Admin console and node software (spec §9.3–9.6, §10.6)

| ID | Step | Files | Verify |
|---|---|---|---|
| P11.S01 | Health telemetry bundles from every node (battery, temperature, storage, contacts, refusals, version). | `node/core.py`, tests | `uv run pytest validation/node/test_health.py` |
| P11.S02 | Admin console: inventory, last seen, battery, storage, refusals by reason, delivery times per clinic. | `interface/static/admin.*`, `interface/app.py`, tests | UI test |
| P11.S03 | Provision / revoke / publish directory from the console, signed by the operator's hub credential and logged. | interface, `node/pki.py`, tests | UI test + emulated revocation spreads |
| P11.S04 | Signed `node.toml` issued at provisioning; core refuses unsigned config. | `node/pki.py`, tests | unit tests |
| P11.S05 | Signed software updates: release key, `update` hub document, payload as a bundle, install conditions, staged rollout; emulated A/B with probation and rollback. | `node/`, `simulation/`, tests | `uv run pytest validation/sim/test_updates.py` |
| P11.S06 | Status light state machine in the core (healthy, carrying, transferring, amber, red) shown on the map. | `node/core.py`, map, tests | unit + UI test |
| P11.S07 | Site node as access point for the clinic laptop (spec §9.6): endpoint-key authentication on the API. | `interface/app.py`, tests | API tests |

**Phase 8–11 exit:** a sizing report for Arusha with sourced assumptions; a referral written on the clinic laptop in Swahili reaches the hospital, is accepted, and a result comes back, all in the emulator; the admin can revoke a node and push a signed update that rolls back safely when it fails.

---

## Hardware phases (future, require hardware)

Outline only. Each item replaces a virtual implementation behind an existing interface.

**H1 — Bench (2–6 nodes)**
- `node/daemon.py`: asyncio driver for `NodeCore` (real timers, sockets, systemd watchdog).
- Drivers: ATECC608B via `cryptoauthlib` (`SecureElement`), DS3231 (`Rtc`), UPS HAT GPIO (`PowerSource`), SX1262 (`Radio` LoRa), BlueZ BLE advertising/scan, Wi-Fi with static addressing.
- LUKS2 data partition; SD-card image build; `deploy/` systemd unit.
- Measurements that feed back into emulator defaults: link-up time, throughput per contact, hold-up time, enclosure temperature, LoRa range.

**H2 — Supervised field pilot (~10–30 nodes)**
- Partner governance (§13), parallel running with existing channels, pilot metrics against the hazard log.
