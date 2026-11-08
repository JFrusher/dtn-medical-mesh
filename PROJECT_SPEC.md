# Satchel (dtn-medical-mesh) — Project Specification

| | |
|---|---|
| **Status** | Draft v0.2: emulated implementation complete (phases 0–7); v0.2 adds node, staff-app and emulator-fidelity design |
| **Licence** | Apache 2.0 |
| **Primary language** | Python 3.11+ (node, emulator, servers); TypeScript/JS in the staff app |
| **Target** | Supervised real-world pilot (~10–30 nodes), reached via simulation → bench → field |
| **Regulatory posture** | Design-aligned with IEC 62304 / ISO 14971 / UK & EU GDPR Art. 9 / HIPAA §164.312. **Not certified. No compliance is claimed.** |

---

## 1. Summary

`dtn-medical-mesh` is an offline-first, **delay-tolerant network (DTN)** that moves encrypted clinical data between health sites that have no reliable internet. It uses the vehicles and people already travelling between those sites as couriers: ambulances, supply vans, outreach staff.

Small, ruggedised Raspberry Pi nodes are mounted wherever it helps: on a clinic table, on an ambulance's back shelf, on a street light. They find each other automatically, swap encrypted shards when they come within radio range, and carry them onward. A messaging-style web app on a laptop is the only thing staff ever touch; crews just see a status light. Clinic staff write a referral or attach a photo, and some time later the hospital receives it and can reply.

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
- **No per-peer shard cap:** mules copy shards rather than move them, so any peer may carry a full set. Capping a peer below *k* would stop a single mule completing a delivery on its own and raise the NOT DELIVERED risk; path diversity comes naturally because different mules meet different peers.

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

### 9.3 Node daemon (hardware driver)
The daemon is the second driver of `NodeCore` (the emulator is the first). It owns everything the core may not touch.

| Concern | Design |
|---|---|
| Process model | One asyncio process: a radio task per radio, a timer wheel, a storage writer applying actions strictly in order, and a local API task (site nodes only). |
| Supervision | `systemd` service with `WatchdogSec`; the daemon pings the watchdog only after a full event loop turn, so a hung radio driver causes a restart. |
| Configuration | `/etc/satchel/node.toml`: role, service area, radio band and power, capacity, status-light pins. Signed by the hub when issued at provisioning; the daemon refuses an unsigned config. |
| Logs | Structured JSON lines to a small ring buffer on disk (no clinical content ever exists on a node to leak). Summaries travel as `TELEMETRY` bundles. |
| Health telemetry | Every 15 min (assumption, configurable): battery, temperature, storage use, contacts seen, refusals, software version. Lowest priority, evicted first. |
| Status light | One RGB LED, readable by crews at a glance (§11.4). |

### 9.4 Provisioning, replacement and decommissioning
1. **Provision at a hub.** The node's secure element generates its key; the admin console (§10.6) issues a certificate binding node ID, role and service area, writes the signed `node.toml`, and records the node in the inventory.
2. **Replace.** A failed node is revoked and a new one provisioned under a new node ID; shards it held are recovered by redundancy and the origin's retained copy, never by copying its disk.
3. **Stolen or lost.** The admin revokes it; the revocation list reaches the mesh with top priority (§7.6). Its certificate also expires within 14 days without a hub visit.
4. **Decommission.** Revoke, then wipe by destroying the secure element's storage key (the SQLCipher store becomes unreadable).
5. **Endpoint laptops** are provisioned the same way: the laptop generates its endpoint keys, the hub adds them to the signed directory; a lost laptop is removed from the directory and its keys never receive new messages.

### 9.5 Signed software updates over the mesh
- **Package.** The hub publishes an update as a hub document (§7.6) of kind `update`: version, target roles, minimum current version, payload hash and size. The payload itself travels as a normal `ROUTINE` bundle addressed to every service area, erasure-coded like any message.
- **Authority.** Updates must be signed by a dedicated **release key** held offline next to the root CA, not by a hub's operational key, so a compromised hub cannot push software.
- **Install.** A node installs only when it holds the complete, verified payload, is on external power, and has no transfer in progress. A/B partitions: the new version boots once on probation; if the daemon fails to report healthy within 10 min (assumption), the bootloader rolls back automatically.
- **Pacing.** Updates never displace clinical traffic: lowest priority after `TELEMETRY`, and the hub can stage rollouts (relays first, then mules, then sites).
- **Emulation.** The emulator models update spread and the rollback path; the real A/B mechanism is built in hardware phase H1.

### 9.6 Laptop ⇄ site node link
The clinic laptop reaches its site node over the node's own **local Wi-Fi access point** or USB. The link is authenticated with the laptop's endpoint key: a laptop that is not in the directory cannot inject. The site node never sees plaintext: the laptop's Python endpoint (§7.4) hands it finished shards and receives rebuilt ciphertext.

## 10. Staff app and consoles

### 10.1 One app, on the clinic laptop
- A **web app** (PWA) served locally by the endpoint service on the clinic laptop; works fully offline in the laptop's browser.
- All clinical crypto runs in the Python `endpoint/` package on the laptop (§7.4), so plaintext never reaches the site node.
- **Languages:** built on a translation catalogue from the start (ICU MessageFormat strings, no text in code). English and Swahili first; every Swahili string reviewed by a native-speaking clinician before a pilot. Layout tolerates longer strings; numbers, dates and units are localised.
- **Accessibility:** full keyboard use; minimum 44 px targets for touchscreen laptops; screen-reader labels; never colour alone for status.

### 10.2 Sign-in on the shared laptop
- Each clinic **laptop** holds its clinic's endpoint keys (§9.4), encrypted at rest and unlocked only while the app is open.
- Each **staff member** signs in with a short personal PIN (rate-limited, locks after repeated failures). Messages record who wrote them inside the signed payload; the hospital sees "Nurse A. Mollel, Murieti".
- Auto-lock after inactivity (default 5 min, assumption). A lost laptop is removed from the directory by the admin.

### 10.3 Clinic workflow
| Screen | Purpose |
|---|---|
| **Patients** | List per clinic: local ID, name, age, sex. Messages are grouped by patient, not by conversation. |
| **New referral** | Structured form mapped to FHIR `ServiceRequest`: reason, working diagnosis, history, vitals, treatment given, urgency; photo attachments (wounds, lab slips). Free-text note always allowed. |
| **Case view** | One referral's thread: status (*Sent, In transit, Opened, Accepted / Declined / Needs information*), hospital advice, results, and the delivery deadline. |
| **Results** | Lab and imaging results from the hospital (FHIR `DiagnosticReport`), linked to the patient. |

Patient identifiers inside the encrypted payload are limited to **name, age, sex and local ID** (no national or insurance numbers).

### 10.4 Hospital triage
- **Inbox sorted by urgency, then age**, with the clinic, patient summary and how long it took to arrive.
- One-tap responses: **Accept** (with expected arrival advice), **Decline** (with reason and alternative), **Need information** (question back to the clinic). Each response is itself a message, tracked like any other.
- Results can be sent back to the originating clinic from the case.

### 10.5 Crews (zero-touch)
Ambulance and transport crews never interact with the system. The node's status light (§11.4) tells them it is working; the delivery state lives with the staff who sent the message.

### 10.6 Admin console (hub)
For the network administrator, on the hub's local network only:
- **Inventory:** every node and laptop, role, service area, software version, last contact, battery and storage from health telemetry.
- **Provision / revoke** nodes and laptops (§9.4); **publish** the endpoint directory and revocation list.
- **Updates:** stage and monitor signed software rollouts (§9.5).
- **Network health:** delivery times per clinic, messages flagged NOT DELIVERED, refusals by reason (possible attacks), nodes not seen recently.
All admin actions are signed with the operator's hub credential and logged.

### 10.7 Network map and journey (demo and admin)
The live map (real streets offline when the scenario has coordinates, a plain ground otherwise), moving message pieces, and the journey view of one message are shared by the emulator demo and the admin console. They are fed by one telemetry schema (`docs/telemetry.schema.json`).

## 11. Hardware

### 11.1 Variants by role
Indicative prices (USD, 2026, single units, before shipping and duty); confirm at purchase.

| Part | Relay (street light) | Mule (vehicle) | Site / hub |
|---|---|---|---|
| Compute | Pi Zero 2 W | Pi Zero 2 W | Pi 5 (4 GB) |
| Storage | Industrial microSD 32 GB | Industrial microSD 32 GB | NVMe SSD 256 GB + microSD boot |
| Secure element | ATECC608B | ATECC608B | ATECC608B |
| RTC | DS3231 + CR2032 | DS3231 + CR2032 | Pi 5 RTC + battery |
| LoRa | SX1262, external antenna | SX1262, roof or rear-window antenna | SX1262, mast antenna |
| Wi-Fi | On-board + external antenna option | On-board | On-board; serves the staff app access point |
| Power | 10–20 W panel, 12 V LiFePO4 battery, charge controller, UPS HAT | 12–24 V vehicle supply via automotive DC-DC, UPS HAT | Mains + small UPS |
| Status light | 1 × RGB LED, visible through enclosure | 1 × RGB LED on the dash cable | 1 × RGB LED |
| Enclosure | IP67, white, shaded, pole clamp | IP54, vibration mounts | Indoor case |
| **Target total** | **~$90–100** (excluding the pole) | **~$60–80** | **~$150–250** |

### 11.2 Power budget (assumptions until measured in H1)
| Variant | Idle | Transferring | Daily energy (assumed duty) |
|---|---|---|---|
| Pi Zero 2 W node | ~0.7 W | ~2 W | ~20 Wh |
| Pi 5 hub | ~3 W | ~7 W | ~90 Wh (mains) |

A solar relay must ride through the longest expected run of overcast days at the pilot site; the panel and battery are sized from the site's irradiance data during pilot planning (§18). The emulator's power model uses these figures as parameters.

### 11.3 Power behaviour
- The UPS HAT signals the daemon on input loss; the daemon signs receipts for everything held (already implemented in the core), finishes the current transaction and shuts down within the hold-up time. *Target: ≤ 3 s; validate in H1.*
- Vehicle installs tolerate engine-crank dips and load dumps (automotive-rated DC-DC).

### 11.4 Status light
| Light | Meaning |
|---|---|
| Slow green breath | Healthy, nothing carried |
| Solid blue | Carrying messages |
| Blue flash | Handing over or receiving right now |
| Amber | Storage nearly full, or not seen a hub for 7 days (certificate renewal due) |
| Red | Fault: certificate expired or revoked, storage error, or power failing |

### 11.5 Environmental
- Sealed enclosures in direct sun can exceed 65 °C: measure in the H1 heat test and throttle or pause Wi-Fi bursts above a threshold.
- Vibration: secure the SD card and connectors; prefer soldered headers.

## 12. Emulator

### 12.1 Principle
The emulator drives the **real `NodeCore`** in a single process on a **discrete-event virtual clock**, so days of scenario run in seconds and every run can be reproduced from its seed. Only the hardware is virtual: radios, RTC, power/UPS, a software P-256 secure element, and seeded entropy. Simulated power cuts throw away a node's in-memory state and rebuild it from its storage file; true mid-write crashes are covered by a subprocess `SIGKILL` test.

**Goal for v0.2:** realistic enough to *size a pilot*: how many vehicles and relays a district needs, expected delivery times, storage and battery, with every assumption visible and replaceable by H1 measurements.

### 12.2 Fidelity model
| Area | v0.1 (built) | v0.2 (planned) |
|---|---|---|
| Wi-Fi link | Disc range, fixed 2 s setup, fixed 1.5 MB/s | Log-distance path loss with log-normal shadowing, **clutter from OSM building density** along the path, **line of sight from SRTM elevation**; throughput falls with distance; BLE discovery delay before Wi-Fi setup |
| Channel sharing | Each contact independent | Contacts in the same neighbourhood **share airtime** (one Wi-Fi channel per area) |
| LoRa | Disc range, airtime, duty cycle | Same path-loss and terrain model as Wi-Fi with LoRa link budget per spreading factor |
| Compute | Free | **Per-operation costs** for P-256 verify, Ed25519, XChaCha20, Merkle hashing, SQLCipher writes, erasure decode; per node type, from published Pi benchmarks first, replaced by H1 measurements |
| Storage | Capacity limit | Plus write throughput and a flash-wear counter |
| Mobility | Exact loops | **Schedules with noise**: departure jitter, traffic delays, skipped rounds, breakdowns; weekly patterns (night stops, market days); on-foot couriers with irregular paths |
| Power | Battery and solar curve | Per-variant power draw from §11.2, irradiance by month for the site |
| Updates | — | Spread and rollback of signed updates (§9.5) |

Every parameter lives in the scenario or a shared `simulation/assumptions.yaml` with its **source** (published figure, estimate or H1 measurement), and the run report lists the assumptions it used.

### 12.3 Pilot sizing
A batch runner sweeps scenario parameters across many seeds and reports with confidence intervals, for example:
- delivery time distribution vs. number of vans, relay count and placement;
- share of CRITICAL messages delivered within 2 h, 4 h, 6 h;
- peak storage per node type; battery days of autonomy per relay;
- traffic overhead (bytes moved per byte delivered).
Output: CSV plus charts in a Markdown report, so a partner can read "3 vans and 2 relays give 90 % of critical referrals within 4 h at Arusha volumes (assumed)".

### 12.4 Scenarios
`arusha` (real roads), `rural-clinics`, `disaster-response`, `island-medevac`, `stress`. Message volumes are estimates until a partner provides real ones; each scenario states its assumed volumes.

### 12.5 Output
Run report (delivery by class, latency, NOT-DELIVERED with reasons, storage peaks, overhead, ledger checks, assumptions used), the live map, and the journey view.

### 12.6 Calibration plan
Hardware phase H1 measures, on real Pis: link-up time, throughput vs. distance, LoRa range around Arusha, per-operation compute costs, power draw, UPS hold-up, enclosure temperature. Each replaces the matching assumption, and the sizing results are re-run.

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
├── endpoint/                    # endpoint crypto reference implementation, FHIR-lite (§7.4, §6.4)
├── interface/                   # staff app, admin console, map (§10)
├── simulation/                  # emulator + scenarios (§12)
├── validation/                  # tests + trace_matrix.py (§13, §15)
├── tools/                       # offline map build, GIF recording, sizing runs
└── deploy/                      # systemd unit, node.toml examples, image build (H1)
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
| **1 — Simulation** (done) | Node core, endpoint crypto, emulator, PWA and map, all on one machine | `stress` scenario passes zero-silent-loss; full trace matrix coverage of P1 requirements |
| **1b — Pilot-grade emulation** | Fidelity model and pilot sizing (§12.2–12.3) | Sizing report for Arusha with every assumption sourced |
| **1c — Staff app, consoles, node software** | Staff app (§10.1–10.5), admin console (§10.6), daemon design, provisioning and signed updates in emulation (§9.3–9.5) | Clinic-to-hospital referral, triage and results end to end in the emulator, in English and Swahili |
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
| Cap each peer below *k* shards of a bundle | **No cap** (P4.S12 dropped) | Mules copy rather than move shards; a cap would stop one mule completing a delivery and raise the NOT DELIVERED risk. |
| Repo name `dtn-mesh-medical` | `dtn-medical-mesh` | Matches the actual repository. |
| No updates in the field | **Signed software updates over the mesh**, release key offline, A/B rollback (v0.2) | A district-wide pilot cannot rely on site visits to patch nodes. |
| Crew app | **Status light only** (v0.2) | Crews should have nothing to do. |
| Disc radio model (fixed range, setup, rate) | **Path loss + shadowing, BLE discovery, shared airtime, terrain and clutter, LoRa link budget per SF** (P8) | Defaults calibrated to the old ~100 m / ~2 km ranges; scenarios now set a path-loss exponent instead of a range. |
| Free compute, exact vehicle loops | **Per-operation CPU/storage time; noisy schedules (night stops, breakdowns); on-foot couriers** (P8) | Arusha vans now park 20:00-06:00: 82/93 delivered in 24 h (was 89/93), zero silent loss. |
| Arusha relay power | **10 W panel, 77 Wh LiFePO4** per §11.1 (P8) | The earlier 6 W / 30 Wh was below the spec's own hardware. |

## 18. Open questions
1. ~~AEAD choice~~ (decided: XChaCha20-Poly1305).
2. PRoPHET parameters (P_init, β, γ, ageing unit) and the warm-up threshold *N*: tune in Phase 1.
3. ~~Pseudonym epoch length~~ (pseudonyms dropped in favour of trial decryption, §7.5).
4. Size-bucket boundaries for large media. (Decided: payloads over 32 MB are rejected at send.)
5. BLE rotating token scheme details and its privacy properties.
6. Endpoint key recovery when a clinic laptop is lost (re-provision at a hub? escrow?).
7. Whether relays should drop **TELEMETRY** entirely when storage is under pressure.
8. Pilot jurisdiction: Tanzania (Arusha) is the working assumption. To confirm with a partner: the permitted LoRa band and duty cycle, the data-protection obligations under Tanzanian law, and the clinical safety process the partner follows.
9. Real vehicle schedules and message volumes for the pilot district (all current figures are estimates).
10. Site irradiance data for sizing solar relays (§11.2).
11. PIN policy details (length, lockout, recovery) agreed with the partner's information governance lead.
