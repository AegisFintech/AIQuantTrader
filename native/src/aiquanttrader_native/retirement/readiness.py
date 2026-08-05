"""Independent assembly of the Phase 10 retirement readiness observation."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from time import time_ns

from aiquanttrader_native.retirement.collector import verify_native_production_observation
from aiquanttrader_native.retirement.final_state import verify_legacy_final_state
from aiquanttrader_native.retirement.models import (
    LegacyArchiveCredentialScanPolicy,
    LegacyArchiveManifest,
    LegacyFinalState,
    NativeProductionObservation,
    RetirementPolicy,
    RetirementReadinessObservation,
)

MAX_READINESS_OBSERVATION_BYTES = 16_777_216


def assemble_retirement_readiness_observation(
    native_evidence_root: Path,
    legacy_evidence_root: Path,
    native_observation: NativeProductionObservation,
    archive_manifest: LegacyArchiveManifest,
    final_state: LegacyFinalState,
    *,
    policy: RetirementPolicy,
    credential_scan_policy: LegacyArchiveCredentialScanPolicy,
    expected_key_id: str,
    expected_public_key_sha256: str,
) -> RetirementReadinessObservation:
    """Reverify both evidence roots and bind their current canonical observations."""

    verified_native, verified_final_state = _verify_readiness_sources(
        native_evidence_root,
        legacy_evidence_root,
        native_observation,
        archive_manifest,
        final_state,
        policy=policy,
        credential_scan_policy=credential_scan_policy,
        expected_key_id=expected_key_id,
        expected_public_key_sha256=expected_public_key_sha256,
    )
    observed_ts_ns = time_ns()
    _require_current_sources(
        verified_native,
        verified_final_state,
        policy=policy,
        checked_ts_ns=observed_ts_ns,
    )
    return _build_readiness_observation(
        verified_native,
        archive_manifest,
        verified_final_state,
        policy=policy,
        observed_ts_ns=observed_ts_ns,
    )


def _verify_readiness_sources(
    native_evidence_root: Path,
    legacy_evidence_root: Path,
    native_observation: NativeProductionObservation,
    archive_manifest: LegacyArchiveManifest,
    final_state: LegacyFinalState,
    *,
    policy: RetirementPolicy,
    credential_scan_policy: LegacyArchiveCredentialScanPolicy,
    expected_key_id: str,
    expected_public_key_sha256: str,
) -> tuple[NativeProductionObservation, LegacyFinalState]:
    verified_native = verify_native_production_observation(
        native_evidence_root,
        native_observation,
        policy=policy,
        expected_key_id=expected_key_id,
        expected_public_key_sha256=expected_public_key_sha256,
    )
    verified_final_state = verify_legacy_final_state(
        legacy_evidence_root,
        archive_manifest,
        final_state,
        policy=policy,
        credential_scan_policy=credential_scan_policy,
    )
    return verified_native, verified_final_state


def verify_retirement_readiness_observation(
    native_evidence_root: Path,
    legacy_evidence_root: Path,
    observation: RetirementReadinessObservation,
    *,
    policy: RetirementPolicy,
    credential_scan_policy: LegacyArchiveCredentialScanPolicy,
    expected_key_id: str,
    expected_public_key_sha256: str,
) -> RetirementReadinessObservation:
    """Replay an observation from both roots while authority and final state are current."""

    verified_native, verified_final_state = _verify_readiness_sources(
        native_evidence_root,
        legacy_evidence_root,
        observation.native,
        observation.archive,
        observation.legacy,
        policy=policy,
        credential_scan_policy=credential_scan_policy,
        expected_key_id=expected_key_id,
        expected_public_key_sha256=expected_public_key_sha256,
    )
    verified_ts_ns = time_ns()
    if observation.observed_ts_ns > verified_ts_ns:
        raise ValueError("retirement readiness observation is dated after verification")
    _require_current_sources(
        verified_native,
        verified_final_state,
        policy=policy,
        checked_ts_ns=verified_ts_ns,
    )
    assembled = _build_readiness_observation(
        verified_native,
        observation.archive,
        verified_final_state,
        policy=policy,
        observed_ts_ns=observation.observed_ts_ns,
    )
    if assembled != observation:
        raise ValueError("retirement readiness observation does not match its evidence roots")
    return assembled


def _require_current_sources(
    native: NativeProductionObservation,
    final_state: LegacyFinalState,
    *,
    policy: RetirementPolicy,
    checked_ts_ns: int,
) -> None:
    if checked_ts_ns >= native.authorization_expires_ts_ns:
        raise ValueError("native production authorization expired during readiness replay")
    final_state_age_ns = checked_ts_ns - final_state.captured_ts_ns
    if not 0 <= final_state_age_ns <= policy.maximum_final_state_age_ns:
        raise ValueError("final legacy state is stale after readiness replay")


def _build_readiness_observation(
    native: NativeProductionObservation,
    archive_manifest: LegacyArchiveManifest,
    final_state: LegacyFinalState,
    *,
    policy: RetirementPolicy,
    observed_ts_ns: int,
) -> RetirementReadinessObservation:
    final_state_age_ns = observed_ts_ns - final_state.captured_ts_ns
    if not 0 <= final_state_age_ns <= policy.maximum_final_state_age_ns:
        raise ValueError("final legacy state is stale at readiness observation")
    return RetirementReadinessObservation(
        retirement_id=archive_manifest.retirement_id,
        observed_ts_ns=observed_ts_ns,
        native=native,
        archive=archive_manifest,
        legacy=final_state,
    )


def load_retirement_readiness_observation(path: Path) -> RetirementReadinessObservation:
    """Load one canonical, bounded, non-symlink readiness observation."""

    payload = _read_regular(path, maximum_bytes=MAX_READINESS_OBSERVATION_BYTES)
    observation = RetirementReadinessObservation.model_validate_json(payload)
    if payload != observation.canonical_bytes() + b"\n":
        raise ValueError("retirement readiness observation is not canonical JSON")
    return observation


def _read_regular(path: Path, *, maximum_bytes: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"cannot open retirement readiness observation: {path.name}") from exc
    try:
        initial = os.fstat(descriptor)
        if not stat.S_ISREG(initial.st_mode):
            raise ValueError("retirement readiness observation must be a regular file")
        if initial.st_size <= 0 or initial.st_size > maximum_bytes:
            raise ValueError("retirement readiness observation size is invalid")
        payload = bytearray()
        remaining = initial.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1_048_576))
            if not chunk:
                break
            payload.extend(chunk)
            remaining -= len(chunk)
        final = os.fstat(descriptor)
        initial_identity = (
            initial.st_dev,
            initial.st_ino,
            initial.st_size,
            initial.st_mtime_ns,
            initial.st_ctime_ns,
        )
        final_identity = (
            final.st_dev,
            final.st_ino,
            final.st_size,
            final.st_mtime_ns,
            final.st_ctime_ns,
        )
        if len(payload) != initial.st_size or final_identity != initial_identity:
            raise ValueError("retirement readiness observation changed while read")
        return bytes(payload)
    finally:
        os.close(descriptor)
