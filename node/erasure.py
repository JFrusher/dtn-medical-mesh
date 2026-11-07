"""Reed-Solomon erasure coding via zfec (spec §6.3, REQ-DUR-02). Typed wrapper; no own GF maths."""

import zfec


def encode(data: bytes, k: int, m: int) -> list[bytes]:
    """Split data into k equal blocks and add m parity blocks; any k rebuild it."""
    if k < 1 or m < 0 or len(data) % k:
        raise ValueError(f"data length {len(data)} must be a positive multiple of k={k}")
    size = len(data) // k
    blocks = [data[i * size : (i + 1) * size] for i in range(k)]
    return [bytes(b) for b in zfec.Encoder(k, k + m).encode(blocks)]


def decode(shares: dict[int, bytes], k: int, m: int) -> bytes:
    """Rebuild the data from any k shares, keyed by share index (0 .. k+m-1)."""
    if len(shares) < k:
        raise ValueError(f"need {k} shares, have {len(shares)}")
    nums = sorted(shares)[:k]
    if nums[-1] >= k + m:
        raise ValueError("share index out of range")
    blocks = zfec.Decoder(k, k + m).decode([shares[n] for n in nums], nums)
    return b"".join(bytes(b) for b in blocks)
