from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from aiquanttrader.data_store import connect
from aiquanttrader.prices import ingest_bars


ROOT = Path(__file__).resolve().parents[1]


def test_xau_strategy_lab_cli_smoke(tmp_path):
    data_path = tmp_path / "bars.duckdb"
    registry_path = tmp_path / "registry.duckdb"
    output_dir = tmp_path / "profile_lab"
    experiment_dir = tmp_path / "experiments"
    with connect(data_path) as con:
        ingest_bars(con, "XAUUSD", _bars(360))

    proc = subprocess.run(
        [
            sys.executable,
            "scripts/xau_strategy_lab.py",
            "--candidate",
            "incumbent_smc4",
            "--candidate",
            "attack_atr_m1",
            "--folds",
            "2",
            "--purge-bars",
            "10",
            "--embargo-bars",
            "10",
            "--min-train-bars",
            "10",
            "--min-test-bars",
            "10",
            "--data-source",
            str(data_path),
            "--registry",
            str(registry_path),
            "--output-dir",
            str(output_dir),
            "--experiment-dir",
            str(experiment_dir),
            "--run-id",
            "lab-smoke",
            "--max-data-age-hours",
            "0",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    report_path = output_dir / "lab-smoke.json"
    assert report_path.exists()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["winner"]["profile"]["profile_name"] in {
        "incumbent_smc4",
        "attack_atr_m1",
    }
    assert "recent_total_pnl" in payload["winner"]
    assert "recent_profit_factor" in payload["winner"]
    assert "incumbent_delta_pnl" in payload["winner"]
    assert payload["backtest_defaults"]["min_challenger_pnl_delta"] == 250.0
    assert payload["backtest_defaults"]["spread_points"] == 8.0
    assert payload["backtest_defaults"]["slippage_points"] == 2.0
    candidate_config = payload["candidates"][0]
    experiment = json.loads(Path(candidate_config["experiment_json"]).read_text(encoding="utf-8"))
    assert experiment["config"]["inner_strategy"] == "XauLiveSignals"
    assert experiment["backtest_config"]["point_value"] == 100.0
    assert experiment["backtest_config"]["fill_config"]["point_size"] == 0.01
    assert experiment["backtest_config"]["fill_config"]["commission_per_lot"] == 3.5
    assert experiment["backtest_config"]["break_even"]["rr_ratio"] == 1.0
    assert len(payload["candidates"]) == 2
    assert (experiment_dir / "lab-smoke-incumbent_smc4.json").exists()


def test_xau_strategy_lab_rejects_stale_data():
    sys.path.insert(0, str(ROOT / "scripts"))
    from xau_strategy_lab import _validate_data_freshness

    with pytest.raises(ValueError, match="research data is stale"):
        _validate_data_freshness(
            [{"time": 1_000}],
            max_age_hours=1.0,
            now_epoch=10_000.0,
        )


def test_xau_strategy_lab_uses_full_enabled_live_signal_chain():
    sys.path.insert(0, str(ROOT / "scripts"))
    from xau_strategy_lab import _strategy

    from aiquanttrader.backtest import XauLiveSignalStrategy
    from aiquanttrader.xau_profiles import DEFAULT_PROFILE

    strategy = _strategy(DEFAULT_PROFILE)

    assert isinstance(strategy._inner, XauLiveSignalStrategy)


def test_xau_strategy_lab_loads_one_freshest_source_bar_per_timestamp(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    from xau_strategy_lab import _load_bars

    data_path = tmp_path / "duplicate-sources.duckdb"
    fresh = _bars(3)
    stale = [
        {
            **bar,
            "close": bar["close"] - 100.0,
            "source": "retired-export",
        }
        for bar in fresh[:2]
    ]
    fresh = [{**bar, "source": "current-export"} for bar in fresh]
    with connect(data_path) as con:
        ingest_bars(con, "XAUUSD", stale)
        ingest_bars(con, "XAUUSD", fresh)

    loaded = _load_bars(
        data_source=data_path,
        symbol="XAUUSD",
        from_date="",
        to_date="",
        max_bars=2,
    )

    assert [bar["time"] for bar in loaded] == [fresh[1]["time"], fresh[2]["time"]]
    assert [bar["close"] for bar in loaded] == [fresh[1]["close"], fresh[2]["close"]]


def _bars(count: int) -> list[dict]:
    bars = []
    for idx in range(count):
        wave = ((idx % 20) - 10) * 0.9
        close = 4100.0 + wave + idx * 0.02
        bars.append(
            {
                "time": 1_700_000_000 + idx * 60,
                "open": close - 0.35,
                "high": close + 1.4,
                "low": close - 1.4,
                "close": close,
                "volume": 100.0,
                "source": "test",
            }
        )
    return bars
