"""SIGKILL a writer at random moments; the store must reopen consistent with every commit.

This proves crash atomicity (REQ-DUR-03). A killed process leaves the OS page cache intact, so
true power-loss durability also depends on fsync (synchronous=FULL, REQ-DUR-04), which only the
hardware bench (Phase H1) can test.
"""

import hashlib
import os
import random
import signal
import time
from pathlib import Path

import pytest

from node.events import Store
from node.storage import Storage

KILLS = 500


def value_for(key: bytes, size: int) -> bytes:
    digest = hashlib.sha256(key).digest()
    return (digest * (size // 32 + 1))[:size]


def writer(db: Path, start: int, report_fd: int) -> None:
    rng = random.Random(start)
    storage = Storage(db)
    i = start
    while True:
        key = i.to_bytes(4)
        storage.apply(Store(table="shards", key=key, value=value_for(key, rng.randint(1, 20_000))))
        os.write(report_fd, key)  # only after the commit returned
        i += 1


@pytest.mark.req("REQ-DUR-03")
def test_store_survives_random_sigkills(tmp_path: Path) -> None:
    db = tmp_path / "node.db"
    rng = random.Random(1)
    committed: set[bytes] = set()
    next_key = 0
    for _ in range(KILLS):
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:  # child: write until killed
            os.close(read_fd)
            try:
                writer(db, next_key, write_fd)
            finally:
                os._exit(1)
        os.close(write_fd)
        time.sleep(rng.uniform(0, 0.02))
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        reported = b""
        while chunk := os.read(read_fd, 65536):
            reported += chunk
        os.close(read_fd)
        committed |= {reported[i : i + 4] for i in range(0, len(reported) - len(reported) % 4, 4)}

        storage = Storage(db)
        assert storage._conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        rows = storage.load().get("shards", {})
        storage.close()
        assert committed <= rows.keys(), "a committed record was lost"
        for key, value in rows.items():
            assert value == value_for(key, len(value)), "torn record"
        next_key = max(int.from_bytes(k) for k in rows) + 1 if rows else 0
    assert len(committed) > KILLS  # the test really wrote between kills
