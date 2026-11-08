"""Pad ciphertext to fixed size buckets so size reveals little (spec §6.1, REQ-SEC-10).

Format: length (4 bytes, big-endian) || data || zero bytes up to the bucket size.
"""

BUCKETS = (4 * 1024, 64 * 1024, 1024 * 1024, 8 * 1024 * 1024, 32 * 1024 * 1024)
_LEN = 4


class PayloadTooLarge(ValueError):
    """Over the largest bucket: the sender must compress it or use another channel."""


def bucket_for(n: int) -> int:
    for bucket in BUCKETS:
        if n + _LEN <= bucket:
            return bucket
    raise PayloadTooLarge(f"{n} bytes is over the {BUCKETS[-1] // (1024 * 1024)} MB limit")


def pad(data: bytes) -> bytes:
    bucket = bucket_for(len(data))
    return len(data).to_bytes(_LEN) + data + bytes(bucket - _LEN - len(data))


def unpad(padded: bytes) -> bytes:
    n = int.from_bytes(padded[:_LEN])
    if len(padded) not in BUCKETS or n > len(padded) - _LEN:
        raise ValueError("not a padded payload")
    return padded[_LEN : _LEN + n]
