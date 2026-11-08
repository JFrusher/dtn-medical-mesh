# Hazard log

Structured in the style of ISO 14971 so a clinical partner can adopt and extend it. Severity and probability are **provisional engineering estimates** pending review by a Clinical Safety Officer (spec §13). Each control names the requirement that implements it; `validation/trace_matrix.py` shows the tests behind each requirement.

Scales: **Severity** 1 negligible · 2 minor · 3 serious · 4 critical. **Probability** (before controls) 1 rare · 2 unlikely · 3 possible · 4 likely.

| ID | Hazard | Cause | Sev | Prob | Controls | Residual risk / notes |
|---|---|---|---|---|---|---|
| HAZ-01 | Clinician believes a message was delivered when it was not | Forged or misrouted delivery ACK | 4 | 2 | ACKs signed by a site or hub of the destination area, checked against the signed header (REQ-PROTO-06, REQ-SEC-04); invariant checker catches a false ACK in emulation (REQ-SAFE-01) | Low. A compromised **destination** site could still sign false ACKs; mitigated by revocation (REQ-SEC-07). |
| HAZ-02 | Message silently never arrives | Node theft, storage loss, link failure, eviction | 4 | 3 | Origin keeps its copy until the delivery ACK (REQ-PROTO-05); NOT DELIVERED alert at TTL (REQ-SAFE-01); erasure coding (REQ-DUR-02); crash-safe storage (REQ-DUR-03, REQ-DUR-04); CRITICAL never evicted by lower classes (REQ-DUR-05) | Low. Staff must act on NOT DELIVERED; training needed. |
| HAZ-03 | Delay mistaken for timely care | System used for emergencies | 4 | 3 | Intended-use exclusion in UI and README (REQ-SAFE-04); per-message "timing not guaranteed" and NOT DELIVERED deadline (REQ-SAFE-03) | **Medium.** Depends on training and an emergency channel existing in parallel. |
| HAZ-04 | Message opened by the wrong person | Wrong recipient selected; stolen node or laptop | 4 | 2 | End-to-end encryption, no node holds keys (REQ-SEC-01, REQ-SEC-02); signed directory for recipient keys; storage encrypted at rest (REQ-SEC-11) | Low for nodes. A stolen, unlocked **laptop** exposes its inbox: full-disk encryption and screen lock are deployment requirements. |
| HAZ-05 | Clinical content altered in transit | Corruption, bit rot, malicious relay | 4 | 2 | Merkle-verified shards (REQ-DUR-01); signed headers (REQ-SEC-04); inner author signature (REQ-SEC-03) | Low. |
| HAZ-06 | Message attributed to the wrong author | Forged sender | 3 | 2 | Inner Ed25519 signature resolved through the hub-signed directory; unknown signers shown as UNVERIFIED SENDER (REQ-SEC-03) | Low. |
| HAZ-07 | Urgent messages starved by bulk data | Large media filling storage or contact time | 3 | 3 | Priority-ordered transfer (REQ-PROTO-03) and eviction (REQ-DUR-05); 32 MB cap at send (REQ-SEC-10) | Low. |
| HAZ-08 | Expired or stale bundle acted upon | Clock reset revives an old bundle | 2 | 2 | HLC plus Bundle Age, stricter wins (REQ-TIME-01, REQ-TIME-02) | Low. |
| HAZ-09 | Mesh flooded, real messages dropped | Rogue or compromised injector | 3 | 2 | Only certified site/hub nodes inject (REQ-SEC-04, REQ-SEC-06); per-injector rate limit (REQ-SEC-12); revocation (REQ-SEC-07) | Low. |
| HAZ-10 | Metadata reveals patient information | Traffic analysis of headers | 3 | 2 | No identities in headers, service-area routing, size buckets (REQ-SEC-09, REQ-SEC-10) | Medium for very small areas (one clinic = one area): an observer learns that *some* message went there. Recorded in the DPIA. |
| HAZ-11 | Test or demo randomness used in production | Seeded entropy reachable from production code | 4 | 1 | Import-boundary test (REQ-SEC-13) | Low. |

## Open items for the clinical partner
- Confirm severities and probabilities with a Clinical Safety Officer.
- Define the parallel emergency channel and the response to NOT DELIVERED.
- Add hazards for the partner's specific workflow (for example result routing to the wrong ward).
