from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from aiquanttrader.backtest.models import ValidationPolicy
from aiquanttrader.cli import main
from aiquanttrader.domain.data import DataQualityPolicy
from aiquanttrader.research.readiness import evaluate_data_readiness
from aiquanttrader.research.readiness_models import (
    ResearchDataReadinessPolicy,
    ResearchDataReadinessState,
)
from aiquanttrader.service.storage import StorageExpansionPreflightReport


def test_storage_expansion_cli_writes_immutable_ready_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    now_ns = time.time_ns()
    data_root = tmp_path / "data"
    data_root.mkdir()
    readiness_report = evaluate_data_readiness(
        data_root=data_root,
        policy=ResearchDataReadinessPolicy(
            policy_id="cli-readiness",
            maximum_contiguous_gap_ns=1,
            maximum_latest_segment_age_ns=1,
            maximum_excluded_frames=0,
            minimum_free_bytes=1,
            storage_projection_safety_bps=10_000,
            data_quality_policy=DataQualityPolicy(max_classified_gap_ns=1),
        ),
        validation_policy=ValidationPolicy(
            policy_id="cli-validation",
            train_ns=10,
            purge_ns=2,
            validation_ns=4,
            embargo_ns=2,
            test_ns=4,
            step_ns=4,
            final_holdout_ns=5,
            label_horizon_ns=2,
            minimum_folds=3,
        ),
        generated_ts_ns=now_ns,
    )
    state_path = tmp_path / "data-readiness.json"
    state_path.write_bytes(
        ResearchDataReadinessState(
            status="running",
            heartbeat_ts_ns=now_ns,
            report=readiness_report,
        ).canonical_bytes()
    )
    policy_path = tmp_path / "storage.toml"
    policy_path.write_text(
        """\
schema_version = 1
policy_id = "cli-storage"
minimum_maintenance_headroom_bytes = 1
allocation_increment_bytes = 1073741824
maximum_readiness_age_ns = 180000000000
""",
        encoding="utf-8",
    )
    output = tmp_path / "preflight.json"
    arguments = [
        "storage-expansion-preflight",
        "--data-root",
        str(data_root),
        "--readiness-state",
        str(state_path),
        "--policy",
        str(policy_path),
        "--output",
        str(output),
    ]

    assert main(arguments) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "ready"
    assert summary["stage"] == "ready"
    assert summary["operator_action_required"] is False
    report = StorageExpansionPreflightReport.model_validate_json(output.read_bytes())
    assert report.report_id == summary["report_id"]
    assert report.requirement.readiness_report_id == readiness_report.report_id

    action_policy = tmp_path / "storage-action.toml"
    action_policy.write_text(
        """\
schema_version = 1
policy_id = "cli-storage-action"
minimum_maintenance_headroom_bytes = 1125899906842624
allocation_increment_bytes = 1073741824
maximum_readiness_age_ns = 180000000000
""",
        encoding="utf-8",
    )
    action_output = tmp_path / "action-preflight.json"
    action_arguments = [
        *arguments[:-4],
        "--policy",
        str(action_policy),
        "--output",
        str(action_output),
    ]
    assert main(action_arguments) == 3
    action_summary = json.loads(capsys.readouterr().out)
    assert action_summary["status"] == "action_required"
    assert action_summary["capacity_shortfall_bytes"] > 0
    assert action_summary["operator_action_required"] is True

    assert main(arguments) == 2
    failure = json.loads(capsys.readouterr().err)
    assert failure["status"] == "invalid"
    assert "immutable artifact already exists" in failure["error"]
