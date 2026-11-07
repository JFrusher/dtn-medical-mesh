# Architecture

## Layers

```
interface/   FastAPI app + chat UI + map ──── Backend protocol ────┐
                                                                    │
endpoint/    crypto · padding · sharding · FHIR-lite · status       │  laptop only
─────────────────────────────────────────────────────────────────── │ ─────────────
node/        NodeCore (sans-IO) · bundle · pki · routing · clock    │  every node
node/hw/     SecureElement · Rtc · PowerSource · Radio · Entropy    │
                                                                    │
simulation/  scheduler · world · radio model · power · faults ──────┘  emulator
             · hostile nodes · scenarios · metrics · live backend
```

## Rules the code enforces

| Rule | Enforced by |
|---|---|
| `node/` never imports `endpoint/` or `interface/`: no Pi can decrypt | `validation/test_boundaries.py` |
| Production code never imports `simulation/` (seeded randomness) | same |
| The core does no I/O: `handle(event) -> list[Action]` | Design; drivers apply actions |
| Drivers apply actions strictly in order and stop on failure | `node/events.py` contract; the world driver does so |
| Each Store/Delete is one transaction | `node/storage.py` |

## Drivers of `NodeCore`

| Driver | Status | Clock | Radios | Storage key |
|---|---|---|---|---|
| `simulation/world.py` | Done | Discrete-event virtual time | Modelled (range, setup, throughput, loss, LoRa airtime) | Software secure element |
| `node/daemon.py` | Hardware phase H1 | Monotonic + DS3231 | BlueZ BLE, Wi-Fi, SX1262 | ATECC608B slot |

## Emulator fidelity: what it does not prove

- Simulated power cuts drop RAM and reopen storage; real mid-write crashes are covered only by the SIGKILL test (`validation/crash/`). True power-loss durability (fsync on the actual SD card) needs the H1 bench.
- Radio numbers (2 s link setup, 1.5 MB/s, 100 m Wi-Fi, 2 km LoRa) are assumptions until measured.
- The software secure element cannot show physical key protection; `SoftwareSecureElement` only matches the interface.
- No basemap: positions are a schematic plane in metres.
