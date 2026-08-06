from __future__ import annotations

from pathlib import Path

import pytest

from aiquanttrader.config import load_config
from aiquanttrader.domain.data import (
    DataQualityPolicy,
    MarketDataSoakPolicy,
    MarketDataSoakReport,
    NormalizerState,
    RecorderState,
    SegmentFinalizationReason,
)
from aiquanttrader.market_data import cli
from aiquanttrader.market_data.evidence import (
    evaluate_market_data_soak,
    load_soak_policy,
    parse_recorder_metrics,
)
from aiquanttrader.market_data.raw import RawSegmentWriter
from aiquanttrader.market_data.storage import normalize_segment

NOW_NS = 1_700_000_000_000_000_000
GIB = 1024**3


def _segment(root: Path, started_ns: int, ended_ns: int, connection: str) -> None:
    writer = RawSegmentWriter(
        root,
        network="mainnet",
        connection_id=connection,
        started_at_ns=started_ns,
        sync_every_records=1,
    )
    writer.append(
        b'{"channel":"pong"}',
        receive_ts_ns=ended_ns,
        monotonic_ts_ns=ended_ns - started_ns,
    )
    segment = writer.finalize(SegmentFinalizationReason.ROTATION)
    normalize_segment(
        segment.segment_path,
        output_root=root,
        quarantine_root=root / "quarantine" / "raw-corrupt",
    )


def _state(root: Path, captured_ns: int) -> None:
    target = root / "market-data"
    target.mkdir(parents=True)
    target.joinpath("recorder-state.json").write_bytes(
        RecorderState(
            status="connected",
            environment="paper",
            network="mainnet",
            connection_id="ws-live",
            heartbeat_ts_ns=captured_ns - 1_000_000_000,
            last_frame_ts_ns=captured_ns - 1_000_000_000,
            current_segment_id="active-segment",
            reconnect_count=0,
        ).canonical_bytes()
    )
    target.joinpath("normalizer-state.json").write_bytes(
        NormalizerState(
            status="running",
            heartbeat_ts_ns=captured_ns - 1_000_000_000,
            discovered=2,
            normalized=2,
            already_complete=0,
            quarantined=0,
        ).canonical_bytes()
    )


def _metrics(*, quality: str = "") -> str:
    return f"""
# TYPE aqt_market_data_frames_total counter
aqt_market_data_frames_total{{transport="binary"}} 3
# TYPE aqt_market_data_bytes_total counter
aqt_market_data_bytes_total 100
# TYPE aqt_market_data_reconnects_total counter
# TYPE aqt_market_data_quality_issues_total counter
{quality}
# TYPE aqt_market_data_last_frame_timestamp_seconds gauge
aqt_market_data_last_frame_timestamp_seconds 1700000020
# TYPE aqt_market_data_disk_free_bytes gauge
aqt_market_data_disk_free_bytes {6 * GIB}
# TYPE aqt_market_data_connected gauge
aqt_market_data_connected 1
# TYPE aqt_market_data_segments_finalized_total counter
aqt_market_data_segments_finalized_total{{reason="rotation"}} 2
"""


def _policy() -> MarketDataSoakPolicy:
    return MarketDataSoakPolicy(
        policy_id="test-soak-v1",
        frozen_at_ns=NOW_NS,
        minimum_observation_ns=20_000_000_000,
        minimum_finalized_segments=2,
        maximum_start_lag_ns=0,
        maximum_reconnects=0,
        maximum_recorder_restarts=0,
        maximum_normalizer_restarts=0,
        maximum_excluded_frames=0,
        minimum_free_bytes=5 * GIB,
        recorder_state_stale_after_ns=30_000_000_000,
        normalizer_state_stale_after_ns=120_000_000_000,
        allowed_finalization_reasons=(SegmentFinalizationReason.ROTATION,),
        data_quality_policy=DataQualityPolicy(),
    )


def _evaluate(
    repository_root: Path,
    data_root: Path,
    state_root: Path,
    *,
    metrics_text: str | None = None,
    recorder_restarts: int = 0,
) -> MarketDataSoakReport:
    captured = NOW_NS + 21_000_000_000
    bundle = load_config(repository_root / "configs", "paper")
    return evaluate_market_data_soak(
        bundle=bundle,
        policy=_policy(),
        data_root=data_root,
        state_root=state_root,
        metrics=parse_recorder_metrics(
            _metrics() if metrics_text is None else metrics_text,
            captured_ts_ns=captured,
        ),
        requested_started_ts_ns=NOW_NS,
        runtime_code_identity="1" * 40,
        collector_code_identity="2" * 40,
        image_digest=f"sha256:{'3' * 64}",
        expected_config_fingerprint=bundle.fingerprint,
        start_free_bytes=6 * GIB,
        recorder_restart_count=recorder_restarts,
        normalizer_restart_count=0,
    )


def test_soak_evidence_accepts_only_verified_in_window_artifacts(
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).parents[2]
    data = tmp_path / "data"
    _segment(data, NOW_NS - 20_000_000_000, NOW_NS - 10_000_000_000, "pre-soak")
    _segment(data, NOW_NS, NOW_NS + 10_000_000_000, "first")
    _segment(data, NOW_NS + 10_000_000_000, NOW_NS + 20_000_000_000, "second")
    state = tmp_path / "state"
    _state(state, NOW_NS + 21_000_000_000)

    report = _evaluate(repository_root, data, state)

    assert report.accepted is True
    assert report.raw_segments == 2
    assert report.observation_ns == 20_000_000_000
    assert report.dataset_manifest is not None
    assert report.dataset_admission_error is None
    assert len(report.raw_manifest_sha256s) == 2
    assert MarketDataSoakReport.model_validate_json(report.canonical_bytes()) == report


def test_soak_evidence_retains_a_content_addressed_rejection(tmp_path: Path) -> None:
    repository_root = Path(__file__).parents[2]
    data = tmp_path / "data"
    _segment(data, NOW_NS, NOW_NS + 10_000_000_000, "first")
    _segment(data, NOW_NS + 10_000_000_000, NOW_NS + 20_000_000_000, "second")
    state = tmp_path / "state"
    _state(state, NOW_NS + 21_000_000_000)
    metrics = _metrics(
        quality=(
            'aqt_market_data_quality_issues_total{kind="schema_error",code="unknown_channel"} 1'
        )
    )

    report = _evaluate(
        repository_root,
        data,
        state,
        metrics_text=metrics,
        recorder_restarts=1,
    )

    assert report.accepted is False
    failed = {gate.gate for gate in report.gates if not gate.passed}
    assert failed == {"recorder_restarts", "metric_schema_error"}


def test_metrics_parser_rejects_ambiguous_and_invalid_samples() -> None:
    with pytest.raises(ValueError, match="one unlabeled sample"):
        parse_recorder_metrics(
            _metrics() + "\naqt_market_data_connected 1\n",
            captured_ts_ns=NOW_NS + 21_000_000_000,
        )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            _metrics().replace("aqt_market_data_connected 1", "aqt_market_data_connected 2"),
            "zero or one",
        ),
        (
            _metrics().replace(
                f"aqt_market_data_disk_free_bytes {6 * GIB}",
                "aqt_market_data_disk_free_bytes 1.5",
            ),
            "valid domains",
        ),
        (
            _metrics().replace(
                "aqt_market_data_last_frame_timestamp_seconds 1700000020",
                "aqt_market_data_last_frame_timestamp_seconds NaN",
            ),
            "must be finite",
        ),
        (
            _metrics().replace('aqt_market_data_frames_total{transport="binary"} 3', ""),
            "required metric is missing",
        ),
        (
            _metrics().replace(
                "# TYPE aqt_market_data_reconnects_total counter",
                "# TYPE aqt_market_data_reconnects_total counter\n"
                'aqt_market_data_reconnects_total{unexpected="x"} 1',
            ),
            "unexpected labels",
        ),
        (
            _metrics(quality='aqt_market_data_quality_issues_total{kind="schema_error"} 1'),
            "quality issue metric has unexpected labels",
        ),
    ],
)
def test_metrics_parser_fails_closed_on_invalid_contracts(payload: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_recorder_metrics(payload, captured_ts_ns=NOW_NS + 21_000_000_000)


def test_evidence_inputs_reject_invalid_time_disk_and_restart_bounds(tmp_path: Path) -> None:
    repository_root = Path(__file__).parents[2]
    bundle = load_config(repository_root / "configs", "paper")
    metrics = parse_recorder_metrics(
        _metrics(),
        captured_ts_ns=NOW_NS + 21_000_000_000,
    )

    def evaluate(started_ns: int, recorder_restarts: int) -> MarketDataSoakReport:
        return evaluate_market_data_soak(
            bundle=bundle,
            policy=_policy(),
            data_root=tmp_path / "missing-data",
            state_root=tmp_path / "missing-state",
            metrics=metrics,
            requested_started_ts_ns=started_ns,
            runtime_code_identity="1" * 40,
            collector_code_identity="2" * 40,
            image_digest=f"sha256:{'3' * 64}",
            expected_config_fingerprint=bundle.fingerprint,
            start_free_bytes=6 * GIB,
            recorder_restart_count=recorder_restarts,
            normalizer_restart_count=0,
        )

    with pytest.raises(ValueError, match="must not follow"):
        evaluate(metrics.captured_ts_ns + 1, 0)
    with pytest.raises(ValueError, match="cannot be negative"):
        evaluate(NOW_NS, -1)
    with pytest.raises(ValueError, match="non-negative integer"):
        parse_recorder_metrics(
            _metrics().replace(
                "aqt_market_data_bytes_total 100", "aqt_market_data_bytes_total 1.5"
            ),
            captured_ts_ns=NOW_NS + 21_000_000_000,
        )


def test_policy_loader_reads_the_checked_in_frozen_policy() -> None:
    policy = load_soak_policy(Path(__file__).parents[2] / "configs/market-data/soak-v1.toml")

    assert policy.minimum_observation_ns == 6 * 60 * 60 * 1_000_000_000
    assert policy.maximum_reconnects == 0
    assert policy.allowed_finalization_reasons == (SegmentFinalizationReason.ROTATION,)


def test_policy_loader_fails_closed_when_policy_is_missing(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid market-data soak policy"):
        load_soak_policy(tmp_path / "missing.toml")


def test_evaluate_soak_cli_writes_an_atomic_accepted_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository_root = Path(__file__).parents[2]
    data = tmp_path / "data"
    _segment(data, NOW_NS, NOW_NS + 10_000_000_000, "first")
    _segment(data, NOW_NS + 10_000_000_000, NOW_NS + 20_000_000_000, "second")
    state = tmp_path / "state"
    captured = NOW_NS + 21_000_000_000
    _state(state, captured)
    metrics = tmp_path / "metrics.prom"
    metrics.write_text(_metrics(), encoding="utf-8")
    policy = tmp_path / "policy.toml"
    policy.write_text(
        f"""
schema_version = 1
policy_id = "test-soak-v1"
frozen_at_ns = {NOW_NS}
minimum_observation_ns = 20000000000
minimum_finalized_segments = 2
maximum_start_lag_ns = 0
maximum_reconnects = 0
maximum_recorder_restarts = 0
maximum_normalizer_restarts = 0
maximum_excluded_frames = 0
minimum_free_bytes = {5 * GIB}
recorder_state_stale_after_ns = 30000000000
normalizer_state_stale_after_ns = 120000000000
allowed_finalization_reasons = ["rotation"]

[data_quality_policy]
schema_version = 1
max_classified_gap_ns = 30000000000
max_schema_errors = 0
max_crossed_books = 0
max_timestamp_regressions = 0
max_duplicates = 0
reject_unexplained_gaps = true
""",
        encoding="utf-8",
    )
    output = tmp_path / "evidence" / "soak.json"
    bundle = load_config(repository_root / "configs", "paper")

    result = cli.main(
        [
            "evaluate-soak",
            "--config-dir",
            str(repository_root / "configs"),
            "--policy",
            str(policy),
            "--data-root",
            str(data),
            "--state-root",
            str(state),
            "--metrics-snapshot",
            str(metrics),
            "--metrics-captured-ts-ns",
            str(captured),
            "--requested-started-ts-ns",
            str(NOW_NS),
            "--runtime-code-identity",
            "1" * 40,
            "--collector-code-identity",
            "2" * 40,
            "--image-digest",
            f"sha256:{'3' * 64}",
            "--runtime-config-fingerprint",
            bundle.fingerprint,
            "--start-free-bytes",
            str(6 * GIB),
            "--recorder-restart-count",
            "0",
            "--normalizer-restart-count",
            "0",
            "--output",
            str(output),
        ]
    )

    assert result == 0
    assert MarketDataSoakReport.model_validate_json(output.read_bytes()).accepted is True
    assert '"status": "accepted"' in capsys.readouterr().out
