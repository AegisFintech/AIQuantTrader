"""Canonical serialization primitives for immutable artifacts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict


class DomainModel(BaseModel):
    """Immutable schema model with unknown-key rejection."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


CanonicalValue = Mapping[str, Any] | Sequence[Any] | str | int | float | bool | None


def canonical_json_bytes(value: CanonicalValue) -> bytes:
    """Serialize JSON deterministically for hashing and signatures."""

    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: CanonicalValue) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()
