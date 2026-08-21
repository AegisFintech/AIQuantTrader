from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aiquanttrader.backtest.models import ValidationPolicy
from aiquanttrader.domain.data import (
    DataQualityPolicy,
    NormalizedFileManifest,
    NormalizedSegmentManifest,
    RawSegmentManifest,
    SegmentFinalizationReason,
)
from aiquanttrader.research.metrics import DataReadinessMetrics
from aiquanttrader.research.readiness import (
    DataReadinessMonitor,
    evaluate_data_readiness,
    load_readiness_inputs,
    required_validation_span_ns,
)
from aiquanttrader.research.readiness_models import (
    ResearchDataReadinessPolicy,
    ResearchDataReadinessReport,
    ResearchDataReadinessState,
)

_NOW_NS = 2_000_000_000_000


def _validation_policy() -> ValidationPolicy:
    return ValidationPolicy(
        policy_id="test-validation",
        train_ns=10_000_000_000,
        purge_ns=1_000_000_000,
        validation_ns=2_000_000_000,
        embargo_ns=1_000_000_000,
        test_ns=2_000_000_000,
        step_ns=2_000_000_000,
        final_holdout_ns=3_000_000_000,
        label_horizon_ns=1_000_000_000,
        minimum_folds=3,
    )


def _readiness_policy() -> ResearchDataReadinessPolicy:
    return ResearchDataReadinessPolicy(
        policy_id="test-readiness",
        maximum_contiguous_gap_ns=1_000_000_000,
        maximum_latest_segment_age_ns=10_000_000_000,
        maximum_excluded_frames=0,
        minimum_free_bytes=1,
        storage_projection_safety_bps=10_000,
        data_quality_policy=DataQualityPolicy(max_classified_gap_ns=1_000_000_000),
    )


def _write_pair(root: Path, segment: int, start_ns: int, end_ns: int) -> None:
    segment_id = f"segment-{segment}"
    digest = hashlib.sha256(segment_id.encode()).hexdigest()
    raw = RawSegmentManifest(
        segment_id=segment_id,
        network="mainnet",
        relative_path=f"raw/{segment_id}.raw.zst",
        connection_id="connection-1",
        started_at_ns=start_ns,
        ended_at_ns=end_ns,
        record_count=10,
        payload_bytes=100,
        compressed_bytes=50,
        compressed_sha256=digest,
        records_sha256="1" * 64,
        recorder_version="test",
        finalization_reason=SegmentFinalizationReason.ROTATION,
        created_at=datetime.fromtimestamp(end_ns / 1e9, tz=UTC),
    )
    raw_manifest = root / "raw" / f"{segment_id}.manifest.json"
    raw_manifest.parent.mkdir(parents=True, exist_ok=True)
    raw_manifest.write_bytes(raw.canonical_bytes())

    relative_file = f"normalized/trades/{segment_id}.parquet"
    normalized_file = root / relative_file
    normalized_file.parent.mkdir(parents=True, exist_ok=True)
    normalized_file.write_bytes(b"x")
    normalized = NormalizedSegmentManifest(
        source_segment_id=segment_id,
        source_segment_sha256=digest,
        normalizer_version="test",
        files=(
            NormalizedFileManifest(
                event_type="trade",
                relative_path=relative_file,
                row_count=1,
                byte_count=1,
                file_sha256=hashlib.sha256(b"x").hexdigest(),
            ),
        ),
        event_count=1,
        excluded_frame_count=0,
        created_at=datetime.fromtimestamp(end_ns / 1e9, tz=UTC),
    )
    normalized_manifest = (
        root / "normalized" / "manifests" / f"{segment_id}.normalized.manifest.json"
    )
    normalized_manifest.parent.mkdir(parents=True, exist_ok=True)
    normalized_manifest.write_bytes(normalized.canonical_bytes())


def test_required_validation_span_includes_every_fold_boundary_and_holdout() -> None:
    assert required_validation_span_ns(_validation_policy()) == 23_000_000_000


def test_readiness_accepts_a_fresh_contiguous_normalized_capture(tmp_path: Path) -> None:
    _write_pair(tmp_path, 1, _NOW_NS - 30_000_000_000, _NOW_NS - 20_000_000_000)
    _write_pair(tmp_path, 2, _NOW_NS - 20_000_000_000, _NOW_NS - 10_000_000_000)
    _write_pair(tmp_path, 3, _NOW_NS - 10_000_000_000, _NOW_NS - 1_000_000_000)

    report = evaluate_data_readiness(
        data_root=tmp_path,
        policy=_readiness_policy(),
        validation_policy=_validation_policy(),
        generated_ts_ns=_NOW_NS,
    )

    assert report.ready_for_horizon_audit
    assert report.latest_contiguous_span_ns == 29_000_000_000
    assert report.completion_bps == 10_000
    assert report.latest_chain_segment_count == 3
    assert report.latest_chain_dataset_admitted
    assert not report.model_training_authorized
    assert not report.production_promotion_authorized


def test_readiness_uses_latest_chain_instead_of_an_older_longer_chain(tmp_path: Path) -> None:
    _write_pair(tmp_path, 1, _NOW_NS - 40_000_000_000, _NOW_NS - 15_000_000_000)
    _write_pair(tmp_path, 2, _NOW_NS - 10_000_000_000, _NOW_NS - 1_000_000_000)

    report = evaluate_data_readiness(
        data_root=tmp_path,
        policy=_readiness_policy(),
        validation_policy=_validation_policy(),
        generated_ts_ns=_NOW_NS,
    )

    assert report.longest_contiguous_span_ns == 25_000_000_000
    assert report.latest_contiguous_span_ns == 9_000_000_000
    assert not report.ready_for_horizon_audit
    failed = {gate.gate for gate in report.gates if not gate.passed}
    assert "latest_capture_span" in failed


def test_readiness_fails_closed_on_unpaired_raw_segment(tmp_path: Path) -> None:
    _write_pair(tmp_path, 1, _NOW_NS - 30_000_000_000, _NOW_NS - 1_000_000_000)
    raw_path = next((tmp_path / "raw").glob("*.manifest.json"))
    payload = json.loads(raw_path.read_bytes())
    payload["segment_id"] = "unpaired-segment"
    (tmp_path / "raw" / "unpaired-segment.manifest.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    report = evaluate_data_readiness(
        data_root=tmp_path,
        policy=_readiness_policy(),
        validation_policy=_validation_policy(),
        generated_ts_ns=_NOW_NS,
    )

    assert report.unpaired_raw_segment_count == 1
    assert not report.ready_for_horizon_audit
    normalization_gate = next(
        gate for gate in report.gates if gate.gate == "normalization_complete"
    )
    assert not normalization_gate.passed


def test_monitor_writes_fresh_state_and_bounded_metrics(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    state_root = tmp_path / "state"
    _write_pair(data_root, 1, _NOW_NS - 30_000_000_000, _NOW_NS - 1_000_000_000)
    metrics = DataReadinessMetrics()
    monitor = DataReadinessMonitor(
        data_root=data_root,
        state_root=state_root,
        policy=_readiness_policy(),
        validation_policy=_validation_policy(),
        metrics=metrics,
    )

    report = monitor.run_once(generated_ts_ns=_NOW_NS)
    state = json.loads((state_root / "research" / "data-readiness.json").read_bytes())

    assert state["status"] == "running"
    assert state["report"]["report_id"] == report.report_id
    assert metrics.ready._value.get() == pytest.approx(1.0)


def test_readiness_policy_requires_one_gap_definition() -> None:
    with pytest.raises(ValueError, match="gap bounds must match"):
        ResearchDataReadinessPolicy(
            policy_id="invalid",
            maximum_contiguous_gap_ns=2,
            maximum_latest_segment_age_ns=1,
            maximum_excluded_frames=0,
            minimum_free_bytes=1,
            data_quality_policy=DataQualityPolicy(max_classified_gap_ns=1),
        )


def test_readiness_reports_empty_or_malformed_artifacts_without_claiming_data(
    tmp_path: Path,
) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "normalized" / "manifests").mkdir(parents=True)
    (tmp_path / "raw" / "broken.manifest.json").write_text("{", encoding="utf-8")
    (tmp_path / "normalized" / "manifests" / "broken.json").write_text("[]", encoding="utf-8")

    report = evaluate_data_readiness(
        data_root=tmp_path,
        policy=_readiness_policy(),
        validation_policy=_validation_policy(),
        generated_ts_ns=_NOW_NS,
    )

    assert report.invalid_manifest_count == 2
    assert report.latest_contiguous_started_ts_ns is None
    assert report.latest_contiguous_span_ns == 0
    assert report.storage_rate_bytes_per_day == 0
    assert not report.ready_for_horizon_audit


def test_readiness_detects_source_digest_and_file_metadata_mismatches(tmp_path: Path) -> None:
    _write_pair(tmp_path, 1, _NOW_NS - 30_000_000_000, _NOW_NS - 1_000_000_000)
    normalized_path = next((tmp_path / "normalized" / "manifests").glob("*.json"))
    payload = json.loads(normalized_path.read_bytes())
    payload["source_segment_sha256"] = "f" * 64
    normalized_path.write_text(json.dumps(payload), encoding="utf-8")

    digest_report = evaluate_data_readiness(
        data_root=tmp_path,
        policy=_readiness_policy(),
        validation_policy=_validation_policy(),
        generated_ts_ns=_NOW_NS,
    )
    assert digest_report.invalid_binding_count == 1

    payload["source_segment_sha256"] = hashlib.sha256(b"segment-1").hexdigest()
    payload["files"][0]["byte_count"] = 2
    normalized_path.write_text(json.dumps(payload), encoding="utf-8")
    file_report = evaluate_data_readiness(
        data_root=tmp_path,
        policy=_readiness_policy(),
        validation_policy=_validation_policy(),
        generated_ts_ns=_NOW_NS,
    )
    assert file_report.missing_normalized_file_count == 1


def test_readiness_loaders_and_timestamp_fail_closed(tmp_path: Path) -> None:
    missing = tmp_path / "missing.toml"
    with pytest.raises(ValueError, match="readiness policy"):
        load_readiness_inputs(missing, missing)

    readiness = tmp_path / "readiness.toml"
    readiness.write_text("schema_version = 1", encoding="utf-8")
    with pytest.raises(ValueError, match="readiness policy"):
        load_readiness_inputs(readiness, missing)

    with pytest.raises(ValueError, match="timestamp cannot be negative"):
        evaluate_data_readiness(
            data_root=tmp_path,
            policy=_readiness_policy(),
            validation_policy=_validation_policy(),
            generated_ts_ns=-1,
        )


def test_monitor_records_a_typed_failure_state(tmp_path: Path) -> None:
    metrics = DataReadinessMetrics()
    monitor = DataReadinessMonitor(
        data_root=tmp_path / "missing-data",
        state_root=tmp_path / "state",
        policy=_readiness_policy(),
        validation_policy=_validation_policy(),
        metrics=metrics,
    )

    monitor.record_failure(ValueError("test"))

    state = ResearchDataReadinessState.model_validate_json(
        (tmp_path / "state" / "research" / "data-readiness.json").read_bytes()
    )
    assert state.status == "failed"
    assert state.last_error_code == "ValueError"
    assert metrics.service_healthy._value.get() == pytest.approx(0.0)


def test_readiness_report_and_state_invariants_reject_tampering(tmp_path: Path) -> None:
    _write_pair(tmp_path, 1, _NOW_NS - 30_000_000_000, _NOW_NS - 1_000_000_000)
    report = evaluate_data_readiness(
        data_root=tmp_path,
        policy=_readiness_policy(),
        validation_policy=_validation_policy(),
        generated_ts_ns=_NOW_NS,
    )
    base = report.model_dump(mode="json")
    cases: tuple[tuple[dict[str, object], str], ...] = (
        ({"required_validation_span_ns": 1}, "span does not match"),
        ({"latest_contiguous_started_ts_ns": None}, "bounds must be present"),
        ({"latest_contiguous_span_ns": 1}, "span does not match its bounds"),
        ({"remaining_validation_span_ns": 1}, "remaining readiness span"),
        ({"completion_bps": 1}, "readiness completion"),
        ({"storage_headroom_bytes": 1}, "storage headroom"),
        ({"gates": [*base["gates"], base["gates"][0]]}, "gates must be unique"),
        ({"ready_for_horizon_audit": False}, "verdict does not match"),
        ({"report_id": "0" * 64}, "identity does not match"),
    )
    for updates, match in cases:
        with pytest.raises(ValueError, match=match):
            ResearchDataReadinessReport.model_validate({**base, **updates})

    with pytest.raises(ValueError, match="running readiness state"):
        ResearchDataReadinessState(status="running", heartbeat_ts_ns=1)
    with pytest.raises(ValueError, match="failed readiness state"):
        ResearchDataReadinessState(status="failed", heartbeat_ts_ns=1)
