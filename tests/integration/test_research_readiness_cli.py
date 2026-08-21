from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from aiquanttrader.domain.data import SegmentFinalizationReason
from aiquanttrader.market_data.raw import RawSegmentWriter
from aiquanttrader.market_data.storage import normalize_segment
from aiquanttrader.research.cli import main as research_main
from aiquanttrader.research.readiness_models import ResearchDataReadinessReport
from aiquanttrader.research_readiness_cli import main as readiness_main


def test_data_readiness_cli_evaluates_real_normalized_segment_without_training(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_root = tmp_path / "data"
    now_ns = time.time_ns()
    writer = RawSegmentWriter(
        data_root,
        network="mainnet",
        connection_id="readiness-test",
        started_at_ns=now_ns,
        sync_every_records=1,
    )
    writer.append(b'{"channel":"pong"}', receive_ts_ns=now_ns + 1, monotonic_ts_ns=1)
    finalized = writer.finalize(SegmentFinalizationReason.ROTATION)
    normalize_segment(
        finalized.segment_path,
        output_root=data_root,
        quarantine_root=data_root / "quarantine" / "raw-corrupt",
    )

    readiness_policy = tmp_path / "readiness.toml"
    readiness_policy.write_text(
        """\
schema_version = 1
policy_id = "integration-readiness"
maximum_contiguous_gap_ns = 1000000000
maximum_latest_segment_age_ns = 10000000000
maximum_excluded_frames = 0
minimum_free_bytes = 1
storage_projection_safety_bps = 10000

[data_quality_policy]
schema_version = 1
max_classified_gap_ns = 1000000000
max_schema_errors = 0
max_crossed_books = 0
max_timestamp_regressions = 0
max_duplicates = 0
reject_unexplained_gaps = true
""",
        encoding="utf-8",
    )
    validation_policy = tmp_path / "validation.toml"
    validation_policy.write_text(
        """\
schema_version = 1
policy_id = "integration-validation"
train_ns = 10
purge_ns = 2
validation_ns = 4
embargo_ns = 2
test_ns = 4
step_ns = 4
final_holdout_ns = 5
label_horizon_ns = 2
minimum_folds = 3
""",
        encoding="utf-8",
    )
    output = tmp_path / "readiness-report.json"

    result = readiness_main(
        [
            "evaluate",
            "--data-root",
            str(data_root),
            "--policy",
            str(readiness_policy),
            "--validation-policy",
            str(validation_policy),
            "--output",
            str(output),
        ]
    )

    assert result == 3
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "collecting"
    assert summary["model_training_authorized"] is False
    report = ResearchDataReadinessReport.model_validate_json(output.read_bytes())
    assert report.paired_segment_count == 1
    assert not report.ready_for_horizon_audit
    assert report.model_training_authorized is False

    research_output = tmp_path / "research-cli-report.json"
    assert (
        research_main(
            [
                "data-readiness",
                "--data-root",
                str(data_root),
                "--policy",
                str(readiness_policy),
                "--validation-policy",
                str(validation_policy),
                "--output",
                str(research_output),
            ]
        )
        == 3
    )
    assert ResearchDataReadinessReport.model_validate_json(research_output.read_bytes())
