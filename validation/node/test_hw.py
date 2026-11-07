from node.hw.entropy import Entropy, OsEntropy


def test_os_entropy() -> None:
    source: Entropy = OsEntropy()
    a, b = source.random_bytes(32), source.random_bytes(32)
    assert len(a) == 32 and a != b
