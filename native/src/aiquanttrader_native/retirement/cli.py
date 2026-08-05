"""Credential-free Phase 10 evidence and approval-verification CLI."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path

from aiquanttrader_native.retirement.approval import (
    ExpectedRetirementAction,
    RetirementApprovalError,
    RetirementApprovalPaths,
    verify_retirement_approval,
)
from aiquanttrader_native.retirement.archive import (
    assemble_legacy_archive_manifest,
    load_legacy_archive_credential_scan_policy,
    load_legacy_archive_manifest,
    verify_legacy_archive_manifest,
)
from aiquanttrader_native.retirement.collector import (
    assemble_native_production_observation,
    load_native_production_observation,
    verify_native_production_observation,
)
from aiquanttrader_native.retirement.evidence import (
    evaluate_disabled_observation,
    evaluate_retirement_readiness,
    load_retirement_policy,
)
from aiquanttrader_native.retirement.final_state import (
    assemble_legacy_final_state,
    load_legacy_final_state,
    verify_legacy_final_state,
)
from aiquanttrader_native.retirement.models import (
    DisabledObservation,
    LegacyArchiveCredentialScanPolicy,
    LegacyCleanupManifest,
    RetirementActionApproval,
    RetirementActionScope,
    RetirementPolicy,
    RetirementReadinessObservation,
)
from aiquanttrader_native.retirement.readiness import (
    assemble_retirement_readiness_observation,
    load_retirement_readiness_observation,
    verify_retirement_readiness_observation,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aqt-retirement",
        description="Evaluate retirement evidence; this command cannot stop or remove services.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    assemble_native = commands.add_parser("assemble-native")
    assemble_native.add_argument("--evidence-root", type=Path, required=True)
    assemble_native.add_argument("--policy", type=Path, required=True)
    assemble_native.add_argument("--output", type=Path, required=True)
    assemble_native.add_argument("--approval-key-id", required=True)
    assemble_native.add_argument("--approval-public-key-sha256", required=True)

    verify_native = commands.add_parser("verify-native")
    verify_native.add_argument("--evidence-root", type=Path, required=True)
    verify_native.add_argument("--observation", type=Path, required=True)
    verify_native.add_argument("--policy", type=Path, required=True)
    verify_native.add_argument("--approval-key-id", required=True)
    verify_native.add_argument("--approval-public-key-sha256", required=True)

    assemble_archive = commands.add_parser("assemble-archive")
    assemble_archive.add_argument("--evidence-root", type=Path, required=True)
    assemble_archive.add_argument("--policy", type=Path, required=True)
    assemble_archive.add_argument("--credential-scan-policy", type=Path, required=True)
    assemble_archive.add_argument("--output", type=Path, required=True)

    verify_archive = commands.add_parser("verify-archive")
    verify_archive.add_argument("--evidence-root", type=Path, required=True)
    verify_archive.add_argument("--manifest", type=Path, required=True)
    verify_archive.add_argument("--policy", type=Path, required=True)
    verify_archive.add_argument("--credential-scan-policy", type=Path, required=True)

    assemble_final_state = commands.add_parser("assemble-final-state")
    assemble_final_state.add_argument("--evidence-root", type=Path, required=True)
    assemble_final_state.add_argument("--archive-manifest", type=Path, required=True)
    assemble_final_state.add_argument("--policy", type=Path, required=True)
    assemble_final_state.add_argument("--credential-scan-policy", type=Path, required=True)
    assemble_final_state.add_argument("--output", type=Path, required=True)

    verify_final_state = commands.add_parser("verify-final-state")
    verify_final_state.add_argument("--evidence-root", type=Path, required=True)
    verify_final_state.add_argument("--archive-manifest", type=Path, required=True)
    verify_final_state.add_argument("--final-state", type=Path, required=True)
    verify_final_state.add_argument("--policy", type=Path, required=True)
    verify_final_state.add_argument("--credential-scan-policy", type=Path, required=True)

    assemble_readiness = commands.add_parser("assemble-readiness")
    assemble_readiness.add_argument("--native-evidence-root", type=Path, required=True)
    assemble_readiness.add_argument("--legacy-evidence-root", type=Path, required=True)
    assemble_readiness.add_argument("--native-observation", type=Path, required=True)
    assemble_readiness.add_argument("--archive-manifest", type=Path, required=True)
    assemble_readiness.add_argument("--final-state", type=Path, required=True)
    assemble_readiness.add_argument("--policy", type=Path, required=True)
    assemble_readiness.add_argument("--credential-scan-policy", type=Path, required=True)
    assemble_readiness.add_argument("--approval-key-id", required=True)
    assemble_readiness.add_argument("--approval-public-key-sha256", required=True)
    assemble_readiness.add_argument("--output", type=Path, required=True)

    verify_readiness = commands.add_parser("verify-readiness")
    _add_readiness_replay_arguments(verify_readiness)

    readiness = commands.add_parser("evaluate-readiness")
    _add_readiness_replay_arguments(readiness)
    readiness.add_argument("--output", type=Path)

    disabled = commands.add_parser("evaluate-disabled")
    disabled.add_argument("--observation", type=Path, required=True)
    disabled.add_argument("--policy", type=Path, required=True)
    disabled.add_argument("--output", type=Path)

    canonicalize = commands.add_parser("canonicalize-approval")
    canonicalize.add_argument("--input", type=Path, required=True)
    canonicalize.add_argument("--output", type=Path, required=True)

    cleanup = commands.add_parser("validate-cleanup-manifest")
    cleanup.add_argument("--manifest", type=Path, required=True)
    cleanup.add_argument("--output", type=Path)

    verify = commands.add_parser("verify-approval")
    verify.add_argument("--approval", type=Path, required=True)
    verify.add_argument("--signature", type=Path, required=True)
    verify.add_argument("--public-key", type=Path, required=True)
    verify.add_argument("--public-key-sha256", required=True)
    verify.add_argument("--key-id", required=True)
    verify.add_argument("--expected-retirement-id", required=True)
    verify.add_argument(
        "--expected-scope",
        choices=tuple(item.value for item in RetirementActionScope),
        required=True,
    )
    verify.add_argument("--expected-report-sha256", required=True)
    verify.add_argument("--expected-native-deployment-id", required=True)
    verify.add_argument("--expected-native-admission-id", required=True)
    verify.add_argument("--expected-archive-manifest-sha256", required=True)
    verify.add_argument("--expected-source-commit-sha", required=True)
    verify.add_argument("--expected-cleanup-manifest-sha256")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "assemble-native":
            _require_output_outside_evidence_root(args.evidence_root, args.output)
            native_observation = assemble_native_production_observation(
                args.evidence_root,
                policy=load_retirement_policy(args.policy),
                expected_key_id=args.approval_key_id,
                expected_public_key_sha256=args.approval_public_key_sha256,
            )
            _atomic_write_new(args.output, native_observation.canonical_bytes() + b"\n")
            print(native_observation.model_dump_json())
            return 0

        if args.command == "verify-native":
            native_observation = load_native_production_observation(args.observation)
            verified_native = verify_native_production_observation(
                args.evidence_root,
                native_observation,
                policy=load_retirement_policy(args.policy),
                expected_key_id=args.approval_key_id,
                expected_public_key_sha256=args.approval_public_key_sha256,
            )
            print(verified_native.model_dump_json())
            return 0

        if args.command == "assemble-archive":
            _require_output_outside_evidence_root(args.evidence_root, args.output)
            archive_manifest = assemble_legacy_archive_manifest(
                args.evidence_root,
                policy=load_retirement_policy(args.policy),
                credential_scan_policy=load_legacy_archive_credential_scan_policy(
                    args.credential_scan_policy
                ),
            )
            _atomic_write_new(args.output, archive_manifest.canonical_bytes() + b"\n")
            print(archive_manifest.model_dump_json())
            return 0

        if args.command == "verify-archive":
            archive_manifest = load_legacy_archive_manifest(args.manifest)
            verified_archive = verify_legacy_archive_manifest(
                args.evidence_root,
                archive_manifest,
                policy=load_retirement_policy(args.policy),
                credential_scan_policy=load_legacy_archive_credential_scan_policy(
                    args.credential_scan_policy
                ),
            )
            print(verified_archive.model_dump_json())
            return 0

        if args.command == "assemble-final-state":
            _require_output_outside_evidence_root(args.evidence_root, args.output)
            final_state = assemble_legacy_final_state(
                args.evidence_root,
                load_legacy_archive_manifest(args.archive_manifest),
                policy=load_retirement_policy(args.policy),
                credential_scan_policy=load_legacy_archive_credential_scan_policy(
                    args.credential_scan_policy
                ),
            )
            _atomic_write_new(args.output, final_state.canonical_bytes() + b"\n")
            print(final_state.model_dump_json())
            return 0

        if args.command == "verify-final-state":
            verified_final_state = verify_legacy_final_state(
                args.evidence_root,
                load_legacy_archive_manifest(args.archive_manifest),
                load_legacy_final_state(args.final_state),
                policy=load_retirement_policy(args.policy),
                credential_scan_policy=load_legacy_archive_credential_scan_policy(
                    args.credential_scan_policy
                ),
            )
            print(verified_final_state.model_dump_json())
            return 0

        if args.command == "assemble-readiness":
            _require_output_outside_evidence_root(args.native_evidence_root, args.output)
            _require_output_outside_evidence_root(args.legacy_evidence_root, args.output)
            readiness_observation = assemble_retirement_readiness_observation(
                args.native_evidence_root,
                args.legacy_evidence_root,
                load_native_production_observation(args.native_observation),
                load_legacy_archive_manifest(args.archive_manifest),
                load_legacy_final_state(args.final_state),
                policy=load_retirement_policy(args.policy),
                credential_scan_policy=load_legacy_archive_credential_scan_policy(
                    args.credential_scan_policy
                ),
                expected_key_id=args.approval_key_id,
                expected_public_key_sha256=args.approval_public_key_sha256,
            )
            _atomic_write_new(args.output, readiness_observation.canonical_bytes() + b"\n")
            print(readiness_observation.model_dump_json())
            return 0

        if args.command == "verify-readiness":
            policy = load_retirement_policy(args.policy)
            credential_scan_policy = load_legacy_archive_credential_scan_policy(
                args.credential_scan_policy
            )
            verified_readiness = _verify_readiness_from_args(
                args,
                policy=policy,
                credential_scan_policy=credential_scan_policy,
            )
            print(verified_readiness.model_dump_json())
            return 0

        if args.command == "evaluate-readiness":
            policy = load_retirement_policy(args.policy)
            credential_scan_policy = load_legacy_archive_credential_scan_policy(
                args.credential_scan_policy
            )
            observation = _verify_readiness_from_args(
                args,
                policy=policy,
                credential_scan_policy=credential_scan_policy,
            )
            report = evaluate_retirement_readiness(
                observation=observation,
                policy=policy,
            )
            if args.output is not None:
                _require_output_outside_evidence_root(args.native_evidence_root, args.output)
                _require_output_outside_evidence_root(args.legacy_evidence_root, args.output)
            _write_optional(args.output, report.canonical_bytes() + b"\n")
            print(report.model_dump_json())
            return 0 if report.awaiting_stop_approval else 1

        if args.command == "evaluate-disabled":
            disabled_observation = DisabledObservation.model_validate_json(
                args.observation.read_bytes()
            )
            disabled_report = evaluate_disabled_observation(
                observation=disabled_observation,
                policy=load_retirement_policy(args.policy),
            )
            _write_optional(args.output, disabled_report.canonical_bytes() + b"\n")
            print(disabled_report.model_dump_json())
            return 0 if disabled_report.awaiting_cleanup_approval else 1

        if args.command == "canonicalize-approval":
            approval = RetirementActionApproval.model_validate_json(args.input.read_bytes())
            _atomic_write_new(args.output, approval.canonical_bytes())
            print(json.dumps({"approval_sha256": approval.sha256()}, sort_keys=True))
            return 0

        if args.command == "validate-cleanup-manifest":
            manifest = LegacyCleanupManifest.model_validate_json(args.manifest.read_bytes())
            _write_optional(args.output, manifest.canonical_bytes() + b"\n")
            print(
                json.dumps(
                    {
                        "status": "valid",
                        "retirement_id": manifest.retirement_id,
                        "cleanup_manifest_sha256": manifest.sha256(),
                        "target_count": len(manifest.targets),
                    },
                    sort_keys=True,
                )
            )
            return 0

        if args.command == "verify-approval":
            verified = verify_retirement_approval(
                paths=RetirementApprovalPaths(
                    approval_path=args.approval,
                    signature_path=args.signature,
                    public_key_path=args.public_key,
                ),
                expected=ExpectedRetirementAction(
                    retirement_id=args.expected_retirement_id,
                    scope=RetirementActionScope(args.expected_scope),
                    report_sha256=args.expected_report_sha256,
                    native_deployment_id=args.expected_native_deployment_id,
                    native_admission_id=args.expected_native_admission_id,
                    archive_manifest_sha256=args.expected_archive_manifest_sha256,
                    source_commit_sha=args.expected_source_commit_sha,
                    cleanup_manifest_sha256=args.expected_cleanup_manifest_sha256,
                ),
                expected_key_id=args.key_id,
                expected_public_key_sha256=args.public_key_sha256,
            )
            print(verified.model_dump_json())
            return 0
    except (OSError, RetirementApprovalError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 2
    raise RuntimeError(f"unhandled command: {args.command}")


def _add_readiness_replay_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--native-evidence-root", type=Path, required=True)
    parser.add_argument("--legacy-evidence-root", type=Path, required=True)
    parser.add_argument("--observation", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--credential-scan-policy", type=Path, required=True)
    parser.add_argument("--approval-key-id", required=True)
    parser.add_argument("--approval-public-key-sha256", required=True)


def _verify_readiness_from_args(
    args: argparse.Namespace,
    *,
    policy: RetirementPolicy,
    credential_scan_policy: LegacyArchiveCredentialScanPolicy,
) -> RetirementReadinessObservation:
    return verify_retirement_readiness_observation(
        args.native_evidence_root,
        args.legacy_evidence_root,
        load_retirement_readiness_observation(args.observation),
        policy=policy,
        credential_scan_policy=credential_scan_policy,
        expected_key_id=args.approval_key_id,
        expected_public_key_sha256=args.approval_public_key_sha256,
    )


def _write_optional(path: Path | None, payload: bytes) -> None:
    if path is not None:
        _atomic_write_new(path, payload)


def _require_output_outside_evidence_root(root: Path, output: Path) -> None:
    if not output.is_absolute():
        raise ValueError("retirement output path must be absolute")
    resolved_root = root.resolve(strict=True)
    resolved_output = output.resolve(strict=False)
    if resolved_output == resolved_root or resolved_root in resolved_output.parents:
        raise ValueError("retirement output must be outside the immutable evidence root")


def _atomic_write_new(path: Path, payload: bytes) -> None:
    if not path.is_absolute():
        raise ValueError("retirement output path must be absolute")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise ValueError("retirement output already exists") from exc
        temporary.unlink()
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
