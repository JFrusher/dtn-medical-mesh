from pathlib import Path

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from node.events import Store
from node.storage import STORAGE_KEY_SLOT, Storage
from simulation.virtual_hw import SeededEntropy, SoftwareSecureElement

MARKER = b"SHARD-PAYLOAD-MARKER" * 50


@pytest.mark.req("REQ-SEC-11")
def test_file_on_disk_reveals_nothing(tmp_path: Path) -> None:
    key = SoftwareSecureElement(SeededEntropy("n")).read_slot(STORAGE_KEY_SLOT)
    db = tmp_path / "node.db"
    s = Storage(db, key)
    s.apply(Store(table="shards", key=b"id-1", value=MARKER))
    s.close()
    raw = b"".join(p.read_bytes() for p in tmp_path.iterdir())  # db + any WAL/SHM files
    assert MARKER[:20] not in raw and b"shards" not in raw and b"SQLite format 3" not in raw
    assert Storage(db, key).load() == {"shards": {b"id-1": MARKER}}


@pytest.mark.req("REQ-SEC-11")
def test_wrong_key_fails_loudly(tmp_path: Path) -> None:
    Storage(tmp_path / "node.db", b"a" * 32).close()
    with pytest.raises(sqlcipher.DatabaseError):
        Storage(tmp_path / "node.db", b"b" * 32)
