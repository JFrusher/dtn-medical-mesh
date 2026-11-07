# dtn-medical-mesh — Project Specification

| | |
|---|---|
| **Status** | Draft v0.1 — pre-implementation |
| **Licence** | Apache 2.0 |
| **Primary language** | Python 3.11+ (node, emulator, interface) |
| **Target** | Supervised real-world pilot (~10–30 nodes), reached via simulation → bench → field |
| **Regulatory posture** | Design-aligned with IEC 62304 / ISO 14971 / UK & EU GDPR Art. 9 / HIPAA §164.312. **Not certified. No compliance is claimed.** |

---

## 1. Summary

`dtn-medical-mesh` is an offline-first, **delay-tolerant network (DTN)** that moves encrypted clinical data between health sites that have no reliable internet. It uses the vehicles and people already travelling between those sites as couriers: ambulances, supply vans, outreach staff.

Small, ruggedised Raspberry Pi nodes are mounted wherever it helps: on a clinic table, on an ambulance's back shelf, on a street light. They find each other automatically, swap encrypted shards when they come within radio range, and carry them onward. A messaging-style web app on a laptop is the only thing staff ever touch. Clinic staff write a referral or attach a photo, and some time later the hospital receives it and can reply.

Relay nodes are **blind carriers**. They see the minimum routing metadata needed to move data and never hold keys that could decrypt clinical content.

## 2. Problem and intended use

### 2.1 Problem
Rural clinics, disaster zones and remote stations often have local power but no cellular or internet connection. Clinical data (referral notes, vitals, photos, EKG traces) currently waits on a local device until someone physically carries it, or until the patient themselves is moved. Meanwhile, vehicles travel between these sites every day. This project turns that existing physical movement into a secure data link that needs no clicks from the people travelling.

### 2.2 Intended use
Transport of **non-time-critical** clinical information between registered health endpoints (clinics, hospitals), where delivery taking minutes to days is clinically acceptable. Examples: referrals, handoff summaries, lab results, imaging for review, replies and advice from the hospital.

### 2.3 Explicit exclusions (must appear in the UI and README)
- **Not for emergency or time-critical communication.** Delivery time is unbounded, and the system never promises a time.
- Not a replacement for clinical judgement, existing referral pathways, or a medical record system.
- The UI must show the expected delivery latency and a **"no guarantee of timing"** indicator on every message.

## 3. Goals and non-goals

**Goals**
1. **No silent loss.** Every message is either acknowledged as delivered or visibly flagged at the origin as *not delivered*. Nothing disappears unnoticed.
2. Confidentiality and integrity of clinical content end-to-end, against compromised, stolen or malicious relay nodes.
3. Work within short, unreliable contact windows (a drive-by can be about 3–7 s) and survive abrupt power loss.
4. Zero extra workload for staff: no more effort than sending a WhatsApp message.
5. One codebase whose emulator runs the **real node software**, so what you see in a demo is how the deployed system behaves.

**Non-goals (v1)**
- Wire interoperability with other DTN stacks (BPv7/ION/dtn7). The format is *inspired by* RFC 9171 but is our own (§6).
- Bridges to third-party messengers (WhatsApp, Telegram, Matrix). Patient data never leaves the system.
- Map basemaps or tiles. The map is a schematic canvas (§10.3).
- GPS.
- Time-critical or emergency messaging (§2.3).

## 4. Glossary

| Term | Meaning |
|---|---|
| **Endpoint** | A laptop/tablet running the interface app. The only place clinical plaintext exists and the only holder of decryption keys. |
| **Site node** | Pi 4/5 attached to an endpoint at a clinic or hospital. It injects and receives bundles for that site. |
| **Hub** | A site node at a hospital that also acts as Certificate Authority (CA) and docking point. |
| **Mule** | A mobile node (ambulance, van, outreach worker) that carries shards between sites. |
| **Relay** | A fixed node (street light, roof) that bridges sites just out of each other's range. |
| **Bundle** | One encrypted clinical message: a header plus ciphertext. |
| **Shard** | One of the *k + m* erasure-coded pieces of a bundle's ciphertext. Each shard can be verified on its own. |
| **Service area (SA)** | A public, stable routing destination: a hub, or a group of clinics. Relays route on SAs only. |
| **Trial decryption** | Every endpoint in the destination service area tries to open a delivered bundle; only the intended recipient can. The header names no recipient. |
| **Custody** | A node's signed acceptance of responsibility for a set of shards. |
| **Delivery ACK** | Signed confirmation from the destination endpoint that the bundle was rebuilt and decrypted. |
| **HLC** | Hybrid Logical Clock: wall-clock time combined with a logical counter. |

## 5. System architecture

### 5.1 Roles

```
 ┌──────────── Clinic ────────────┐                                   ┌──────────── Hospital ───────────┐
 │ Endpoint (laptop PWA)          │                                   │ Endpoint (laptop/server PWA)     │
 │  • writes FHIR-lite message    │                                   │  • reconstructs + decrypts       │
 │  • encrypts, shards, signs     │                                   │  • replies, issues delivery ACK  │
 │        │ USB/Ethernet          │                                   │        ▲                          │
 │        ▼                       │   Wi-Fi (data) / BLE (discovery)  │        │                          │
 │ Site node (Pi 4/5 + SSD)       │◄────────► Mules ◄────► Relays ◄──►│ Hub (Pi 4/5 + SSD, CA)           │
 └────────────────────────────────┘     (Pi Zero 2 W, ambulances,     └──────────────────────────────────┘
                                         vans, street lights)
                     ◄──────────── LoRa control channel (ACKs, receipts, revocations, hints) ────────────►
```

### 5.2 Data path (clinic → hospital)
1. Staff compose a message in the PWA (§10).
2. The **endpoint** serialises it as FHIR-lite, encrypts it to the recipient (§7.4), signs the inner content, erasure-codes the ciphertext into *k + m* shards, and hands the shards plus a header to its site node.
3. The **site node** signs the outer header with its hardware key (§7.2) and queues the shards.
4. When nodes meet, they exchange summaries, transfer missing shards most important first, and sign custody receipts back asynchronously (§8).
5. **PRoPHET routing** (epidemic until warmed up) decides which shards to give each peer (§8.6).
6. Once any *k* shards reach a node in the destination SA, they are handed to the destination endpoint. The endpoint rebuilds, verifies and decrypts the bundle, then issues a **delivery ACK**.
7. The ACK floods back over Wi-Fi encounters and LoRa. Every node then purges that bundle's shards. The origin marks the message as *Delivered*.

Replies (hospital → clinic) take the same path in reverse. Traffic is **bidirectional** between endpoints.

## 6. Data model and wire format

Our own **versioned CBOR** encoding, borrowing concepts from BPv7 (RFC 9171): endpoint IDs, creation timestamp, lifetime, Bundle Age. It is not wire-compatible with BPv7.

### 6.1 Bundle header (readable by relays, signed by the injecting site node)

| Field | Type | Notes |
|---|---|---|
| `version` | uint | Format version. |
| `bundle_id` | 32 B | SHA-256 of the ciphertext Merkle root plus creation HLC. Content-addressed. |
| `src_sa` / `dst_sa` | str | Public **service-area** IDs. Routing uses only these. |
| `priority` | enum | `CRITICAL` / `URGENT` / `ROUTINE` / `TELEMETRY` (§6.3). |
| `created_hlc` | HLC | Creation time (§8.9). |
| `lifetime_s` | uint | TTL in seconds. |
| `age_ms` | uint | Bundle Age: dwell time added up by each node's monotonic clock. Lets nodes apply the TTL even without trustworthy wall-clock time. |
| `size_bucket` | enum | Ciphertext padded up to a fixed bucket (e.g. 4 KB, 64 KB, 1 MB, 8 MB, 32 MB) so size leaks little. |
| `k`, `m` | uint | Erasure parameters. |
| `merkle_root` | 32 B | Root over all *k + m* shard hashes. |
| `injector_cert_id` | bytes | Site node certificate that signed this header. |
| `sig` | bytes | P-256 ECDSA over all of the above, produced inside the secure element. |

The header deliberately has **no** patient identifiers, endpoint identities or free text.

### 6.2 Shard (the unit that moves over radio)
`{ header, shard_index, shard_bytes, merkle_proof }`

Any node can check a shard on its own: verify the header signature against a valid, unrevoked node certificate, then verify `shard_bytes` against `merkle_root` using `merkle_proof`. A shard either verifies completely or is thrown away. Partial transfers can never leave a corrupt record in storage.

### 6.3 Priority classes (defaults, set in deployment config)

| Class | Use | Default TTL | Erasure *k + m* | Eviction |
|---|---|---|---|---|
| `CRITICAL` | Urgent referral, deterioration notes | 6 h | 4 + 8 | Never evicted to make room for lower classes |
| `URGENT` | Same-day results, replies | 24 h | 4 + 4 | Evicted only for `CRITICAL` |
| `ROUTINE` | Routine referrals, documents, media | 72 h | 4 + 2 | Oldest first, after `TELEMETRY` |
| `TELEMETRY` | Node health, encounter stats | 12 h | 1 + 0 (replicated) | Evicted first |

"Critical" means *clinically important to deliver*, not *time-critical* (see §2.3).

### 6.4 Clinical payload (inside the ciphertext): FHIR-lite
A small, versioned subset of HL7 FHIR R4 resources, so hospitals can later import into real systems:
- `Patient` (minimal demographics; local identifiers only)
- `Observation` (vitals: BP, HR, RR, SpO₂, temperature, blood glucose, blood group)
- `Communication` (free-text note, referral, reply; `inResponseTo` for threads)
- `DocumentReference` + `Binary` (photos, PDFs, EKG traces, voice memos, video clips)

Each payload is signed by the **endpoint's** Ed25519 key *inside* the encryption (§7.3), which proves clinical authorship without revealing it to relays.

Supported v1 payload sizes run from bytes (vitals) to tens of MB (media). Large media is expected to move mostly during long contacts (clinic visits, hub docking), not drive-bys.

## 7. Security architecture

### 7.1 Threat model (summary)

| Threat | Mitigation |
|---|---|
| Relay/mule stolen or compromised | Holds only ciphertext and no decryption keys. Node key lives in the secure element and cannot be exported. Short-lived certificate plus revocation (§7.6). |
| SD card pulled out | LUKS full-disk encryption; key held in the secure element (§7.7). |
| Forged or junk bundles flooding the mesh | Headers must be signed by a valid site-node certificate. Per-injector rate limits. Unsigned or invalid shards are dropped. |
| Forged custody receipts | Receipts are signed by the node's secure element and hash-chained (§8.5). |
| Bit rot, transmission corruption | Shards are checked against the Merkle tree; *k*-of-*(k+m)* erasure coding. |
| Replay | Content-addressed bundle IDs, HLC ordering, and receipts and ACKs that bind specific IDs. |
| Traffic analysis | Service-area routing, no recipient or sender identity in headers (trial decryption), size buckets. Residual risk recorded in the DPIA. |
| Clock tampering or reset | RTC, HLC, Bundle Age; no security decision depends only on wall-clock time (§8.9). |

### 7.2 Identities and algorithms

| Holder | Key | Algorithm | Storage |
|---|---|---|---|
| **Node** (every Pi) | Signing key | **ECDSA P-256** | Generated inside an **ATECC608B** secure element; never exportable |
| **Endpoint** (laptop) | Signing key | Ed25519 | Software keystore on a full-disk-encrypted laptop |
| **Endpoint** | Encryption key | X25519 | Same |
| **Hub CA** | Root + issuing key | P-256 (signs node and endpoint certs) | Offline root; issuing key on hub (secure element) |

Two signature algorithms are a deliberate trade-off. Node keys are tied to hardware, which needs P-256 on the ATECC608B. Endpoint keys stay on modern curves in software.

### 7.3 Two-layer signatures
- **Outer** (header): signed by the injecting **site node**. Relays verify it to decide whether to accept and carry the shard, which stops flooding by unregistered hardware.
- **Inner** (inside ciphertext): signed by the authoring **endpoint**. Only the recipient can verify it. This is clinical non-repudiation without exposing who wrote what.

### 7.4 Payload encryption (endpoint only)
1. Generate a random 256-bit content key.
2. Encrypt the signed FHIR-lite payload with **XChaCha20-Poly1305** (libsodium via PyNaCl). Random 192-bit nonces are safe, and it is fast in software on every Pi.
3. Wrap the content key for the recipient's X25519 key (ECIES-style: ephemeral X25519 → HKDF-SHA256 → AEAD).
4. Pad to the size bucket, then erasure-code (**encrypt-then-shard**).

Use the vetted `cryptography` library. No hand-rolled cryptographic primitives.

### 7.5 Service areas and recipient privacy
- Relays route on **public, stable SA IDs**: a hub, or a named group of clinics. This is what PRoPHET learns.
- The header names **neither sender nor recipient endpoint**. Authorship lives only in the inner signature, and the recipient is implicit in the encryption key.
- Delivery inside an SA: a bundle bound for an SA is delivered to **every site node in that SA**. Every endpoint there tries to open it (**trial decryption**), and only the intended recipient succeeds. A failed attempt costs one X25519 exchange and one AEAD tag check, which is negligible at pilot scale.
- *Decision (replaces the earlier pseudonym design):* an HMAC pseudonym keyed by the recipient's secret can't be computed by senders, and a shared group secret adds a key to distribute and rotate. Trial decryption hides the recipient completely with no extra keys.

### 7.6 PKI, provisioning and revocation
- **Provisioning:** in person at a hub. The node's secure element generates its key, and the hub CA issues a certificate binding `node_id`, role and SA.
- **Operational certificates:** short-lived (default **14 days**), renewed **only when docked** at a hub. A node that misses renewal stops being trusted automatically.
- **Revocation:** the hub signs a revocation list. It spreads over the mesh with top priority and over LoRa. Nodes reject shards, headers and receipts from revoked or expired certificates.
- **Endpoint directory:** hubs publish signed bundles of endpoint public keys and service areas. They spread over the mesh so clinics can encrypt to recipients they have never met.
- **Root CA** stays offline. Losing or rotating it is a documented manual procedure.

### 7.7 Data at rest
- **Relay, mule and site nodes:** LUKS2 full-disk encryption on the data partition. The LUKS key is stored in an ATECC608B slot protected by an IO-protection key bound to that Pi.
  - *Residual risk (documented):* a thief who takes the **whole working unit** can boot it and unlock the disk. They would still only get ciphertext and a node key that revocation and certificate expiry neutralise.
- **Endpoints:** OS full-disk encryption (BitLocker, FileVault or LUKS) is a deployment requirement, because plaintext exists there.

## 8. Network protocol

### 8.1 Design principle
Every exchange must stay **safe if the link drops at any byte**. There are no multi-round-trip handshakes before data starts moving. Nodes push self-verifying shards straight away, and everything else (receipts, ACKs) is asynchronous.

### 8.2 Discovery
- Nodes send **BLE advertisements** carrying a short rotating token derived from the node certificate. The token is unlinkable to outsiders and resolvable by any node holding the current SA directory.
- A peer seen over BLE triggers **Wi-Fi** link setup with pre-configured addressing (no DHCP), so data can flow quickly. *Target: data flowing within 2 s of BLE detection. Validate in Phase 2.*
- Before trusting a peer, each side checks the other's certificate validity and revocation status.

### 8.3 Summary exchange (hybrid)
- **Opening message:** a **Bloom filter** of held shard IDs and ACKed bundle IDs. One packet, sent immediately, sized for a false-positive rate of about 1% at pilot scale.
- **Follow-up:** if the link is still up after the first transfer burst, send **exact lists** of truncated shard IDs to fix Bloom false positives. A false positive can only delay a shard. It never causes loss, because the sender keeps the shard until custody or delivery ACK (§8.5).

### 8.4 Transfer scheduling
Order of transmission within a contact:
1. Revocation lists, delivery ACKs, custody receipts (tiny, highest value)
2. `CRITICAL` shards, then `URGENT`, `ROUTINE`, `TELEMETRY`
3. Within a class: shards of bundles closest to *k* at the peer's SA first, then smallest first

Each shard is written to storage in **one atomic transaction** before it counts as received.

### 8.5 Asynchronous custody
- A receiver gathers the shards it has durably stored and signs one **custody receipt**: `{receiver_cert_id, sender_cert_id, shard_ids[], hlc, prev_receipt_hash}`, signed by its secure element.
- The receipt goes back over the same link if it is still up. Otherwise it travels later over any path, including LoRa.
- Each node keeps an **append-only, hash-chained custody ledger** in SQLite. It can be exported as JSON or CSV for information-governance audit.
- **Deletion rules:**
  - The **origin site node** never deletes a bundle's shards until the **delivery ACK** arrives.
  - A **relay or mule** may drop a shard after custody is receipted by a peer with higher delivery predictability for the destination SA, or when a delivery ACK arrives, or when the TTL expires.
  - A node that is **full** refuses new shards: it simply does not receipt them, so the sender keeps them. Refusal is always safe.

### 8.6 Routing
- **Cold start: epidemic.** Every eligible shard is offered to every peer, limited by storage and eviction policy.
- **Warm: PRoPHET (RFC 6693 concepts)** with service areas as destinations. Each node keeps delivery predictabilities `P(node, SA)`, updated on encounter, aged over time, and made transitive across peers. A shard is forwarded to a peer whose `P` for the shard's `dst_sa` is higher than the holder's.
- **Warm-up switch:** per SA, a node switches from epidemic to PRoPHET once it has seen at least *N* encounters relevant to that SA (default *N* = 20, tune in emulator).
- **Erasure coding makes paths diverse naturally:** different shards of one bundle should go to different peers where possible, so no single mule carries *k* shards unless no alternative exists.

### 8.7 Delivery ACK and purge
- When the destination endpoint has rebuilt, verified and decrypted a bundle, it issues a **delivery ACK** `{bundle_id, hlc}` signed by the destination's site node. Clinical identity stays out of it.
- ACKs have top priority over Wi-Fi and LoRa and spread epidemically. Every node that sees one purges that bundle's shards (the chat's "poison pill").
- The origin endpoint shows **Delivered** with the time.

### 8.8 TTL expiry
- Relays and mules purge expired shards.
- The **origin endpoint keeps the original** until it is ACKed. On expiry it shows a prominent **"NOT DELIVERED — use another channel"** alert, and staff decide whether to resend. There is no automatic resend.

### 8.9 Time
- Every node has a **DS3231 RTC** with a coin-cell backup, so wall-clock time survives power cuts.
- Ordering uses a **Hybrid Logical Clock**: max(RTC, highest HLC seen) plus a counter. HLCs are exchanged on every encounter.
- TTL is enforced using **Bundle Age** (summed monotonic dwell time) as well as the creation HLC. The stricter result wins, so a reset or tampered clock can neither revive nor prematurely kill a bundle.
- Certificate validity is checked against the HLC. Hubs, which may have internet or NTP, act as time anchors on docking.

### 8.10 LoRa control channel
- An SX1262 module on every node, long range and very low bandwidth.
- It carries **only small, signed control messages**: delivery ACKs, custody receipts, revocation entries, and PRoPHET predictability summaries. **It never carries payload shards.**
- It must respect regional duty-cycle and band rules (for example EU868 sub-band duty cycles, US915 dwell time), set per deployment. Messages must fit a single LoRa frame (roughly ≤ 200 B). A P-256 signature is 64 B.

## 9. Node software

### 9.1 Modules (`node/`)

The node logic is a **sans-IO core**: `NodeCore.handle(event) -> list[Action]`, with no direct access to clocks, radios, disk or randomness. The emulator and the real daemon are two drivers for the same core.

| Module | Responsibility |
|---|---|
| `core.py` / `events.py` | Sans-IO state machine; event and action types. |
| `daemon.py` | asyncio driver for real hardware (hardware phase). |
| `hw/` | `SecureElement`, `Rtc`, `PowerSource`, `Radio`, `Entropy` interfaces. Virtual implementations live in `simulation/`; real drivers are added in the hardware phase. |
| `bundle.py` | CBOR header/shard encoding, Merkle tree, verification. |
| `erasure.py` | Thin wrapper over **zfec** (proven Reed-Solomon library). No hand-rolled GF(2⁸) maths. |
| `crypto.py` | Node-side signing (secure element via `cryptoauthlib`), certificate and revocation checks. |
| `pki.py` | Certificate parsing, revocation list, endpoint directory. |
| `routing.py` | Epidemic → PRoPHET, predictability tables, forwarding decisions. |
| `storage.py` | SQLite (WAL, `synchronous=FULL`) queue, eviction, custody ledger. |
| `clock.py` | RTC read, HLC, Bundle Age accounting. |
| `api.py` | Local HTTP/WebSocket API for the attached endpoint (site nodes only). |

Endpoint-only crypto (payload encryption and decryption, inner signatures, sharding, FHIR-lite) lives in a separate **`endpoint/`** package. `node/` must never import it, and a test enforces that boundary.

### 9.2 Storage
- SQLCipher (key held in the secure element) with `journal_mode=WAL`, `synchronous=FULL`, on the LUKS volume. The emulator uses the same SQLCipher path.
- Tables: `shards`, `bundles` (header cache), `custody_ledger` (append-only, hash-chained), `acks`, `revocations`, `prophet`, `peers`, `telemetry`.
- Eviction follows §6.3. Above 95% capacity, refuse incoming shards that would need a forbidden eviction.
- Power loss: rely on SQLite WAL atomicity plus a UPS HAT that signals the daemon to flush and shut down cleanly (§11.2).

### 9.3 Packaging and operation
- One installable Python package; the same code runs on every role, with the role set in `/etc/dtn-medical-mesh/node.toml`.
- Runs as a `systemd` service with automatic restart and a watchdog.
- Pre-built SD-card image for Pi Zero 2 W and Pi 4/5 (Phase 2).
- Headless: health is reported as `TELEMETRY` bundles and is visible on the hub map.

## 10. Base station interface (endpoint)

### 10.1 Stack
FastAPI backend and a **messaging-style PWA**. It runs locally on the laptop or tablet and talks to its site node over USB or Ethernet.

### 10.2 UX rules (to avoid "shadow workload")
- It should feel like a chat app: one conversation per patient or referral, a text box, and an attach button.
- Sending takes **no more taps than WhatsApp**. Priority defaults to `ROUTINE`, and raising it is one tap.
- Every message shows a status: *Queued → In transit (n/k shards seen) → Delivered* or **NOT DELIVERED**. It also shows the expected-latency and "no guarantee of timing" indicator (§2.3).
- Structured vitals have quick-entry forms that map to FHIR `Observation`.
- No clinical content is ever shown on, or stored by, a non-endpoint node.

### 10.3 Network map
- A Leaflet (or plain canvas) view **with no basemap or tiles**: nodes plotted on a schematic coordinate plane, so it works fully offline.
- Shows node role and status, battery, queue depth, active links, shards in flight, and custody and ACK events.
- Fed by one **telemetry JSON schema**. In the field it comes from `TELEMETRY` bundles and fixed install coordinates. In simulation it comes from the emulator. Same schema, same UI.

## 11. Hardware

### 11.1 Bill of materials by role

| Component | Relay / Mule | Site node / Hub |
|---|---|---|
| Compute | Raspberry Pi Zero 2 W | Raspberry Pi 4 or 5 |
| Storage | Industrial high-endurance microSD (e.g. SanDisk Industrial, Swissbit), 32 GB | Industrial USB SSD (≥128 GB) + industrial microSD boot |
| Secure element | ATECC608B (I²C) | ATECC608B (I²C) |
| RTC | DS3231 + CR2032 | DS3231 + CR2032 |
| LoRa | SX1262 module + antenna (region-correct band) | SX1262 module + antenna |
| Wi-Fi / BLE | On-board | On-board |
| Power protection | UPS HAT (e.g. PiSugar 3 or Geekworm X-series) with shutdown signal | UPS HAT or mains UPS |
| Power input | 12–24 V → 5 V/3 A DC-DC buck (vehicle/solar) | Mains or 12 V |
| Solar (street light) | Panel + battery sized per site irradiance | — |
| Enclosure | IP67 polycarbonate (~150×100×70 mm), cable glands, internal aluminium heat-spreader, white/shaded exterior | Indoor case |

### 11.2 Power behaviour
- The UPS HAT signals the daemon on input loss. The daemon finishes the current transaction, checkpoints WAL, and shuts down within the UPS hold-up time. *Target: ≤ 3 s. Validate in Phase 2.*
- Vehicle installs must tolerate engine-crank dips and load dumps (the DC-DC converter must be automotive-rated).

### 11.3 Environmental
- Sealed enclosures in direct sun can exceed 65 °C. Measure internal temperature in the Phase 2 heat test, and throttle or suspend Wi-Fi bursts above a configured threshold.
- Vibration: secure the SD card and connectors; prefer soldered headers.

## 12. Emulator

### 12.1 Principle
The emulator drives the **real `NodeCore`** in a single process on a **discrete-event virtual clock**, so days of scenario run in seconds and every run can be reproduced from its seed. Only the hardware is virtual: radios, RTC, power/UPS, a software P-256 secure element, and seeded entropy. If the emulator works, the protocol logic works.

Simulated power cuts throw away a node's in-memory state and rebuild it from its storage file. True mid-write crashes are covered separately by a subprocess `SIGKILL` test against `storage.py`.

### 12.2 Physical pressures modelled

| Pressure | Model | What it exercises |
|---|---|---|
| Mobility | Nodes follow scenario routes or schedules; contact when distance ≤ radio range | Store-and-forward, PRoPHET learning |
| Short contacts | Contact duration from relative speed and range; link bandwidth limit | Transfer scheduling, Bloom/exact summaries |
| Link loss and bit errors | Per-link loss rate, burst loss, bit flips | Merkle verification, erasure recovery |
| Power cuts | Random and scheduled brownouts, battery and solar models; the process is *really killed* mid-transaction | WAL durability, custody correctness |
| Clock faults | RTC drift, RTC failure, reset to epoch | HLC and Bundle Age |
| Storage pressure | Small capacity limits | Eviction, refusal, no-silent-loss |
| Hostile nodes | Revoked or unregistered injector, forged receipts, corrupt shards | PKI and verification |
| LoRa | Range, duty-cycle budget | Control-plane spread |

### 12.3 Scenarios (`simulation/scenarios/*.yaml`)
Region-agnostic, with several shipped:
- `rural-clinics`: hub, 4 clinics, 2 vans, 1 ambulance, 3 street-light relays
- `disaster-response`: field hospital, ad-hoc mules, failing relays
- `island-medevac`: sparse, long-delay contacts
- `stress`: every pressure at once

Each scenario defines nodes, SAs, routes, schedules, radio parameters, fault injection and message workload. Runs take a seed so they can be reproduced exactly.

### 12.4 Output
- Drives the **web map** (§10.3) through the shared telemetry schema.
- The terminal shows plain logs.
- At the end of a run it writes a metrics report: delivery ratio by class, latency distribution, NOT-DELIVERED count with reasons, storage peaks, shards per delivered bundle (overhead), and custody-ledger consistency checks.

## 13. Regulatory and clinical safety (design-aligned, not certified)

The project follows the *structure* of these frameworks so a future partner can build on it. It does **not** claim conformity, certification or a device classification. Those depend on the manufacturer, intended use and jurisdiction.

| Framework | What the repo provides |
|---|---|
| IEC 62304 | Software requirements list with IDs, architecture document, SOUP list (zfec, cryptography, etc.), configuration management via git. |
| ISO 14971 | `docs/hazard_log.md`: hazards (e.g. undetected non-delivery, wrong recipient, corruption, delay mistaken for delivery), causes, controls, residual risk. |
| UK/EU GDPR Art. 9 | DPIA template; data-flow map; metadata minimisation rationale (no endpoint identities in headers). |
| HIPAA §164.312 | Mapping of access control, audit (custody ledger), integrity (Merkle + signatures) and transmission security (E2E encryption). |
| UK DCB0129/0160 (if piloted in UK) | Placeholder for clinical safety case; needs a partner's Clinical Safety Officer. |

**Traceability:** `validation/trace_matrix.py` links each `REQ-*` ID to the tests that verify it, and reports which requirements are covered or uncovered. It reports **test coverage of requirements, never "compliance"**.

**What a pilot partner must provide** (the project is currently solo and looking for a partner): a data controller, a DPIA sign-off, a clinical safety officer, the clinical workflow definition, staff training, and a fallback channel that runs in parallel throughout the pilot.

## 14. Repository layout

```
dtn-medical-mesh/
├── README.md
├── PROJECT_SPEC.md              # this document
├── LICENSE                      # Apache-2.0
├── pyproject.toml
├── docs/
│   ├── architecture.md
│   ├── protocol.md              # wire format + encounter protocol (detail of §6, §8)
│   ├── hazard_log.md            # ISO 14971-style
│   ├── dpia_template.md
│   ├── requirements.md          # REQ-* IDs
│   └── hardware/                # BOM, wiring, enclosure, power notes
├── node/                        # sans-IO core + daemon (§9)
├── endpoint/                    # endpoint-only crypto, FHIR-lite (§7.4, §6.4)
├── interface/                   # FastAPI + PWA + map (§10)
├── simulation/                  # emulator + scenarios (§12)
├── validation/                  # tests + trace_matrix.py (§13, §15)
└── deploy/                      # systemd unit, node.toml examples, image build
```

## 15. Testing and validation
- **Unit:** encoding round-trips, Merkle proofs, erasure decode from every *k*-subset, HLC properties, eviction rules, PKI expiry and revocation.
- **Property/fuzz:** random truncation, bit flips and reordering of shards; random power kills during writes (the store must reopen consistent, and no receipted shard may be missing).
- **Protocol:** the emulator's `stress` scenario in CI with fixed seeds; assert **zero silent losses**: every injected bundle ends either ACKed or flagged NOT DELIVERED at its origin.
- **Security:** rejection of revoked or expired certificates, forged receipts, tampered headers and unregistered injectors.
- **Bench (Phase 2):** real link-up time, throughput per contact, power-cut recovery, enclosure temperature, LoRa range and duty cycle.

## 16. Roadmap

Step-by-step breakdown: [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md). There is no hardware yet, so Phase 1 below is built and validated entirely in emulation.

| Phase | Scope | Exit criteria |
|---|---|---|
| **1 — Simulation** | Node core, endpoint crypto, emulator, PWA and map, all on one machine | `stress` scenario passes zero-silent-loss; full trace matrix coverage of P1 requirements |
| **2 — Bench / drive test** | 2–6 real nodes; secure element, RTC, LoRa, UPS; drive-by tests | Measured contact budgets inform the defaults; power-cut and heat tests pass |
| **3 — Supervised field pilot** | ~10–30 nodes with a partner, running **in parallel** with existing channels | Partner governance in place (§13); pilot metrics reviewed against hazard log |

## 17. Decision log: corrections to the original brainstorm

Decisions made while turning the original design chat into this spec:

| Original idea | Decision | Reason |
|---|---|---|
| "Sipser-Zimzewitz" erasure coding; hand-written XOR "Reed-Solomon" | Real Reed-Solomon via **zfec** | The name doesn't exist; the XOR code could not rebuild data once a data shard was lost. |
| Synchronous custody *and* fire-and-forget (contradictory) | **Asynchronous custody** (§8.5) | Safe at any link-drop point and still never deletes without a receipt. |
| Epidemic vs spray-and-wait vs PRoPHET | **Epidemic → PRoPHET** (§8.6) | PRoPHET has no history on day one. |
| Bloom filter only | **Bloom then exact list** (§8.3) | False positives would skip shards. |
| "RFC 9171 custody transfer compliance" | Custom custody receipts; BPv7-*inspired* format | BPv7 (RFC 9171) has no custody transfer (it was in RFC 5050). |
| LUKS with a TPM-bound key | LUKS with key in an **ATECC608B** | The Pi has no TPM. |
| Ed25519 everywhere | **P-256 for node keys** (secure element), Ed25519/X25519 for endpoints | ATECC608B supports P-256 only. |
| Timestamps in receipts | **RTC + HLC + Bundle Age** | The Pi has no RTC; clock resets broke TTLs and receipt ordering. |
| Pi encrypts payloads | **Endpoint (laptop) only** | No Pi ever sees plaintext. |
| Clinic → hospital only | **Bidirectional** | Referrals need replies. |
| WhatsApp bot bridge | **PWA only** | PHI never leaves the system. |
| CartoDB online tiles | **No basemap** | Pilot sites may be offline. |
| Terminal ASCII map + rich dashboard | **Web map only, real node code** | One UI and one telemetry schema. |
| "100% COMPLIANT", "data can never be lost" | Design-aligned, no claims; **no *silent* loss** | Honest and testable. |
| Stable destination IDs | **Service-area routing + trial decryption** (no recipient in header) | Privacy without breaking PRoPHET; pseudonyms couldn't be computed by senders. |
| Fixed k=4, m=2 | **Parity scales with priority** | More paths for important data. |
| Repo name `dtn-mesh-medical` | `dtn-medical-mesh` | Matches the actual repository. |

## 18. Open questions
1. ~~AEAD choice~~ (decided: XChaCha20-Poly1305).
2. PRoPHET parameters (P_init, β, γ, ageing unit) and the warm-up threshold *N*: tune in Phase 1.
3. ~~Pseudonym epoch length~~ (pseudonyms dropped in favour of trial decryption, §7.5).
4. Size-bucket boundaries for large media. (Decided: payloads over 32 MB are rejected at send.)
5. BLE rotating token scheme details and its privacy properties.
6. Endpoint key recovery when a clinic laptop is lost (re-provision at a hub? escrow?).
7. Whether relays should drop **TELEMETRY** entirely when storage is under pressure.
8. Pilot jurisdiction, which decides LoRa band, data-protection regime and clinical safety standard.
