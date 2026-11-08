"""Durable, encrypted record store for one node (spec §9.2, §7.7).

NodeCore decides what to keep; this module only applies its Store/Delete actions and
loads everything back on boot. Each action is its own transaction (REQ-DUR-03).
The file is SQLCipher-encrypted with a key from the secure element (REQ-SEC-11).
"""

from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlcipher3 import dbapi2 as sqlcipher

from node.events import Delete, Store

STORAGE_KEY_SLOT = 0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    tbl   TEXT NOT NULL,
    key   BLOB NOT NULL,
    value BLOB NOT NULL,
    PRIMARY KEY (tbl, key)
) WITHOUT ROWID
"""


class Storage:
    def __init__(self, path: Path, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("storage key must be 32 bytes")
        # One thread at a time uses a node's store (the emulator and daemon are single-threaded);
        # it may not be the thread that opened it (e.g. a test client's event-loop thread).
        self._conn = sqlcipher.connect(str(path), isolation_level=None, check_same_thread=False)
        self._conn.execute(f"PRAGMA key = \"x'{key.hex()}'\"")  # must precede any other statement
        mode = self._conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]
        if mode != "wal":
            raise RuntimeError(f"could not enable WAL (got {mode!r})")
        self._conn.execute("PRAGMA synchronous = FULL")
        self._conn.execute(_SCHEMA)

    def apply(self, action: Store | Delete) -> None:
        with self._transaction():
            if isinstance(action, Store):
                self._conn.execute(
                    "INSERT OR REPLACE INTO records VALUES (?, ?, ?)",
                    (action.table, action.key, action.value),
                )
            else:
                self._conn.execute(
                    "DELETE FROM records WHERE tbl = ? AND key = ?", (action.table, action.key)
                )

    def load(self) -> dict[str, dict[bytes, bytes]]:
        tables: dict[str, dict[bytes, bytes]] = defaultdict(dict)
        for tbl, key, value in self._conn.execute("SELECT tbl, key, value FROM records"):
            tables[tbl][key] = value
        return dict(tables)

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")
