# Data Protection Impact Assessment: template

Pre-filled with what the system does. The **data controller** (the pilot partner) completes the bracketed sections and signs it off before any real patient data is used (UK/EU GDPR Art. 35; special-category health data under Art. 9).

## 1. Processing

| | |
|---|---|
| Controller | [Partner organisation] |
| Purpose | Moving clinical messages (referrals, results, vitals, images) between registered health sites without internet connectivity |
| Lawful basis | [e.g. Art. 6(1)(e) public task with Art. 9(2)(h) provision of health care] |
| Data subjects | Patients referred between participating sites; staff who author messages |
| Data | Free text, vitals, images and documents chosen by clinicians; a local patient identifier if the clinician adds one |
| Retention | Laptops: [partner policy]. Nodes: ciphertext only, deleted on delivery ACK or TTL (at most 72 h by default) |
| Recipients | Only the addressed endpoint can decrypt. Couriers (vehicles, relays) carry ciphertext they cannot read |

## 2. Data-flow map

```
Clinician ─► Laptop (plaintext) ─► Site node ─► Mules / relays ─► Hub / site node ─► Laptop (plaintext) ─► Clinician
              encrypt + sign       ciphertext    ciphertext only     ciphertext        decrypt + verify
              (full-disk enc.)     (encrypted    (encrypted at rest, (encrypted
                                    at rest)      no keys)            at rest)
```

| Where | What is present | Protection |
|---|---|---|
| Laptops | Plaintext messages, endpoint keys | OS full-disk encryption and screen lock (deployment requirement); partner access control |
| All nodes | Ciphertext shards; routing headers (source/destination service area, priority, size bucket, timestamps) | SQLCipher store keyed by the secure element; no clinical keys |
| Radio | Same as nodes | Only authenticated peers exchange data; LoRa carries control messages only |

## 3. Risks and measures

| Risk | Measure | Residual |
|---|---|---|
| Node stolen | Holds only ciphertext; storage encrypted; certificate revocable and expiring after 14 days | Low |
| Laptop stolen | Full-disk encryption and screen lock: **controller to enforce** | [assess] |
| Traffic analysis of headers | No patient or endpoint identity in headers; sizes padded to buckets; service-area routing | **Medium for single-clinic areas**: an observer can tell that a message of a given priority went to that clinic. Consider grouping small clinics into one area |
| Message misdirected | Recipient chosen from a hub-signed directory; only that endpoint can decrypt | Low |
| Retention on nodes beyond purpose | Shards purged on delivery ACK or TTL | Low |
| Data subject rights (access, erasure) | Plaintext exists only on laptops; handled under the controller's record-keeping | [assess] |

## 4. Consultation and sign-off

| Role | Name | Date | Decision |
|---|---|---|---|
| Data Protection Officer | | | |
| Clinical Safety Officer | | | |
| Information Governance lead | | | |
