"""Independent reconstruction of the final legacy MT5 account state."""

from __future__ import annotations

import hashlib
import os
import stat
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from time import time_ns

from aiquanttrader.domain.base import DomainModel
from aiquanttrader.retirement.archive import verify_legacy_archive_manifest
from aiquanttrader.retirement.models import (
    LegacyArchiveArtifact,
    LegacyArchiveArtifactKind,
    LegacyArchiveCredentialScanPolicy,
    LegacyArchiveManifest,
    LegacyBrokerAccountStateEvidence,
    LegacyFinalState,
    LegacyFinalTradeReportEvidence,
    LegacyServiceConfigurationEvidence,
    RetirementPolicy,
)

MAX_FINAL_STATE_BYTES = 16_777_216
MAX_TAR_MEMBERS = 100_000
MAX_TAR_CONTENT_BYTES = 1_099_511_627_776
FINAL_TRADE_REPORT_MEMBER = "final-state/final-trade-report.json"
FINAL_TRADE_REPORT_SOURCE_MEMBER = "final-state/final-trade-report.txt"
BROKER_ACCOUNT_STATE_MEMBER = "final-state/broker-account-state.json"
BROKER_ACCOUNT_EXPORT_MEMBER = "final-state/broker-account-export.txt"
SERVICE_CONFIGURATION_MEMBER = "final-state/service-configuration.json"
SERVICE_STATUS_MEMBER = "final-state/aiquanttrader-status.json"
ENTRY_PAUSE_FLAG_MEMBER = "final-state/aiquanttrader-entry-pause.flag"


@dataclass(frozen=True, slots=True)
class FileIdentity:
    device: int
    inode: int
    byte_count: int
    modified_ts_ns: int
    changed_ts_ns: int


def assemble_legacy_final_state(
    root: Path,
    archive_manifest: LegacyArchiveManifest,
    *,
    policy: RetirementPolicy,
    credential_scan_policy: LegacyArchiveCredentialScanPolicy,
) -> LegacyFinalState:
    """Derive one final state from a verified immutable legacy archive."""

    return _assemble_legacy_final_state(
        root,
        archive_manifest,
        policy=policy,
        credential_scan_policy=credential_scan_policy,
        assembled_ts_ns=time_ns(),
    )


def _assemble_legacy_final_state(
    root: Path,
    archive_manifest: LegacyArchiveManifest,
    *,
    policy: RetirementPolicy,
    credential_scan_policy: LegacyArchiveCredentialScanPolicy,
    assembled_ts_ns: int,
) -> LegacyFinalState:
    archive = verify_legacy_archive_manifest(
        root,
        archive_manifest,
        policy=policy,
        credential_scan_policy=credential_scan_policy,
    )
    if archive.assembled_ts_ns > assembled_ts_ns:
        raise ValueError("final legacy state cannot predate archive assembly")
    by_kind = {item.kind: item for item in archive.artifacts}
    report_artifact = by_kind[LegacyArchiveArtifactKind.FINAL_TRADE_REPORT]
    broker_artifact = by_kind[LegacyArchiveArtifactKind.BROKER_ACCOUNT_STATE]
    service_artifact = by_kind[LegacyArchiveArtifactKind.SERVICE_CONFIGURATION]
    report = _load_archive_fact(
        root,
        report_artifact,
        FINAL_TRADE_REPORT_MEMBER,
        LegacyFinalTradeReportEvidence,
    )
    broker = _load_archive_fact(
        root,
        broker_artifact,
        BROKER_ACCOUNT_STATE_MEMBER,
        LegacyBrokerAccountStateEvidence,
    )
    service = _load_archive_fact(
        root,
        service_artifact,
        SERVICE_CONFIGURATION_MEMBER,
        LegacyServiceConfigurationEvidence,
    )
    _verify_member_hash(
        root,
        report_artifact,
        FINAL_TRADE_REPORT_SOURCE_MEMBER,
        report.source_report_sha256,
    )
    _verify_member_hash(
        root,
        broker_artifact,
        BROKER_ACCOUNT_EXPORT_MEMBER,
        broker.source_export_sha256,
    )
    _verify_member_hash(
        root,
        service_artifact,
        SERVICE_STATUS_MEMBER,
        service.final_status_sha256,
    )
    for check in service.command_writer_checks:
        _verify_member_hash(
            root,
            service_artifact,
            check.evidence_member,
            check.evidence_sha256,
        )
    if service.entry_pause_file_present:
        if service.entry_pause_file_sha256 is None:
            raise ValueError("entry-pause evidence hash is missing")
        _verify_member_hash(
            root,
            service_artifact,
            ENTRY_PAUSE_FLAG_MEMBER,
            service.entry_pause_file_sha256,
        )
    _verify_capture_lineage(
        archive,
        report_artifact,
        broker_artifact,
        service_artifact,
        report,
        broker,
        service,
        policy=policy,
        assembled_ts_ns=assembled_ts_ns,
    )

    broker_positions = {item.position_id_sha256: item for item in broker.positions}
    managed_positions = {item.position_id_sha256: item for item in report.managed_positions}
    if report.mt5_reported_open_positions != len(broker_positions):
        raise ValueError("MT5 and broker account position totals differ")
    if not managed_positions.keys() <= broker_positions.keys():
        raise ValueError("managed MT5 positions are absent from the broker snapshot")
    if any(
        broker_positions[position_id].instrument_id != managed.instrument_id
        for position_id, managed in managed_positions.items()
    ):
        raise ValueError("managed MT5 position instruments differ from the broker snapshot")
    if report.entry_pause_reported != service.ea_entry_pause_reported:
        raise ValueError("MT5 report and service evidence disagree on entry pause")
    if report.final_status_sha256 != service.final_status_sha256:
        raise ValueError("MT5 report and service evidence reference different status captures")

    writer_count = sum(len(check.active_writer_ids) for check in service.command_writer_checks)
    latest_capture = max(
        report.captured_ts_ns,
        broker.captured_ts_ns,
        service.captured_ts_ns,
    )
    return LegacyFinalState(
        retirement_id=archive.retirement_id,
        captured_ts_ns=latest_capture,
        assembled_ts_ns=assembled_ts_ns,
        policy_id=policy.policy_id,
        policy_sha256=policy.sha256(),
        archive_manifest_sha256=archive.sha256(),
        archive_bundle_sha256=archive.evidence_bundle_sha256,
        account_mode=broker.account_mode,
        account_login_sha256=broker.account_login_sha256,
        broker_server_sha256=broker.broker_server_sha256,
        open_managed_positions=len(managed_positions),
        open_unmanaged_positions=len(broker_positions) - len(managed_positions),
        pending_orders=len(broker.pending_orders),
        entry_pause_active=(report.entry_pause_reported and service.entry_pause_file_present),
        command_file_writer_count=writer_count,
        final_trade_report_sha256=report_artifact.content_sha256,
        final_status_sha256=report.final_status_sha256,
        broker_account_state_sha256=broker_artifact.content_sha256,
        service_configuration_sha256=service_artifact.content_sha256,
        final_trade_report_capture_id=report.capture_id,
        broker_account_capture_id=broker.capture_id,
        service_configuration_capture_id=service.capture_id,
    )


def verify_legacy_final_state(
    root: Path,
    archive_manifest: LegacyArchiveManifest,
    final_state: LegacyFinalState,
    *,
    policy: RetirementPolicy,
    credential_scan_policy: LegacyArchiveCredentialScanPolicy,
) -> LegacyFinalState:
    """Replay a final state and enforce freshness at independent verification."""

    verified_ts_ns = time_ns()
    if final_state.assembled_ts_ns > verified_ts_ns:
        raise ValueError("final legacy state is dated after verification")
    if verified_ts_ns - final_state.captured_ts_ns > policy.maximum_final_state_age_ns:
        raise ValueError("final legacy state is stale at verification")
    assembled = _assemble_legacy_final_state(
        root,
        archive_manifest,
        policy=policy,
        credential_scan_policy=credential_scan_policy,
        assembled_ts_ns=final_state.assembled_ts_ns,
    )
    if assembled != final_state:
        raise ValueError("final legacy state does not match its retained evidence")
    return assembled


def load_legacy_final_state(path: Path) -> LegacyFinalState:
    payload = _read_regular(path, maximum_bytes=MAX_FINAL_STATE_BYTES)
    final_state = LegacyFinalState.model_validate_json(payload)
    if payload != final_state.canonical_bytes() + b"\n":
        raise ValueError("final legacy state is not canonical JSON")
    return final_state


def _verify_capture_lineage(
    archive: LegacyArchiveManifest,
    report_artifact: LegacyArchiveArtifact,
    broker_artifact: LegacyArchiveArtifact,
    service_artifact: LegacyArchiveArtifact,
    report: LegacyFinalTradeReportEvidence,
    broker: LegacyBrokerAccountStateEvidence,
    service: LegacyServiceConfigurationEvidence,
    *,
    policy: RetirementPolicy,
    assembled_ts_ns: int,
) -> None:
    if {report.retirement_id, broker.retirement_id, service.retirement_id} != {
        archive.retirement_id
    }:
        raise ValueError("final legacy state retirement identities differ")
    if (
        report.account_login_sha256 != broker.account_login_sha256
        or report.broker_server_sha256 != broker.broker_server_sha256
    ):
        raise ValueError("MT5 report and broker account identities differ")
    captures = (
        report.captured_ts_ns,
        broker.captured_ts_ns,
        service.captured_ts_ns,
    )
    bindings = (
        report_artifact.captured_ts_ns,
        broker_artifact.captured_ts_ns,
        service_artifact.captured_ts_ns,
    )
    if captures != bindings:
        raise ValueError("final legacy state capture times differ from archive bindings")
    earliest = min(captures)
    latest = max(captures)
    if latest > assembled_ts_ns:
        raise ValueError("final legacy state evidence is dated after assembly")
    if latest - earliest > policy.maximum_final_state_capture_skew_ns:
        raise ValueError("final legacy state capture skew exceeds policy")
    if assembled_ts_ns - latest > policy.maximum_final_state_age_ns:
        raise ValueError("final legacy state evidence is stale at assembly")


def _load_archive_fact[ModelT: DomainModel](
    root: Path,
    artifact: LegacyArchiveArtifact,
    member_name: str,
    model: type[ModelT],
) -> ModelT:
    path = root / artifact.relative_path
    digest, identity = _sha256_regular(path)
    if digest != artifact.content_sha256 or identity.byte_count != artifact.byte_count:
        raise ValueError(f"final legacy state archive artifact changed: {artifact.kind.value}")
    payload = _read_tar_member(path, member_name, expected_identity=identity)
    value = model.model_validate_json(payload)
    if payload != value.canonical_bytes() + b"\n":
        raise ValueError(f"final legacy state evidence is not canonical JSON: {member_name}")
    return value


def _verify_member_hash(
    root: Path,
    artifact: LegacyArchiveArtifact,
    member_name: str,
    expected_sha256: str,
) -> None:
    path = root / artifact.relative_path
    digest, identity = _sha256_regular(path)
    if digest != artifact.content_sha256 or identity.byte_count != artifact.byte_count:
        raise ValueError(f"final legacy state archive artifact changed: {artifact.kind.value}")
    payload = _read_tar_member(path, member_name, expected_identity=identity)
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError(f"final legacy state source evidence hash differs: {member_name}")


def _read_tar_member(
    path: Path,
    member_name: str,
    *,
    expected_identity: FileIdentity,
) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"cannot open final legacy state archive: {path.name}") from exc
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            initial = os.fstat(descriptor)
            if _identity(initial) != expected_identity:
                raise ValueError("final legacy state archive changed before inspection")
            payload: bytes | None = None
            member_paths: set[str] = set()
            member_count = 0
            content_bytes = 0
            try:
                with tarfile.open(fileobj=handle, mode="r|") as archive:
                    for member in archive:
                        member_count += 1
                        if member_count > MAX_TAR_MEMBERS:
                            raise ValueError("final legacy state archive has too many members")
                        member_path = PurePosixPath(member.name)
                        normalized = member_path.as_posix()
                        if (
                            member_path.is_absolute()
                            or ".." in member_path.parts
                            or normalized != member.name
                            or normalized in member_paths
                        ):
                            raise ValueError("final legacy state archive member path is unsafe")
                        member_paths.add(normalized)
                        if member.isdir():
                            continue
                        if not member.isfile():
                            raise ValueError("final legacy state archive has a non-regular member")
                        content_bytes += member.size
                        if content_bytes > MAX_TAR_CONTENT_BYTES:
                            raise ValueError("final legacy state archive content exceeds its bound")
                        if normalized == member_name:
                            if member.size <= 0 or member.size > MAX_FINAL_STATE_BYTES:
                                raise ValueError("final legacy state evidence size is invalid")
                            extracted = archive.extractfile(member)
                            if extracted is None:
                                raise ValueError("cannot read final legacy state evidence")
                            payload = extracted.read(member.size + 1)
                            if len(payload) != member.size:
                                raise ValueError("final legacy state evidence is truncated")
            except tarfile.TarError as exc:
                raise ValueError(
                    f"final legacy state artifact is not a valid tar: {path.name}"
                ) from exc
            final = os.fstat(descriptor)
            if _identity(final) != expected_identity:
                raise ValueError("final legacy state archive changed during inspection")
            if payload is None:
                raise ValueError(f"final legacy state archive member is missing: {member_name}")
            return payload
    finally:
        os.close(descriptor)


def _read_regular(path: Path, *, maximum_bytes: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"cannot open final legacy state evidence: {path.name}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("final legacy state evidence must be a regular file")
        if metadata.st_size <= 0 or metadata.st_size > maximum_bytes:
            raise ValueError("final legacy state evidence size is invalid")
        payload = bytearray()
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1_048_576))
            if not chunk:
                break
            payload.extend(chunk)
            remaining -= len(chunk)
        if len(payload) != metadata.st_size or _identity(os.fstat(descriptor)) != _identity(
            metadata
        ):
            raise ValueError("final legacy state evidence changed while read")
        return bytes(payload)
    finally:
        os.close(descriptor)


def _sha256_regular(path: Path) -> tuple[str, FileIdentity]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"cannot hash final legacy state archive: {path.name}") from exc
    try:
        initial = os.fstat(descriptor)
        if not stat.S_ISREG(initial.st_mode) or initial.st_size <= 0:
            raise ValueError("final legacy state archive must be a non-empty regular file")
        digest = hashlib.sha256()
        observed = 0
        while True:
            chunk = os.read(descriptor, 1_048_576)
            if not chunk:
                break
            digest.update(chunk)
            observed += len(chunk)
        final = os.fstat(descriptor)
        if observed != initial.st_size or _identity(final) != _identity(initial):
            raise ValueError("final legacy state archive changed while hashed")
        return digest.hexdigest(), _identity(initial)
    finally:
        os.close(descriptor)


def _identity(metadata: os.stat_result) -> FileIdentity:
    return FileIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        byte_count=metadata.st_size,
        modified_ts_ns=metadata.st_mtime_ns,
        changed_ts_ns=metadata.st_ctime_ns,
    )
