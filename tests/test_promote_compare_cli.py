from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from aiquanttrader.data_store import connect
from aiquanttrader.prices import ingest_bars
from aiquanttrader.research.experiments import ExperimentRecord, save_experiment
from aiquanttrader.research.registry import index_experiment, init_registry


ROOT = Path(__file__).resolve().parents[1]


def test_promote_compare_cli_smoke(tmp_path):
    registry_path = tmp_path / "promotion.duckdb"
    with connect(registry_path) as con:
        ingest_bars(con, "XAUUSD", _bars(3600))

    proc = subprocess.run(
        [
            sys.executable,
            "scripts/promote_compare.py",
            "--incumbent-strategy",
            "XauAtrImpulse",
            "--challenger-strategy",
            "XauQuickMomentum",
            "--symbol",
            "XAUUSD",
            "--n-folds",
            "2",
            "--purge-bars",
            "10",
            "--embargo-bars",
            "10",
            "--report-id",
            "m4-smoke",
            "--output-dir",
            str(tmp_path),
            "--registry",
            str(registry_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    md_path = tmp_path / "m4-smoke.md"
    json_path = tmp_path / "m4-smoke.json"
    assert md_path.exists()
    assert json_path.exists()
    first_line = next(
        line for line in md_path.read_text(encoding="utf-8").splitlines() if line.strip()
    )
    assert first_line.startswith("verdict: ")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["report_id"] == "m4-smoke"

    with connect(registry_path) as con:
        rows = con.execute(
            "SELECT report_id, decision FROM promotion_reports WHERE report_id = ?",
            ["m4-smoke"],
        ).fetchall()

    assert rows
    assert proc.stdout.splitlines()[0].startswith("verdict: ")


def test_promote_compare_cli_uses_existing_experiments(tmp_path):
    registry_path = tmp_path / "registry.duckdb"
    incumbent = _record("inc-existing", strategy_name="XauAtrImpulse", total_pnl=50)
    challenger = _record(
        "chal-existing",
        strategy_name="XauQuickMomentum",
        total_pnl=200,
        profit_factor=2.0,
        consistency_score=0.8,
    )
    with connect(registry_path) as con:
        init_registry(con)
        index_experiment(con, incumbent, save_experiment(incumbent, root=tmp_path))
        index_experiment(con, challenger, save_experiment(challenger, root=tmp_path))

    proc = subprocess.run(
        [
            sys.executable,
            "scripts/promote_compare.py",
            "--incumbent-run-id",
            "inc-existing",
            "--challenger-run-id",
            "chal-existing",
            "--symbol",
            "XAUUSD",
            "--n-folds",
            "2",
            "--purge-bars",
            "10",
            "--embargo-bars",
            "10",
            "--report-id",
            "m4-existing",
            "--output-dir",
            str(tmp_path),
            "--registry",
            str(registry_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads((tmp_path / "m4-existing.json").read_text(encoding="utf-8"))
    assert payload["incumbent"]["run_id"] == "inc-existing"
    assert payload["challenger"]["run_id"] == "chal-existing"
    with connect(registry_path) as con:
        rows = con.execute(
            "SELECT incumbent_run_id, challenger_run_id FROM promotion_reports "
            "WHERE report_id = ?",
            ["m4-existing"],
        ).fetchall()
    assert rows == [("inc-existing", "chal-existing")]


def _record(
    run_id: str,
    *,
    strategy_name: str,
    symbol: str = "XAUUSD",
    total_pnl: float,
    profit_factor: float = 1.2,
    consistency_score: float = 0.6,
) -> ExperimentRecord:
    return ExperimentRecord(
        run_id=run_id,
        strategy_name=strategy_name,
        symbol=symbol,
        created_at="2026-01-01T00:00:00+00:00",
        git_sha="abc123",
        data_hash="hash123",
        config={"params": {}},
        walk_forward_config={"n_folds": 2},
        backtest_config={"symbol": symbol},
        fold_results=[
            {"fold_idx": 0, "test_start": 1_700_000_000, "test_end": 1_700_001_000}
        ],
        aggregated_metrics={
            "total_pnl": {"mean": total_pnl},
            "profit_factor": {"mean": profit_factor},
            "win_rate": {"mean": 0.50},
            "max_drawdown_pct": {"mean": 0.10},
            "sharpe_ratio": {"mean": 1.0},
            "n_trades": {"mean": 10},
            "expectancy": {"mean": total_pnl / 10},
        },
        walk_forward_stability={
            "consistency_score": consistency_score,
            "worst_fold_pnl": -10.0,
            "best_fold_pnl": total_pnl,
            "fold_pnl_std": 10.0,
            "interpretation": "2/2 positive, low variance",
        },
        verdict={"status": "pass", "rationale": "ok"},
        notes="",
        promotion_decision="pending",
    )


def _bars(count: int) -> list[dict]:
    bars = []
    for idx in range(count):
        close = 4100.0 + ((idx % 24) - 12) * 0.4 + idx * 0.02
        bars.append(
            {
                "time": 1_700_000_000 + idx * 60,
                "open": close - 0.2,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 100.0,
                "source": "test",
            }
        )
    return bars
