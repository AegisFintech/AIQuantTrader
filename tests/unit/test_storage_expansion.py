from __future__ import annotations

import json
from pathlib import Path

import pytest

from aiquanttrader.backtest.models import ValidationPolicy
from aiquanttrader.domain.data import DataQualityPolicy
from aiquanttrader.research.readiness import evaluate_data_readiness
from aiquanttrader.research.readiness_models import (
    ResearchDataReadinessPolicy,
    ResearchDataReadinessState,
)
from aiquanttrader.service.storage import (
    BlockDeviceSnapshot,
    HostStorageSnapshot,
    ResearchRetentionRequirement,
    StorageExpansionPolicy,
    StorageExpansionPreflightReport,
    StorageExpansionStage,
    evaluate_storage_expansion,
    inspect_block_device,
    inspect_host_storage,
    load_retention_requirement,
    load_storage_expansion_policy,
)

GIB = 1 << 30
NOW_NS = 2_000_000_000_000


def _policy() -> StorageExpansionPolicy:
    return StorageExpansionPolicy(
        policy_id="test-storage-expansion",
        minimum_maintenance_headroom_bytes=4 * GIB,
        allocation_increment_bytes=10 * GIB,
        maximum_readiness_age_ns=180_000_000_000,
    )


def _requirement() -> ResearchRetentionRequirement:
    return ResearchRetentionRequirement(
        readiness_report_id="a" * 64,
        generated_ts_ns=NOW_NS,
        minimum_free_bytes=5 * GIB,
        estimated_additional_bytes_required=15 * GIB,
    )


def _block(*, partition_bytes: int, parent_bytes: int) -> BlockDeviceSnapshot:
    return BlockDeviceSnapshot(
        filesystem_device_name="nvme0n1p1",
        filesystem_device_bytes=partition_bytes,
        parent_device_name="nvme0n1",
        parent_device_bytes=parent_bytes,
        partition_index=1,
        logical_block_size=512,
        model="Amazon Elastic Block Store",
        serial="vol00000000000000000",
    )


def _snapshot(
    *,
    filesystem_total_gib: int = 30,
    available_gib: int = 6,
    partition_gib: int = 30,
    parent_gib: int = 30,
    block_device: bool = True,
) -> HostStorageSnapshot:
    return HostStorageSnapshot(
        data_root="/var/lib/docker/volumes/native-data/_data",
        filesystem_device_id="259:1",
        filesystem_total_bytes=filesystem_total_gib * GIB,
        filesystem_used_bytes=22 * GIB,
        filesystem_available_bytes=available_gib * GIB,
        block_device=(
            _block(partition_bytes=partition_gib * GIB, parent_bytes=parent_gib * GIB)
            if block_device
            else None
        ),
    )


@pytest.mark.parametrize(
    ("snapshot", "expected_stage"),
    (
        (_snapshot(), StorageExpansionStage.BLOCK_DEVICE_RESIZE_REQUIRED),
        (
            _snapshot(parent_gib=50),
            StorageExpansionStage.PARTITION_RESIZE_REQUIRED,
        ),
        (
            _snapshot(partition_gib=50, parent_gib=50),
            StorageExpansionStage.FILESYSTEM_RESIZE_REQUIRED,
        ),
        (
            _snapshot(
                filesystem_total_gib=50,
                available_gib=26,
                partition_gib=50,
                parent_gib=50,
            ),
            StorageExpansionStage.READY,
        ),
        (
            _snapshot(block_device=False),
            StorageExpansionStage.UNSUPPORTED_DEVICE_LAYOUT,
        ),
    ),
)
def test_preflight_reports_each_expansion_stage(
    snapshot: HostStorageSnapshot,
    expected_stage: StorageExpansionStage,
) -> None:
    report = evaluate_storage_expansion(
        policy=_policy(),
        requirement=_requirement(),
        snapshot=snapshot,
        generated_ts_ns=NOW_NS + 1,
    )

    assert report.stage is expected_stage
    assert report.research_required_available_bytes == 20 * GIB
    assert report.total_required_available_bytes == 24 * GIB
    assert report.minimum_block_device_bytes == 48 * GIB
    assert report.recommended_block_device_bytes == 50 * GIB
    assert report.operator_action_required is (expected_stage is not StorageExpansionStage.READY)
    assert report.ready_for_expansion_closeout is (expected_stage is StorageExpansionStage.READY)
    assert StorageExpansionPreflightReport.model_validate_json(report.canonical_bytes()) == report


def test_preflight_rejects_stale_time_and_tampered_derivations() -> None:
    with pytest.raises(ValueError, match="stale or from the future"):
        evaluate_storage_expansion(
            policy=_policy(),
            requirement=_requirement(),
            snapshot=_snapshot(),
            generated_ts_ns=NOW_NS - 1,
        )

    report = evaluate_storage_expansion(
        policy=_policy(),
        requirement=_requirement(),
        snapshot=_snapshot(),
        generated_ts_ns=NOW_NS,
    )
    payload = report.model_dump(mode="json")
    cases = (
        ({"readiness_age_ns": 1}, "readiness age"),
        ({"capacity_shortfall_bytes": 1}, "shortfall"),
        ({"minimum_block_device_bytes": 1}, "minimum block size"),
        ({"recommended_block_device_bytes": 10 * GIB}, "recommended block size"),
        ({"stage": "ready"}, "stage is inconsistent"),
        ({"report_id": "0" * 64}, "identity does not match"),
    )
    for update, match in cases:
        with pytest.raises(ValueError, match=match):
            StorageExpansionPreflightReport.model_validate({**payload, **update})


def test_block_and_host_snapshot_invariants_reject_impossible_layouts() -> None:
    with pytest.raises(ValueError, match="cannot exceed its parent"):
        _block(partition_bytes=31 * GIB, parent_bytes=30 * GIB)
    with pytest.raises(ValueError, match="whole-device filesystem"):
        BlockDeviceSnapshot(
            filesystem_device_name="nvme0n1p1",
            filesystem_device_bytes=30 * GIB,
            parent_device_name="nvme0n1",
            parent_device_bytes=30 * GIB,
            logical_block_size=512,
        )
    with pytest.raises(ValueError, match="used and available"):
        HostStorageSnapshot(
            data_root="/data",
            filesystem_device_id="1:1",
            filesystem_total_bytes=10,
            filesystem_used_bytes=6,
            filesystem_available_bytes=5,
        )
    with pytest.raises(ValueError, match="exceed its block device"):
        HostStorageSnapshot(
            data_root="/data",
            filesystem_device_id="1:1",
            filesystem_total_bytes=31 * GIB,
            filesystem_used_bytes=1,
            filesystem_available_bytes=1,
            block_device=_block(partition_bytes=30 * GIB, parent_bytes=30 * GIB),
        )


def test_sysfs_inspection_uses_fixed_512_byte_sector_units(tmp_path: Path) -> None:
    sysfs = tmp_path / "sys"
    parent = sysfs / "devices" / "pci0000:00" / "block" / "nvme0n1"
    partition = parent / "nvme0n1p1"
    (parent / "queue").mkdir(parents=True)
    (parent / "device").mkdir()
    partition.mkdir()
    (parent / "queue" / "logical_block_size").write_text("4096\n", encoding="utf-8")
    (parent / "size").write_text("4194304\n", encoding="utf-8")
    (partition / "size").write_text("2097152\n", encoding="utf-8")
    (partition / "partition").write_text("1\n", encoding="utf-8")
    (parent / "device" / "model").write_text("Test NVMe\n", encoding="utf-8")
    (parent / "device" / "serial").write_text("vol-test\n", encoding="utf-8")
    link = sysfs / "dev" / "block" / "259:1"
    link.parent.mkdir(parents=True)
    link.symlink_to(partition)

    snapshot = inspect_block_device(259, 1, sysfs_root=sysfs)

    assert snapshot is not None
    assert snapshot.filesystem_device_bytes == GIB
    assert snapshot.parent_device_bytes == 2 * GIB
    assert snapshot.logical_block_size == 4096
    assert snapshot.partition_index == 1
    assert snapshot.model == "Test NVMe"
    assert inspect_block_device(259, 2, sysfs_root=sysfs) is None


def test_policy_and_readiness_loaders_fail_closed(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.toml"
    policy_path.write_text(
        """\
schema_version = 1
policy_id = "test-storage-expansion"
minimum_maintenance_headroom_bytes = 4294967296
allocation_increment_bytes = 10737418240
maximum_readiness_age_ns = 180000000000
""",
        encoding="utf-8",
    )
    assert load_storage_expansion_policy(policy_path) == _policy()
    with pytest.raises(ValueError, match="storage expansion policy"):
        load_storage_expansion_policy(tmp_path / "missing.toml")

    readiness_policy = ResearchDataReadinessPolicy(
        policy_id="test-readiness",
        maximum_contiguous_gap_ns=1,
        maximum_latest_segment_age_ns=1,
        maximum_excluded_frames=0,
        minimum_free_bytes=5 * GIB,
        storage_projection_safety_bps=10_000,
        data_quality_policy=DataQualityPolicy(max_classified_gap_ns=1),
    )
    validation_policy = ValidationPolicy(
        policy_id="test-validation",
        train_ns=10,
        purge_ns=2,
        validation_ns=4,
        embargo_ns=2,
        test_ns=4,
        step_ns=4,
        final_holdout_ns=5,
        label_horizon_ns=2,
        minimum_folds=3,
    )
    data_root = tmp_path / "data"
    data_root.mkdir()
    report = evaluate_data_readiness(
        data_root=data_root,
        policy=readiness_policy,
        validation_policy=validation_policy,
        generated_ts_ns=NOW_NS,
    )
    state_path = tmp_path / "data-readiness.json"
    state_path.write_bytes(
        ResearchDataReadinessState(
            status="running",
            heartbeat_ts_ns=NOW_NS,
            report=report,
        ).canonical_bytes()
    )

    requirement = load_retention_requirement(
        state_path,
        now_ns=NOW_NS + 1,
        maximum_age_ns=2,
    )
    assert requirement.readiness_report_id == report.report_id
    assert requirement.minimum_free_bytes == 5 * GIB
    with pytest.raises(ValueError, match="stale or from the future"):
        load_retention_requirement(state_path, now_ns=NOW_NS + 3, maximum_age_ns=2)
    state_path.write_text(json.dumps({"status": "failed"}), encoding="utf-8")
    with pytest.raises(ValueError, match="readiness state"):
        load_retention_requirement(state_path, now_ns=NOW_NS, maximum_age_ns=2)


def test_host_inspection_is_read_only_and_requires_existing_root(tmp_path: Path) -> None:
    snapshot = inspect_host_storage(tmp_path)
    assert snapshot.data_root == str(tmp_path.resolve())
    assert snapshot.filesystem_total_bytes > 0
    assert snapshot.filesystem_available_bytes > 0
    with pytest.raises(ValueError, match="cannot inspect data root"):
        inspect_host_storage(tmp_path / "missing")
