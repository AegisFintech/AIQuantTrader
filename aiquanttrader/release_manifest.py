from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EA_SOURCE = Path("broker") / "mt5" / "AIQuantTraderBridgeEA.mq5"
DEFAULT_MANIFEST = Path("state") / "mt5" / "RELEASE.json"
DEFAULT_EA_MANIFEST = Path("state") / "mt5" / "EA_MANIFEST.txt"


def build_manifest(repo_root: Path = ROOT) -> dict:
    """Inspect the repo and return release metadata for the warehouse and EA."""
    repo_root = Path(repo_root)
    mq5_path = repo_root / EA_SOURCE
    git_sha = _git(repo_root, "rev-parse", "HEAD") or None
    git_short = _git(repo_root, "rev-parse", "--short", "HEAD") or None
    git_dirty = bool(_git(repo_root, "status", "--porcelain")) if git_sha else False
    config_inputs = _parse_ea_inputs(mq5_path)
    return {
        "schema_version": 1,
        "generated_at": int(time.time()),
        "git_sha": git_sha,
        "git_short": git_short,
        "git_dirty": git_dirty,
        "ea_version": _parse_ea_version(mq5_path),
        "ea_source_path": str(EA_SOURCE),
        "config_inputs": config_inputs,
        "managed_symbols": _managed_symbols(config_inputs.get("AutoSymbols")),
        "python_version": sys.version,
    }


def write_manifest(repo_root: Path = ROOT, dest: Path | None = None) -> Path:
    """Build the manifest and write it to state/mt5/RELEASE.json by default."""
    repo_root = Path(repo_root)
    manifest = build_manifest(repo_root)
    path = _dest(repo_root, dest, DEFAULT_MANIFEST)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return path


def write_ea_manifest(repo_root: Path = ROOT, dest: Path | None = None) -> Path:
    """Build and write the compact key=value manifest consumed by the EA."""
    repo_root = Path(repo_root)
    manifest = build_manifest(repo_root)
    path = _dest(repo_root, dest, DEFAULT_EA_MANIFEST)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"schema_version={manifest['schema_version']}",
        f"ea_version={manifest['ea_version']}",
        f"git_sha={manifest.get('git_sha') or ''}",
        f"git_short={manifest.get('git_short') or ''}",
        f"generated_at={manifest['generated_at']}",
        f"git_dirty={1 if manifest.get('git_dirty') else 0}",
    ]
    path.write_text("\n".join(lines) + "\n")
    return path


def load_release_manifest(path: Path | None = None) -> dict:
    """Read state/mt5/RELEASE.json if it exists; return an empty dict otherwise."""
    manifest_path = path or (ROOT / DEFAULT_MANIFEST)
    if not manifest_path.exists() or not manifest_path.stat().st_size:
        return {}
    try:
        return json.loads(manifest_path.read_text())
    except Exception:
        return {}


def _git(repo_root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except Exception:
        return ""
    return result.stdout.strip()


def _parse_ea_version(mq5_path: Path) -> str:
    if not mq5_path.exists():
        return "unknown"
    match = re.search(r'#property\s+version\s+"([^"]+)"', mq5_path.read_text(errors="replace"))
    return match.group(1) if match else "unknown"


def _parse_ea_inputs(mq5_path: Path) -> dict[str, str]:
    if not mq5_path.exists():
        return {}
    inputs: dict[str, str] = {}
    input_lines = 0
    pattern = re.compile(r"^\s*input\s+.+?\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?);")
    for raw_line in mq5_path.read_text(errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue
        if not line.startswith("input "):
            continue
        input_lines += 1
        if input_lines > 30:
            break
        match = pattern.match(line)
        if not match:
            continue
        name, default = match.groups()
        inputs[name] = _clean_default(default)
    return inputs


def _clean_default(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def _managed_symbols(value: Any) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _dest(repo_root: Path, dest: Path | None, default: Path) -> Path:
    if dest is None:
        return repo_root / default
    dest = Path(dest)
    return dest if dest.is_absolute() else repo_root / dest
