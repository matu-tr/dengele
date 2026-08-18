"""Content comparison, used only when metadata cannot decide.

BLAKE2b comes from the standard library, which keeps the packaged app free of
a compiled dependency. Speed barely matters here: hashing only runs to break a
tie between two versions that both changed, not on every file of every run —
the predecessor's habit of SHA-256'ing both sides of every file was what made
it slow.
"""

from __future__ import annotations

from pathlib import Path

CHUNK = 1024 * 1024


def hash_file(path: Path) -> str:
    """Hex BLAKE2b digest of a file's contents."""
    import hashlib

    digest = hashlib.blake2b()
    with open(path, "rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def files_equal(a: Path, b: Path) -> bool:
    """Whether two files hold identical bytes, short-circuiting on size."""
    if a.stat().st_size != b.stat().st_size:
        return False
    return hash_file(a) == hash_file(b)
