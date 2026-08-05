from __future__ import annotations

import gzip
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from aiquanttrader.backtest.cli import main as backtest_main
from aiquanttrader.backtest.conversion import (
    convert_normalized_dataset,
    convert_tardis_day,
    load_event_file,
)
from aiquanttrader.domain.data import (
    DatasetManifest,
    NormalizedFileManifest,
    NormalizedSegmentManifest,
    TardisFileManifest,
)
from aiquanttrader.market_data.io import sha256_file
from aiquanttrader.market_data.storage import parquet_schema


def common_row(
    *, event_order: int, event_id: str, event_ts: int, receive_ts: int
) -> dict[str, object]:
    return {
        "schema_version": 2,
        "event_order": event_order,
        "event_id": event_id,
        "venue": "HYPERLIQUID",
        "instrument_id": "BTC-USD-PERP.HYPERLIQUID",
        "event_ts_ns": event_ts,
        "receive_ts_ns": receive_ts,
        "connection_id": "test-connection",
        "source": "hyperliquid_websocket",
        "event_ts_source": "exchange",
        "source_record_id": "f" * 64,
    }


def write_normalized_fixture(root: Path) -> tuple[Path, Path]:
    book_path = root / "normalized/book.parquet"
    trade_path = root / "normalized/trade.parquet"
    book_path.parent.mkdir(parents=True)
    book_row = {
        **common_row(event_order=0, event_id="book-1", event_ts=1_000, receive_ts=1_100),
        "bids": [{"price": "100", "size": "5", "order_count": 1}],
        "asks": [{"price": "101", "size": "6", "order_count": 1}],
        "is_snapshot": True,
    }
    trade_row = {
        **common_row(event_order=1, event_id="trade-1", event_ts=2_000, receive_ts=2_100),
        "trade_id": "trade-1",
        "price": "100",
        "size": "1.5",
        "aggressor": "seller",
        "transaction_hash": None,
    }
    pq.write_table(pa.Table.from_pylist([book_row], schema=parquet_schema("l2_book")), book_path)
    pq.write_table(pa.Table.from_pylist([trade_row], schema=parquet_schema("trade")), trade_path)
    normalized = NormalizedSegmentManifest(
        source_segment_id="segment-1",
        source_segment_sha256="a" * 64,
        normalizer_version="normalizer-v1",
        files=(
            NormalizedFileManifest(
                event_type="l2_book",
                relative_path="normalized/book.parquet",
                row_count=1,
                byte_count=book_path.stat().st_size,
                file_sha256=sha256_file(book_path),
            ),
            NormalizedFileManifest(
                event_type="trade",
                relative_path="normalized/trade.parquet",
                row_count=1,
                byte_count=trade_path.stat().st_size,
                file_sha256=sha256_file(trade_path),
            ),
        ),
        event_count=2,
        excluded_frame_count=0,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    normalized_path = root / "normalized/segment-1.normalized.manifest.json"
    normalized_path.write_bytes(normalized.canonical_bytes() + b"\n")
    dataset = DatasetManifest(
        dataset_id="b" * 64,
        normalized_manifest_sha256s=(normalized.sha256(),),
        policy_sha256="c" * 64,
        gaps=(),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    dataset_path = root / "dataset.manifest.json"
    dataset_path.write_bytes(dataset.canonical_bytes() + b"\n")
    return dataset_path, normalized_path


def test_normalized_parquet_conversion_is_admitted_causal_and_byte_deterministic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_root = tmp_path / "source"
    dataset_path, normalized_path = write_normalized_fixture(data_root)
    output = tmp_path / "output"
    first_path = output / "events/first.npz"
    second_path = output / "events/second.npz"

    _, first = convert_normalized_dataset(
        data_root=data_root,
        dataset_manifest_path=dataset_path,
        normalized_manifest_paths=[normalized_path],
        output_root=output,
        event_path=first_path,
    )
    _, second = convert_normalized_dataset(
        data_root=data_root,
        dataset_manifest_path=dataset_path,
        normalized_manifest_paths=[normalized_path],
        output_root=output,
        event_path=second_path,
    )

    assert first.dataset_id == second.dataset_id
    assert first.event_file_sha256 == second.event_file_sha256
    assert first_path.read_bytes() == second_path.read_bytes()
    events = load_event_file(first_path)
    assert len(events) == first.event_count
    assert events["local_ts"].min() >= events["exch_ts"].min()

    cli_path = output / "events/cli.npz"
    assert (
        backtest_main(
            [
                "convert-normalized",
                "--data-root",
                str(data_root),
                "--dataset-manifest",
                str(dataset_path),
                "--normalized-manifest",
                str(normalized_path),
                "--output-root",
                str(output),
                "--event-path",
                "events/cli.npz",
            ]
        )
        == 0
    )
    assert '"dataset_id"' in capsys.readouterr().out

    policy_path = tmp_path / "validation.toml"
    policy_path.write_text(
        """schema_version = 1
policy_id = "cli-test"
train_ns = 100
purge_ns = 10
validation_ns = 100
embargo_ns = 10
test_ns = 100
step_ns = 100
final_holdout_ns = 100
label_horizon_ns = 10
minimum_folds = 3
""",
        encoding="utf-8",
    )
    assert (
        backtest_main(
            [
                "plan-validation",
                "--events",
                str(cli_path),
                "--dataset-sha256",
                first.dataset_id,
                "--policy",
                str(policy_path),
            ]
        )
        == 0
    )
    assert '"final_holdout"' in capsys.readouterr().out

    unadmitted = NormalizedSegmentManifest.model_validate_json(normalized_path.read_bytes())
    unadmitted = unadmitted.model_copy(update={"source_segment_id": "other-segment"})
    other_path = data_root / "normalized/other.manifest.json"
    other_path.write_bytes(unadmitted.canonical_bytes() + b"\n")
    with pytest.raises(ValueError, match="not admitted"):
        convert_normalized_dataset(
            data_root=data_root,
            dataset_manifest_path=dataset_path,
            normalized_manifest_paths=[other_path],
            output_root=output,
            event_path=output / "events/rejected.npz",
        )


def write_tardis_file(
    root: Path,
    *,
    data_type: Literal["incremental_book_L2", "trades"],
    content: bytes,
    row_count: int,
) -> Path:
    relative = Path(
        "historical/source=tardis/exchange=hyperliquid",
        f"data_type={data_type}",
        "date=2026-01-01/BTC.csv.gz",
    )
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(content, mtime=0))
    manifest = TardisFileManifest(
        data_type=data_type,
        date="2026-01-01",
        relative_path=relative.as_posix(),
        byte_count=path.stat().st_size,
        compressed_sha256=sha256_file(path),
        row_count=row_count,
        source_url=(
            f"https://datasets.tardis.dev/v1/hyperliquid/{data_type}/2026/01/01/BTC.csv.gz"
        ),
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    path.with_suffix(path.suffix + ".manifest.json").write_bytes(manifest.canonical_bytes() + b"\n")
    return path


def test_tardis_conversion_verifies_manifests_orders_streams_and_is_deterministic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "tardis"
    trades = write_tardis_file(
        source,
        data_type="trades",
        row_count=1,
        content=(
            b"exchange,symbol,timestamp,local_timestamp,id,side,price,amount\n"
            b"hyperliquid,BTC,1500000,1600000,t1,sell,100,1\n"
        ),
    )
    depth = write_tardis_file(
        source,
        data_type="incremental_book_L2",
        row_count=3,
        content=(
            b"exchange,symbol,timestamp,local_timestamp,is_snapshot,side,price,amount\n"
            b"hyperliquid,BTC,1000000,1100000,true,bid,100,5\n"
            b"hyperliquid,BTC,1000000,1100000,true,ask,101,6\n"
            b"hyperliquid,BTC,2000000,2100000,false,bid,100,4\n"
        ),
    )
    output = tmp_path / "output"
    first_path = output / "first.npz"
    second_path = output / "second.npz"
    _, first = convert_tardis_day(
        source_root=source,
        input_files=[depth, trades],
        output_root=output,
        event_path=first_path,
    )
    _, second = convert_tardis_day(
        source_root=source,
        input_files=[trades, depth],
        output_root=output,
        event_path=second_path,
    )

    assert first.dataset_id == second.dataset_id
    assert first_path.read_bytes() == second_path.read_bytes()
    assert load_event_file(first_path).size > 0

    assert (
        backtest_main(
            [
                "convert-tardis",
                "--source-root",
                str(source),
                "--input",
                str(depth),
                "--input",
                str(trades),
                "--output-root",
                str(output),
                "--event-path",
                "cli.npz",
            ]
        )
        == 0
    )
    assert '"event_count"' in capsys.readouterr().out

    trades.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="does not match"):
        convert_tardis_day(
            source_root=source,
            input_files=[trades, depth],
            output_root=output,
            event_path=output / "corrupt.npz",
        )


def test_backtest_cli_validates_scenarios_and_reports_input_errors(
    config_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        backtest_main(
            [
                "validate-scenario",
                "--scenario",
                str(config_dir / "backtest/baseline.toml"),
            ]
        )
        == 0
    )
    assert '"promotion_eligible": false' in capsys.readouterr().out

    assert backtest_main(["validate-scenario", "--scenario", str(tmp_path / "missing.toml")]) == 2
    assert '"status": "invalid"' in capsys.readouterr().err
