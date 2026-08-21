"""Read-only host storage expansion planning from live research requirements."""

from __future__ import annotations

import os
import shutil
import time
import tomllib
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self, cast

from pydantic import Field, StringConstraints, model_validator
from pydantic_core import to_jsonable_python

from aiquanttrader.domain.base import CanonicalValue, DomainModel, canonical_sha256
from aiquanttrader.research.readiness_models import ResearchDataReadinessState

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]
BoundedText = Annotated[str, Field(min_length=1, max_length=256)]
SYSFS_SECTOR_BYTES = 512


class StorageExpansionStage(StrEnum):
    READY = "ready"
    BLOCK_DEVICE_RESIZE_REQUIRED = "block_device_resize_required"
    PARTITION_RESIZE_REQUIRED = "partition_resize_required"
    FILESYSTEM_RESIZE_REQUIRED = "filesystem_resize_required"
    UNSUPPORTED_DEVICE_LAYOUT = "unsupported_device_layout"


class StorageExpansionPolicy(DomainModel):
    schema_version: Literal[1] = 1
    policy_id: Identifier
    minimum_maintenance_headroom_bytes: int = Field(gt=0)
    allocation_increment_bytes: int = Field(gt=0)
    maximum_readiness_age_ns: int = Field(gt=0)


class ResearchRetentionRequirement(DomainModel):
    schema_version: Literal[1] = 1
    readiness_report_id: Sha256
    generated_ts_ns: int = Field(ge=0)
    minimum_free_bytes: int = Field(gt=0)
    estimated_additional_bytes_required: int = Field(ge=0)


class BlockDeviceSnapshot(DomainModel):
    schema_version: Literal[1] = 1
    filesystem_device_name: Identifier
    filesystem_device_bytes: int = Field(gt=0)
    parent_device_name: Identifier
    parent_device_bytes: int = Field(gt=0)
    partition_index: int | None = Field(default=None, gt=0)
    logical_block_size: int = Field(gt=0)
    model: BoundedText | None = None
    serial: BoundedText | None = None

    @model_validator(mode="after")
    def validate_layout(self) -> Self:
        if self.filesystem_device_bytes > self.parent_device_bytes:
            raise ValueError("filesystem block device cannot exceed its parent")
        if self.partition_index is None and (
            self.filesystem_device_name != self.parent_device_name
            or self.filesystem_device_bytes != self.parent_device_bytes
        ):
            raise ValueError("whole-device filesystem must match its parent block device")
        return self


class HostStorageSnapshot(DomainModel):
    schema_version: Literal[1] = 1
    data_root: Annotated[str, Field(min_length=1, max_length=4096)]
    filesystem_device_id: Identifier
    filesystem_total_bytes: int = Field(gt=0)
    filesystem_used_bytes: int = Field(ge=0)
    filesystem_available_bytes: int = Field(ge=0)
    block_device: BlockDeviceSnapshot | None = None

    @model_validator(mode="after")
    def validate_usage(self) -> Self:
        if self.filesystem_used_bytes > self.filesystem_total_bytes:
            raise ValueError("filesystem used bytes exceed total bytes")
        if self.filesystem_available_bytes > self.filesystem_total_bytes:
            raise ValueError("filesystem available bytes exceed total bytes")
        if (
            self.filesystem_used_bytes + self.filesystem_available_bytes
            > self.filesystem_total_bytes
        ):
            raise ValueError("filesystem used and available bytes exceed total bytes")
        if (
            self.block_device is not None
            and self.filesystem_total_bytes > self.block_device.filesystem_device_bytes
        ):
            raise ValueError("filesystem total bytes exceed its block device")
        return self


class StorageExpansionPreflightReport(DomainModel):
    schema_version: Literal[1] = 1
    report_id: Sha256
    generated_ts_ns: int = Field(ge=0)
    policy: StorageExpansionPolicy
    requirement: ResearchRetentionRequirement
    readiness_age_ns: int = Field(ge=0)
    snapshot: HostStorageSnapshot
    research_required_available_bytes: int = Field(gt=0)
    total_required_available_bytes: int = Field(gt=0)
    capacity_shortfall_bytes: int = Field(ge=0)
    minimum_block_device_bytes: int = Field(gt=0)
    recommended_block_device_bytes: int = Field(gt=0)
    stage: StorageExpansionStage
    research_retention_ready: bool
    maintenance_headroom_ready: bool
    ready_for_expansion_closeout: bool
    operator_action_required: bool

    @model_validator(mode="after")
    def validate_derivations_and_identity(self) -> Self:
        research_required = (
            self.requirement.minimum_free_bytes
            + self.requirement.estimated_additional_bytes_required
        )
        total_required = research_required + self.policy.minimum_maintenance_headroom_bytes
        shortfall = max(0, total_required - self.snapshot.filesystem_available_bytes)
        readiness_age = self.generated_ts_ns - self.requirement.generated_ts_ns
        minimum_block_bytes = (
            self.snapshot.filesystem_total_bytes
            - self.snapshot.filesystem_available_bytes
            + total_required
        )
        recommended_block_bytes = _round_up(
            minimum_block_bytes, self.policy.allocation_increment_bytes
        )
        expected_stage = _derive_stage(self.snapshot, shortfall)
        if self.readiness_age_ns != readiness_age or readiness_age < 0:
            raise ValueError("readiness age is inconsistent")
        if self.readiness_age_ns > self.policy.maximum_readiness_age_ns:
            raise ValueError("readiness requirement is stale")
        if self.research_required_available_bytes != research_required:
            raise ValueError("research storage requirement is inconsistent")
        if self.total_required_available_bytes != total_required:
            raise ValueError("total storage requirement is inconsistent")
        if self.capacity_shortfall_bytes != shortfall:
            raise ValueError("storage capacity shortfall is inconsistent")
        if self.minimum_block_device_bytes != minimum_block_bytes:
            raise ValueError("minimum block size is inconsistent")
        if self.recommended_block_device_bytes != recommended_block_bytes:
            raise ValueError("recommended block size is inconsistent")
        if self.stage is not expected_stage:
            raise ValueError("storage expansion stage is inconsistent")
        research_ready = self.snapshot.filesystem_available_bytes >= research_required
        maintenance_ready = (
            self.snapshot.filesystem_available_bytes - research_required
            >= self.policy.minimum_maintenance_headroom_bytes
        )
        closeout = research_ready and maintenance_ready
        if self.research_retention_ready != research_ready:
            raise ValueError("research retention verdict is inconsistent")
        if self.maintenance_headroom_ready != maintenance_ready:
            raise ValueError("maintenance headroom verdict is inconsistent")
        if self.ready_for_expansion_closeout != closeout:
            raise ValueError("storage closeout verdict is inconsistent")
        if self.operator_action_required != (self.stage is not StorageExpansionStage.READY):
            raise ValueError("storage operator-action verdict is inconsistent")
        if closeout != (self.stage is StorageExpansionStage.READY):
            raise ValueError("storage stage does not match its closeout verdict")
        expected_identity = canonical_sha256(self.model_dump(mode="json", exclude={"report_id"}))
        if self.report_id != expected_identity:
            raise ValueError("storage preflight identity does not match its contents")
        return self


def load_storage_expansion_policy(path: Path) -> StorageExpansionPolicy:
    try:
        with path.resolve(strict=True).open("rb") as handle:
            return StorageExpansionPolicy.model_validate(tomllib.load(handle))
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"invalid storage expansion policy {path}: {exc}") from exc


def load_retention_requirement(
    path: Path,
    *,
    now_ns: int,
    maximum_age_ns: int,
) -> ResearchRetentionRequirement:
    try:
        state = ResearchDataReadinessState.model_validate_json(
            path.resolve(strict=True).read_bytes()
        )
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid research readiness state {path}: {exc}") from exc
    if state.status != "running" or state.report is None:
        raise ValueError("research readiness state must be running with a report")
    age_ns = now_ns - state.heartbeat_ts_ns
    if not 0 <= age_ns <= maximum_age_ns:
        raise ValueError("research readiness state is stale or from the future")
    return ResearchRetentionRequirement(
        readiness_report_id=state.report.report_id,
        generated_ts_ns=state.report.generated_ts_ns,
        minimum_free_bytes=state.report.policy.minimum_free_bytes,
        estimated_additional_bytes_required=state.report.estimated_additional_bytes_required,
    )


def _read_positive_int(path: Path, field: str) -> int:
    try:
        value = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid sysfs {field}: {path}") from exc
    if value <= 0:
        raise ValueError(f"sysfs {field} must be positive: {path}")
    return value


def _read_optional_text(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def inspect_block_device(
    major: int,
    minor: int,
    *,
    sysfs_root: Path = Path("/sys"),
) -> BlockDeviceSnapshot | None:
    link = sysfs_root / "dev" / "block" / f"{major}:{minor}"
    try:
        filesystem_device = link.resolve(strict=True)
    except OSError:
        return None
    partition_path = filesystem_device / "partition"
    partition_index = (
        _read_positive_int(partition_path, "partition index") if partition_path.is_file() else None
    )
    parent_device = filesystem_device.parent if partition_index is not None else filesystem_device
    logical_block_size = _read_positive_int(
        parent_device / "queue" / "logical_block_size", "logical block size"
    )
    filesystem_sectors = _read_positive_int(filesystem_device / "size", "device sectors")
    parent_sectors = _read_positive_int(parent_device / "size", "parent device sectors")
    return BlockDeviceSnapshot(
        filesystem_device_name=filesystem_device.name,
        filesystem_device_bytes=filesystem_sectors * SYSFS_SECTOR_BYTES,
        parent_device_name=parent_device.name,
        parent_device_bytes=parent_sectors * SYSFS_SECTOR_BYTES,
        partition_index=partition_index,
        logical_block_size=logical_block_size,
        model=_read_optional_text(parent_device / "device" / "model"),
        serial=_read_optional_text(parent_device / "device" / "serial"),
    )


def inspect_host_storage(data_root: Path) -> HostStorageSnapshot:
    try:
        root = data_root.resolve(strict=True)
        usage = shutil.disk_usage(root)
        device_number = root.stat().st_dev
    except OSError as exc:
        raise ValueError(f"cannot inspect data root {data_root}: {exc}") from exc
    major = os.major(device_number)
    minor = os.minor(device_number)
    return HostStorageSnapshot(
        data_root=str(root),
        filesystem_device_id=f"{major}:{minor}",
        filesystem_total_bytes=usage.total,
        filesystem_used_bytes=usage.used,
        filesystem_available_bytes=usage.free,
        block_device=inspect_block_device(major, minor),
    )


def _round_up(value: int, increment: int) -> int:
    return ((value + increment - 1) // increment) * increment


def _derive_stage(
    snapshot: HostStorageSnapshot,
    shortfall: int,
) -> StorageExpansionStage:
    if shortfall == 0:
        return StorageExpansionStage.READY
    block = snapshot.block_device
    if block is None:
        return StorageExpansionStage.UNSUPPORTED_DEVICE_LAYOUT
    if block.filesystem_device_bytes - snapshot.filesystem_total_bytes >= shortfall:
        return StorageExpansionStage.FILESYSTEM_RESIZE_REQUIRED
    if block.parent_device_bytes - block.filesystem_device_bytes >= shortfall:
        return StorageExpansionStage.PARTITION_RESIZE_REQUIRED
    return StorageExpansionStage.BLOCK_DEVICE_RESIZE_REQUIRED


def evaluate_storage_expansion(
    *,
    policy: StorageExpansionPolicy,
    requirement: ResearchRetentionRequirement,
    snapshot: HostStorageSnapshot,
    generated_ts_ns: int | None = None,
) -> StorageExpansionPreflightReport:
    now_ns = time.time_ns() if generated_ts_ns is None else generated_ts_ns
    if now_ns < 0:
        raise ValueError("storage preflight timestamp cannot be negative")
    readiness_age_ns = now_ns - requirement.generated_ts_ns
    if not 0 <= readiness_age_ns <= policy.maximum_readiness_age_ns:
        raise ValueError("research retention requirement is stale or from the future")
    research_required = (
        requirement.minimum_free_bytes + requirement.estimated_additional_bytes_required
    )
    total_required = research_required + policy.minimum_maintenance_headroom_bytes
    shortfall = max(0, total_required - snapshot.filesystem_available_bytes)
    minimum_block_bytes = (
        snapshot.filesystem_total_bytes - snapshot.filesystem_available_bytes + total_required
    )
    recommended_block_bytes = _round_up(minimum_block_bytes, policy.allocation_increment_bytes)
    stage = _derive_stage(snapshot, shortfall)
    research_ready = snapshot.filesystem_available_bytes >= research_required
    maintenance_ready = (
        snapshot.filesystem_available_bytes - research_required
        >= policy.minimum_maintenance_headroom_bytes
    )
    payload = {
        "schema_version": 1,
        "generated_ts_ns": now_ns,
        "policy": policy,
        "requirement": requirement,
        "readiness_age_ns": readiness_age_ns,
        "snapshot": snapshot,
        "research_required_available_bytes": research_required,
        "total_required_available_bytes": total_required,
        "capacity_shortfall_bytes": shortfall,
        "minimum_block_device_bytes": minimum_block_bytes,
        "recommended_block_device_bytes": recommended_block_bytes,
        "stage": stage,
        "research_retention_ready": research_ready,
        "maintenance_headroom_ready": maintenance_ready,
        "ready_for_expansion_closeout": shortfall == 0,
        "operator_action_required": stage is not StorageExpansionStage.READY,
    }
    identity = cast(CanonicalValue, to_jsonable_python(payload))
    return StorageExpansionPreflightReport.model_validate(
        {**payload, "report_id": canonical_sha256(identity)}
    )
