"""Minimal secret-file handling that never exposes key material in representations."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

PRIVATE_KEY_PATTERN = re.compile(r"^(?:0x)?[0-9a-fA-F]{64}$")


class PrivateKey:
    """Opaque validated EVM private key."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not PRIVATE_KEY_PATTERN.fullmatch(value):
            raise ValueError("wallet secret must be a 32-byte hexadecimal private key")
        self._value = value if value.startswith("0x") else f"0x{value}"

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "PrivateKey(<redacted>)"

    __str__ = __repr__


def read_private_key(path: Path) -> PrivateKey:
    """Read one bounded regular file without following a final symlink."""

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("wallet secret must be a regular file")
        if metadata.st_size > 256:
            raise ValueError("wallet secret file is unexpectedly large")
        value = os.read(descriptor, 257).decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise ValueError("wallet secret must contain ASCII hexadecimal text") from exc
    finally:
        os.close(descriptor)
    return PrivateKey(value)
