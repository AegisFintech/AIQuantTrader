"""DuckDB experiment registry index."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from finrobot.research.experiments import ExperimentRecord


def init_registry(con: duckdb.DuckDBPyConnection) -> None:
    """Create experiment registry tables and indexes if needed."""

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS experiments (
          run_id TEXT PRIMARY KEY,
          strategy TEXT,
          symbol TEXT,
          n_folds INTEGER,
          mean_pnl DOUBLE,
          mean_win_rate DOUBLE,
          mean_profit_factor DOUBLE,
          worst_fold_pnl DOUBLE,
          consistency_score DOUBLE,
          verdict TEXT,
          git_sha TEXT,
          data_hash TEXT,
          created_at TIMESTAMP,
          json_path TEXT
        )
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_experiments_strategy ON experiments(strategy)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_experiments_symbol ON experiments(symbol)"
    )


def index_experiment(
    con: duckdb.DuckDBPyConnection,
    record: ExperimentRecord,
    json_path: Path,
) -> None:
    """Insert or replace one experiment registry row."""

    con.execute("DELETE FROM experiments WHERE run_id = ?", [record.run_id])
    con.execute(
        """
        INSERT INTO experiments (
          run_id, strategy, symbol, n_folds, mean_pnl, mean_win_rate,
          mean_profit_factor, worst_fold_pnl, consistency_score, verdict,
          git_sha, data_hash, created_at, json_path
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CAST(? AS TIMESTAMP), ?)
        """,
        [
            record.run_id,
            record.strategy_name,
            record.symbol,
            _as_int(record.walk_forward_config.get("n_folds")),
            _metric_mean(record.aggregated_metrics, "total_pnl"),
            _metric_mean(record.aggregated_metrics, "win_rate"),
            _metric_mean(record.aggregated_metrics, "profit_factor"),
            _as_float(record.walk_forward_stability.get("worst_fold_pnl")),
            _as_float(record.walk_forward_stability.get("consistency_score")),
            str(record.verdict.get("status", "") or ""),
            record.git_sha,
            record.data_hash,
            record.created_at,
            str(json_path),
        ],
    )


def query_experiments(
    con: duckdb.DuckDBPyConnection,
    *,
    strategy: str | None = None,
    symbol: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Query registry rows ordered newest first."""

    where: list[str] = []
    params: list[Any] = []
    if strategy is not None:
        where.append("strategy = ?")
        params.append(strategy)
    if symbol is not None:
        where.append("symbol = ?")
        params.append(symbol)
    sql = "SELECT * FROM experiments"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, int(limit)))
    result = con.execute(sql, params)
    columns = [item[0] for item in result.description]
    return [dict(zip(columns, row)) for row in result.fetchall()]


def latest_experiment(
    con: duckdb.DuckDBPyConnection,
    *,
    strategy: str,
    symbol: str,
) -> dict | None:
    """Return the newest registry row for a strategy and symbol."""

    rows = query_experiments(con, strategy=strategy, symbol=symbol, limit=1)
    return rows[0] if rows else None


def _metric_mean(payload: dict[str, Any], metric_name: str) -> float | None:
    metric = payload.get(metric_name, {})
    if not isinstance(metric, dict):
        return None
    return _as_float(metric.get("mean"))


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)
