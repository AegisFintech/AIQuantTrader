from __future__ import annotations

from pathlib import Path

import duckdb

from finrobot.research.experiments import (
    ExperimentRecord,
    experiment_path,
    file_hash,
    git_sha,
    list_experiments,
    load_experiment,
    save_experiment,
)
from finrobot.research.registry import (
    index_experiment,
    init_registry,
    latest_experiment,
    query_experiments,
)


def test_save_and_load_round_trip(tmp_path):
    record = _record("round-trip")

    path = save_experiment(record, root=tmp_path)
    loaded = load_experiment("round-trip", root=tmp_path)

    assert path == tmp_path / "round-trip.json"
    assert loaded == record


def test_experiment_path_under_state_research():
    path = experiment_path("abc")

    assert str(path).endswith("state/research/experiments/abc.json")


def test_git_sha_returns_string():
    sha = git_sha()

    assert isinstance(sha, str)
    assert sha


def test_file_hash_stable(tmp_path):
    path = tmp_path / "known.txt"
    path.write_text("known content\n", encoding="utf-8")

    assert file_hash(path) == file_hash(path)


def test_list_experiments_includes_new_one(tmp_path):
    save_experiment(_record("one"), root=tmp_path)
    save_experiment(_record("two"), root=tmp_path)

    assert {"one", "two"}.issubset(set(list_experiments(root=tmp_path)))


def test_init_registry_creates_table():
    con = duckdb.connect(":memory:")
    init_registry(con)

    rows = con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_name = 'experiments'"
    ).fetchall()

    assert rows == [("experiments",)]


def test_index_and_query_experiment(tmp_path):
    con = duckdb.connect(":memory:")
    init_registry(con)
    record = _record("indexed", strategy_name="XauAtrImpulse")
    path = save_experiment(record, root=tmp_path)

    index_experiment(con, record, path)
    rows = query_experiments(con, strategy="XauAtrImpulse")

    assert len(rows) == 1
    assert rows[0]["run_id"] == "indexed"


def test_query_filters_by_symbol(tmp_path):
    con = duckdb.connect(":memory:")
    init_registry(con)
    xau = _record("xau", symbol="XAUUSD")
    other = _record("other", symbol="GOLD")
    index_experiment(con, xau, save_experiment(xau, root=tmp_path))
    index_experiment(con, other, save_experiment(other, root=tmp_path))

    rows = query_experiments(con, symbol="XAUUSD")

    assert [row["run_id"] for row in rows] == ["xau"]


def test_latest_experiment_returns_most_recent(tmp_path):
    con = duckdb.connect(":memory:")
    init_registry(con)
    old = _record("old", created_at="2026-01-01T00:00:00+00:00")
    new = _record("new", created_at="2026-01-02T00:00:00+00:00")
    index_experiment(con, old, save_experiment(old, root=tmp_path))
    index_experiment(con, new, save_experiment(new, root=tmp_path))

    latest = latest_experiment(con, strategy="XauAtrImpulse", symbol="XAUUSD")

    assert latest is not None
    assert latest["run_id"] == "new"


def _record(
    run_id: str,
    *,
    strategy_name: str = "XauAtrImpulse",
    symbol: str = "XAUUSD",
    created_at: str = "2026-01-01T00:00:00+00:00",
) -> ExperimentRecord:
    return ExperimentRecord(
        run_id=run_id,
        strategy_name=strategy_name,
        symbol=symbol,
        created_at=created_at,
        git_sha="abc123",
        data_hash="hash123",
        config={"params": {}, "strategy_config_diff": {}},
        walk_forward_config={"n_folds": 2},
        backtest_config={"symbol": symbol},
        fold_results=[],
        aggregated_metrics={
            "total_pnl": {"mean": 10.0},
            "win_rate": {"mean": 0.5},
            "profit_factor": {"mean": 1.5},
        },
        walk_forward_stability={
            "worst_fold_pnl": 1.0,
            "consistency_score": 1.0,
        },
        verdict={"status": "pass", "rationale": "ok"},
        notes="",
        promotion_decision="promote",
    )
