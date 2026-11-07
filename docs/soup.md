# Software of unknown provenance (SOUP)

Third-party runtime software (IEC 62304 §8.1.2). Versions are the locked ones in `uv.lock`; `validation/test_docs.py` fails if this list and the lock file disagree. Development-only tools (pytest, ruff, mypy, hypothesis, playwright, httpx) are not part of the product and are not listed.

| Package | Version | Purpose | Safety relevance | Failure we guard against |
|---|---|---|---|---|
| cryptography | 50.0.2 | Ed25519, X25519, P-256 ECDSA, HKDF | High: integrity, authenticity, confidentiality | Signature or key-agreement misuse: wrapped in `endpoint/crypto.py`, `node/crypto.py`; tested for tampering and forgery |
| pynacl | 1.6.2 | XChaCha20-Poly1305 AEAD (libsodium) | High: confidentiality and integrity of clinical content | Decryption of altered data: every bit flip rejected in tests |
| zfec | 1.6.0.0 | Reed-Solomon erasure coding | High: data recovery | Wrong reconstruction: property test decodes from every *k*-subset; Merkle check on every shard |
| sqlcipher3 | 0.6.5 | Encrypted SQLite storage on nodes | High: durability, confidentiality at rest | Lost commits, plaintext on disk: SIGKILL crash test; raw-file inspection test |
| cbor2 | 6.2.1 | Wire and storage encoding | Medium: parsing untrusted input | Malformed input crashing a node: strict decoders, exhaustive bit-flip fuzz test |
| fastapi | 0.162.2 | Local endpoint API and UI server | Low: laptop-local only | Mis-served data: API tests |
| uvicorn | 0.54.0 | ASGI server for the local UI | Low | — |
| websockets | 17.4 | Live map stream | Low: display only | — |
| pyyaml | 6.0.3 | Emulator scenario files | None in the field (simulation only) | Unsafe loading: `safe_load` only |
