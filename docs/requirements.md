# Requirements

Each requirement has a stable ID. Tests cite IDs with `@pytest.mark.req("REQ-…")`. Citing an ID that is not listed here fails test collection. `validation/trace_matrix.py` (P7.S01) reports which requirements are covered by passing tests. That is **test coverage, not compliance** (spec §13).

Format: `REQ-<AREA>-<NN>`. Spec references are to `PROJECT_SPEC.md`.

## SAFE — Clinical safety

| ID | Requirement | Spec |
|---|---|---|
| REQ-SAFE-01 | Every injected bundle ends as *Delivered* (ACKed) or is visibly flagged *NOT DELIVERED* at its origin endpoint; no bundle is lost silently. | §3, §8.8 |
| REQ-SAFE-02 | Expired bundles are never automatically resent; staff decide. | §8.8 |
| REQ-SAFE-03 | Every message in the UI shows expected latency and a "no guarantee of timing" indicator. | §2.3, §10.2 |
| REQ-SAFE-04 | The UI and README state that the system is not for emergency or time-critical communication. | §2.3 |

## SEC — Security and privacy

| ID | Requirement | Spec |
|---|---|---|
| REQ-SEC-01 | Clinical payloads are encrypted at the endpoint with XChaCha20-Poly1305 under a per-bundle key wrapped to the recipient's X25519 key. | §7.4 |
| REQ-SEC-02 | No code in `node/` can encrypt or decrypt clinical payloads, or import endpoint crypto. | §7.4, §9.1 |
| REQ-SEC-03 | Clinical content is signed by the authoring endpoint (Ed25519) inside the ciphertext. | §7.3 |
| REQ-SEC-04 | Bundle headers are signed by the injecting site node (P-256, secure element); relays drop shards with missing, invalid, expired or revoked signatures. | §7.3, §7.6 |
| REQ-SEC-05 | Node signing keys cannot be exported from the secure element interface. | §7.2 |
| REQ-SEC-06 | Peers authenticate each other's certificates (validity, revocation) before exchanging shards. | §8.2 |
| REQ-SEC-07 | Hub-signed revocation lists spread over the mesh with top priority; revoked nodes' shards and receipts are rejected. | §7.6 |
| REQ-SEC-08 | Operational certificates are short-lived (default 14 days) and renewed only when docked at a hub. | §7.6 |
| REQ-SEC-09 | Headers carry no patient identifiers or endpoint identities; the recipient is implicit in the encryption (trial decryption); routing uses service-area IDs. | §6.1, §7.5 |
| REQ-SEC-10 | Ciphertext is padded to fixed size buckets. | §6.1 |
| REQ-SEC-11 | Node storage is encrypted at rest with a key held by the secure element. | §7.7 |
| REQ-SEC-12 | Per-injector rate limits stop one node flooding the mesh. | §7.1 |
| REQ-SEC-13 | Seeded (deterministic) entropy is unreachable from production code. | Plan |
| REQ-SEC-14 | Endpoint keys on the staff laptop are encrypted at rest under each staff member's PIN and the device secret; repeated wrong PINs lock sign-in, and a session locks after inactivity. | §10.2 |

## DUR — Integrity and durability

| ID | Requirement | Spec |
|---|---|---|
| REQ-DUR-01 | Every shard can be verified on its own (header signature + Merkle proof); unverifiable shards are discarded. | §6.2 |
| REQ-DUR-02 | A bundle can be rebuilt from any *k* of its *k + m* shards. | §6.3 |
| REQ-DUR-03 | Each received shard is committed in a single atomic transaction; a crash at any point leaves the store consistent and keeps every committed shard. | §8.4, §9.2 |
| REQ-DUR-04 | Storage uses WAL with `synchronous=FULL`. | §9.2 |
| REQ-DUR-05 | Eviction order: expired, TELEMETRY, ROUTINE (oldest first), URGENT; CRITICAL is never evicted for a lower class; a full node refuses new shards. | §6.3, §9.2 |
| REQ-DUR-06 | Erasure parity scales with priority class (defaults per §6.3). | §6.3 |

## PROTO — Protocol

| ID | Requirement | Spec |
|---|---|---|
| REQ-PROTO-01 | An exchange is safe if the link drops at any byte; no multi-round-trip handshake comes before data. | §8.1 |
| REQ-PROTO-02 | Summaries: Bloom filter first, exact lists while the link stays up; a Bloom false positive can delay but never lose a shard. | §8.3 |
| REQ-PROTO-03 | Transfer order within a contact follows §8.4. | §8.4 |
| REQ-PROTO-04 | Custody receipts are batched, signed by the receiver and hash-chained in an append-only ledger. | §8.5 |
| REQ-PROTO-05 | The origin site node deletes a bundle only on delivery ACK; relays delete only on receipt from a better peer, delivery ACK or TTL expiry. | §8.5 |
| REQ-PROTO-06 | Delivery ACKs are signed, spread with top priority, and purge the bundle at every node that sees them. | §8.7 |
| REQ-PROTO-07 | Routing is epidemic until warm, then PRoPHET keyed on service area. | §8.6 |
| REQ-PROTO-08 | Retired: per-peer shard cap dropped (spec §8.6, §17). Kept so IDs stay stable. | §8.6 |
| REQ-PROTO-09 | The LoRa channel carries only control messages that fit one frame, never shards. | §8.10 |
| REQ-PROTO-10 | Traffic is bidirectional between endpoints. | §5.2 |

## TIME — Time

| ID | Requirement | Spec |
|---|---|---|
| REQ-TIME-01 | Nodes order events with a Hybrid Logical Clock that stays monotonic across RTC resets. | §8.9 |
| REQ-TIME-02 | TTL is enforced as the stricter of Bundle Age and creation HLC, so clock faults can neither revive nor prematurely expire a bundle. | §8.9 |

## SIM — Emulation

| ID | Requirement | Spec |
|---|---|---|
| REQ-SIM-01 | The emulator drives the same `NodeCore` code that runs on hardware. | §12.1 |
| REQ-SIM-02 | A scenario run is fully reproducible from its seed. | §12.3 |
| REQ-SIM-03 | The emulator can inject every pressure listed in §12.2. | §12.2 |

## APP — Staff app

| ID | Requirement | Spec |
|---|---|---|
| REQ-APP-01 | Every user-facing string comes from a translation catalogue; staff can switch language, and an unreviewed translation is flagged as a draft. | §10.1 |
| REQ-APP-02 | Each message records the staff member who wrote it inside the signed payload. | §10.2 |
| REQ-APP-03 | Patient identifiers inside a message are limited to name, age, sex and local ID. | §10.3 |
| REQ-APP-04 | A referral is a structured FHIR `ServiceRequest`; the hospital's Accept, Decline or Need information answer is itself a tracked message linked to it. | §10.3, §10.4 |
| REQ-APP-05 | The hospital's triage inbox lists arrivals by urgency, then oldest first, with how long each took to arrive. | §10.4 |
| REQ-APP-06 | Results travel back to the clinic as FHIR `DiagnosticReport` messages linked to the patient. | §10.5 |
