"""Crash-safe filesystem helpers for immutable market-data artifacts."""

from __future__ import annotations

import hashlib
import os
import secrets
from pathlib import Path


def sha256_file(path: Path, *, chunk_size: int = 1_048_576) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_bytes(path: Path, content: bytes, *, mode: int = 0o640) -> None:
    """Write, sync, and rename without replacing an immutable target."""

    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.{secrets.token_hex(8)}.partial")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb", buffering=0, closefd=True) as handle:
            handle.write(content)
            os.fsync(handle.fileno())
        if path.exists():
            raise FileExistsError(f"immutable artifact already exists: {path}")
        partial.rename(path)
        fsync_directory(path.parent)
    except BaseException:
        try:
            partial.unlink(missing_ok=True)
        finally:
            raise


def atomic_replace_bytes(path: Path, content: bytes, *, mode: int = 0o640) -> None:
    """Write and sync mutable state, then atomically replace its prior version."""

    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.{secrets.token_hex(8)}.partial")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb", buffering=0, closefd=True) as handle:
            handle.write(content)
            os.fsync(handle.fileno())
        partial.replace(path)
        fsync_directory(path.parent)
    except BaseException:
        try:
            partial.unlink(missing_ok=True)
        finally:
            raise
