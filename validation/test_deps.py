"""Smoke test: every runtime dependency imports, and SQLCipher really encrypts."""

from pathlib import Path

import cbor2
import fastapi
import nacl.bindings
import uvicorn
import yaml
import zfec
from cryptography.hazmat.primitives.asymmetric import ec
from sqlcipher3 import dbapi2 as sqlcipher


def test_imports() -> None:
    for mod in (cbor2, fastapi, uvicorn, yaml, zfec, nacl.bindings):
        assert mod is not None
    ec.generate_private_key(ec.SECP256R1())
    assert hasattr(nacl.bindings, "crypto_aead_xchacha20poly1305_ietf_encrypt")


def test_sqlcipher_encrypts_file(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    conn = sqlcipher.connect(str(db_path))
    conn.execute("PRAGMA key = 'test-key'")
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.execute("INSERT INTO t VALUES ('PLAINTEXT_MARKER')")
    conn.commit()
    conn.close()
    assert b"PLAINTEXT_MARKER" not in db_path.read_bytes()
    assert b"SQLite format 3" not in db_path.read_bytes()

    reopened = sqlcipher.connect(str(db_path))
    reopened.execute("PRAGMA key = 'test-key'")
    assert reopened.execute("SELECT v FROM t").fetchone() == ("PLAINTEXT_MARKER",)
    reopened.close()
