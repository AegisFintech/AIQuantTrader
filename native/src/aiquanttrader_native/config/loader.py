"""Deterministic TOML and environment configuration loading."""

from __future__ import annotations

import copy
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from aiquanttrader_native.config.models import NativeSettings

ENV_PREFIX = "AQT_NATIVE__"
ENVIRONMENT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
MAX_CONFIG_BYTES = 1_048_576


class ConfigLoadError(ValueError):
    """Raised when configuration sources cannot be loaded safely."""


@dataclass(frozen=True, slots=True)
class ConfigBundle:
    settings: NativeSettings
    sources: tuple[Path, ...]
    fingerprint: str


def load_config(
    config_dir: Path,
    environment: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> ConfigBundle:
    """Load base and environment TOML, then apply explicit native overrides."""

    if not ENVIRONMENT_PATTERN.fullmatch(environment):
        raise ConfigLoadError("environment contains unsupported characters")

    root = config_dir.resolve(strict=True)
    base_path = _safe_config_path(root, "base.toml")
    environment_path = _safe_config_path(root, f"{environment}.toml")
    payload = _deep_merge(_read_toml(base_path), _read_toml(environment_path))
    _apply_environment(payload, os.environ if environ is None else environ)

    try:
        settings = NativeSettings.model_validate(payload)
    except ValidationError as exc:
        raise ConfigLoadError(str(exc)) from exc
    if settings.environment != environment:
        raise ConfigLoadError(
            f"environment overlay declares {settings.environment!r}, expected {environment!r}"
        )
    return ConfigBundle(
        settings=settings,
        sources=(base_path, environment_path),
        fingerprint=settings.fingerprint(),
    )


def _safe_config_path(root: Path, filename: str) -> Path:
    candidate = root / filename
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ConfigLoadError(f"required config file is missing: {candidate}") from exc
    if not resolved.is_relative_to(root):
        raise ConfigLoadError(f"config file escapes the configuration root: {candidate}")
    if not resolved.is_file():
        raise ConfigLoadError(f"config source is not a regular file: {candidate}")
    if resolved.stat().st_size > MAX_CONFIG_BYTES:
        raise ConfigLoadError(f"config source exceeds {MAX_CONFIG_BYTES} bytes: {candidate}")
    return resolved


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            value = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigLoadError(f"invalid TOML in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigLoadError(f"config source must contain a table: {path}")
    return value


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            result[key] = _deep_merge(current, value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _apply_environment(payload: dict[str, Any], environ: Mapping[str, str]) -> None:
    for name in sorted(environ):
        if not name.startswith(ENV_PREFIX):
            continue
        suffix = name.removeprefix(ENV_PREFIX)
        parts = [part.lower() for part in suffix.split("__")]
        if not parts or any(not part for part in parts):
            raise ConfigLoadError(f"invalid environment override name: {name}")
        _deep_set(payload, parts, _parse_override(environ[name]))


def _deep_set(payload: dict[str, Any], parts: list[str], value: Any) -> None:
    cursor = payload
    for part in parts[:-1]:
        existing = cursor.setdefault(part, {})
        if not isinstance(existing, dict):
            raise ConfigLoadError(f"environment override crosses a scalar at {part!r}")
        cursor = existing
    cursor[parts[-1]] = value


def _parse_override(raw: str) -> Any:
    normalized = raw.strip()
    if normalized.lower() in {"none", "null"}:
        return None
    if normalized.lower() == "true":
        return True
    if normalized.lower() == "false":
        return False
    if re.fullmatch(r"-?(0|[1-9][0-9]*)", normalized):
        return int(normalized)
    if normalized.startswith("[") or normalized.startswith("{"):
        try:
            return tomllib.loads(f"value = {normalized}")["value"]
        except tomllib.TOMLDecodeError as exc:
            raise ConfigLoadError(f"invalid structured environment value: {raw!r}") from exc
    return raw
