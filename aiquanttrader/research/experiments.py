"""JSON sidecar storage for research experiment records."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, fields, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXPERIMENT_DIR = ROOT / "state" / "research" / "experiments"


@dataclass(frozen=True)
class ExperimentRecord:
    """Tracked metadata and results for one research run."""

    run_id: str
    strategy_name: str
    symbol: str
    created_at: str
    git_sha: str
    data_hash: str
    config: dict[str, Any]
    walk_forward_config: dict[str, Any]
    backtest_config: dict[str, Any]
    fold_results: list[dict[str, Any]]
    aggregated_metrics: dict[str, Any]
    walk_forward_stability: dict[str, Any]
    verdict: dict[str, Any]
    notes: str = ""
    promotion_decision: str = "pending"


def experiment_path(run_id: str, *, root: Path | None = None) -> Path:
    """Return the JSON sidecar path for ``run_id``."""

    directory = Path(root) if root is not None else DEFAULT_EXPERIMENT_DIR
    return directory / f"{run_id}.json"


def save_experiment(
    record: ExperimentRecord,
    *,
    root: Path | None = None,
) -> Path:
    """Write ``record`` as JSON and return its sidecar path."""

    path = experiment_path(record.run_id, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json_safe(record), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def load_experiment(
    run_id: str,
    *,
    root: Path | None = None,
) -> ExperimentRecord:
    """Load one experiment JSON sidecar."""

    payload = json.loads(experiment_path(run_id, root=root).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("experiment JSON must contain an object")
    field_names = {field.name for field in fields(ExperimentRecord)}
    filtered = {key: payload[key] for key in payload if key in field_names}
    return ExperimentRecord(**filtered)


def list_experiments(*, root: Path | None = None) -> list[str]:
    """Return run IDs found in the experiment directory."""

    directory = Path(root) if root is not None else DEFAULT_EXPERIMENT_DIR
    if not directory.exists():
        return []
    return sorted(path.stem for path in directory.glob("*.json") if path.is_file())


def git_sha() -> str:
    """Return the current repository SHA, or an empty string on failure."""

    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def file_hash(path: Path) -> str:
    """Return the SHA-256 hash of a file."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now_iso() -> str:
    """Return an ISO-8601 UTC timestamp for experiment records."""

    return datetime.now(timezone.utc).isoformat()


def _json_safe(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "__dict__") and value.__class__.__module__.startswith("aiquanttrader."):
        return _json_safe(vars(value))
    return value
