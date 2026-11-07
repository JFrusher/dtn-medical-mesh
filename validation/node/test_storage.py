from pathlib import Path

import pytest

from node.events import Delete, Store
from node.storage import Storage


def test_store_load_delete_survive_reopen(tmp_path: Path) -> None:
    db = tmp_path / "node.db"
    s = Storage(db)
    s.apply(Store(table="shards", key=b"a", value=b"1"))
    s.apply(Store(table="shards", key=b"b", value=b"2"))
    s.apply(Store(table="acks", key=b"a", value=b""))
    s.apply(Store(table="shards", key=b"a", value=b"1b"))
    s.apply(Delete(table="shards", key=b"b"))
    s.close()

    s = Storage(db)
    assert s.load() == {"shards": {b"a": b"1b"}, "acks": {b"a": b""}}
    s.close()


@pytest.mark.req("REQ-DUR-04")
def test_wal_and_full_sync(tmp_path: Path) -> None:
    s = Storage(tmp_path / "node.db")
    assert s._conn.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    assert s._conn.execute("PRAGMA synchronous").fetchone() == (2,)  # FULL
    s.close()
