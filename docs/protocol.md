# Protocol

Detail of spec §6–§8 as implemented in `node/`. All frames are CBOR maps with a `"t"` field. Every field from a peer is untrusted: decoders check types and ranges, and a frame that fails any check is dropped and counted in `NodeCore.dropped`, never raised.

## Objects

| Object | Module | Signed by | Notes |
|---|---|---|---|
| Header | `node/bundle.py` | Injecting site node (P-256) | `bundle_id = SHA-256(merkle_root ‖ created)`; no endpoint or patient identity; ranges checked |
| Shard | `node/bundle.py` | via header | `{header, index, data, proof}`; verifies alone against the header's Merkle root |
| Ack | `node/bundle.py` | Site or hub of `dst_sa` | Accepted only if `dst_sa` matches the signed header of any held shard |
| Receipt | `node/bundle.py` | Receiver | Batches shard IDs; `prev` hash-chains the receiver's ledger |
| Cert | `node/pki.py` | Root → hubs → other nodes | Hubs 1 year, others 14 days; renewed at a hub |
| HubDoc | `node/pki.py` | A hub | `crl` (revocation list) or `dir` (endpoint directory); higher `seq` replaces lower |

## Frames

| `t` | Radio | Content | When sent |
|---|---|---|---|
| `summary` | Wi-Fi | Bloom filter of held shard and ACK IDs, cert IDs, doc versions, PRoPHET table, sender cert (+ issuer), recipient, HLC, signature | First frame of every contact; authenticates the peer |
| `exact` | Wi-Fi | Exact held shard and ACK IDs | 2 s into a contact, to catch Bloom false positives |
| `cert` | Wi-Fi | One certificate | Before anything that needs it; every cert the peer lacks |
| `doc` | Wi-Fi, LoRa | One hub document | When the peer has an older version, or on publication (LoRa) |
| `ack` | Wi-Fi, LoRa | One delivery ACK | When the peer lacks it, or on creation (LoRa) |
| `shard` | Wi-Fi | One shard plus its carried Bundle Age | When the peer lacks it and routing says forward |
| `receipt` | Wi-Fi, LoRa | One custody receipt | After a 0.5 s batch, at contact end, on power loss, or owed at the next meeting |
| `renew` | Wi-Fi | A fresh certificate for the peer | From a hub when the peer's cert is over a day old |

LoRa accepts only `ack`, `receipt` and `doc`, at most 200 bytes, within the regional duty cycle.

## A contact, step by step

1. Link up → each side sends a signed `summary`.
2. On a valid summary (cert chain valid at HLC time, names this peer and is addressed to us, fresh within 1 h unless our RTC is down), the receiver merges the peer's HLC, updates PRoPHET, pays any owed receipts, renews the cert if it is a hub, then **offers**: certs, newer docs, ACKs, then shards ordered by priority, closeness to *k* at a destination peer, size, age.
3. Each received shard is checked (format, Merkle, header signature, TTL, rate limit), stored in one transaction, then receipted in a batch.
4. 2 s later each side sends `exact`; anything hidden by a Bloom false positive follows.
5. Link down → anything still unreceipted is signed into the outbox. Frames still on the air are lost; nothing depends on them arriving.

## Deletion

| Who | Deletes a shard when |
|---|---|
| Origin site node | Delivery ACK only |
| Destination site / hub | Delivery ACK only |
| Relay / mule | Delivery ACK; or custody receipt from a peer in the destination area or with better PRoPHET predictability; or TTL expiry |
| Anyone, full | Evicts expired, then strictly lower classes (TELEMETRY first, oldest first), never origin copies; otherwise refuses (no receipt, so the sender keeps it) |

## Records on disk (`node/core.py` tables)

`shards`, `acks`, `origin`, `delivered`, `certs`, `docs`, `identity`, `meta` (HLC, PRoPHET), `ledger`, `receipt_outbox`, `receipts_in`. The core rebuilds all state from these at boot; anything only in RAM (open contacts, timers) is lost on power loss, by design.
