"""Operator CLI for signed, explicit, and reversible deployment admission."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path

from aiquanttrader.config import ConfigLoadError, load_config
from aiquanttrader.config.models import DeploymentMode
from aiquanttrader.domain.governance import DeploymentApproval
from aiquanttrader.governance.approval import (
    RenewalApprovalPaths,
    configured_artifact_paths,
    verify_deployment_admission,
    verify_deployment_renewal,
)
from aiquanttrader.governance.bundle import (
    load_release_bundle_spec,
    prepare_release_bundle,
    release_behavior_configuration,
)
from aiquanttrader.governance.evidence import (
    evaluate_canary_evidence,
    evaluate_testnet_evidence,
    load_canary_policy,
    load_testnet_policy,
)
from aiquanttrader.governance.ledger import DeploymentAdmissionLedger
from aiquanttrader.governance.models import (
    CanaryObservation,
    DeploymentAdmissionState,
    DeploymentAuthorizationRenewal,
    TestnetDressRehearsalObservation,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aqt-governance")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("verify", "admit"):
        command = commands.add_parser(name)
        _add_release_identity(command)
        if name == "admit":
            command.add_argument("--actor", required=True)
            command.add_argument("--reason", required=True)

    status = commands.add_parser("status")
    _add_config(status)
    status.add_argument("--deployment-id")

    for name in ("rollback", "revoke"):
        command = commands.add_parser(name)
        _add_config(command)
        command.add_argument("--deployment-id", required=True)
        command.add_argument("--actor", required=True)
        command.add_argument("--reason", required=True)

    for name in ("verify-renewal", "renew"):
        command = commands.add_parser(name)
        _add_release_identity(command)
        command.add_argument("--deployment-id", required=True)
        command.add_argument("--renewal", type=Path, required=True)
        command.add_argument("--signature", type=Path, required=True)
        command.add_argument("--public-key", type=Path, required=True)
        if name == "renew":
            command.add_argument("--actor", required=True)
            command.add_argument("--reason", required=True)

    evidence = commands.add_parser("evaluate-canary")
    _add_config(evidence)
    evidence.add_argument("--deployment-id", required=True)
    evidence.add_argument("--observation", type=Path, required=True)
    evidence.add_argument("--policy", type=Path, required=True)
    evidence.add_argument("--output", type=Path)

    testnet = commands.add_parser("evaluate-testnet")
    testnet.add_argument("--observation", type=Path, required=True)
    testnet.add_argument("--policy", type=Path, required=True)
    testnet.add_argument("--output", type=Path)

    for name in ("release-fingerprint", "prepare-release"):
        release = commands.add_parser(name)
        _add_config(release)
        release.add_argument("--spec", type=Path, required=True)
        if name == "release-fingerprint":
            release.add_argument("--output", type=Path)
        else:
            release.add_argument("--output-dir", type=Path, required=True)

    canonicalize = commands.add_parser("canonicalize-approval")
    canonicalize.add_argument("--input", type=Path, required=True)
    canonicalize.add_argument("--output", type=Path, required=True)
    canonicalize_renewal = commands.add_parser("canonicalize-renewal")
    canonicalize_renewal.add_argument("--input", type=Path, required=True)
    canonicalize_renewal.add_argument("--output", type=Path, required=True)
    return parser


def _add_config(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--environment", required=True)


def _add_release_identity(parser: argparse.ArgumentParser) -> None:
    _add_config(parser)
    parser.add_argument("--code-identity", required=True)
    parser.add_argument("--image-identity", required=True)
    parser.add_argument("--dependency-lock-path", type=Path, required=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "canonicalize-approval":
            approval = DeploymentApproval.model_validate_json(args.input.read_bytes())
            _atomic_write(args.output, approval.canonical_bytes())
            print(json.dumps({"approval_sha256": approval.sha256()}, sort_keys=True))
            return 0

        if args.command == "canonicalize-renewal":
            renewal = DeploymentAuthorizationRenewal.model_validate_json(args.input.read_bytes())
            _atomic_write(args.output, renewal.canonical_bytes())
            print(json.dumps({"renewal_sha256": renewal.sha256()}, sort_keys=True))
            return 0

        if args.command == "evaluate-testnet":
            testnet_observation = TestnetDressRehearsalObservation.model_validate_json(
                args.observation.read_bytes()
            )
            testnet_report = evaluate_testnet_evidence(
                observation=testnet_observation,
                policy=load_testnet_policy(args.policy),
            )
            if args.output is not None:
                _atomic_write(args.output, testnet_report.canonical_bytes() + b"\n")
            print(testnet_report.model_dump_json())
            return 0 if testnet_report.awaiting_canary_approval else 1

        if args.command in {"release-fingerprint", "prepare-release"}:
            release_bundle = load_config(args.config_dir, args.environment, environ={})
            release_spec = load_release_bundle_spec(args.spec)
            if args.command == "release-fingerprint":
                _settings, payload, fingerprint = release_behavior_configuration(
                    release_bundle, release_spec
                )
                if args.output is not None:
                    _atomic_write(args.output, payload)
                print(
                    json.dumps(
                        {
                            "status": "awaiting_testnet_rehearsal",
                            "behavior_configuration_sha256": fingerprint,
                        },
                        sort_keys=True,
                    )
                )
                return 0
            receipt = prepare_release_bundle(
                bundle=release_bundle,
                spec=release_spec,
                output_dir=args.output_dir,
            )
            print(receipt.model_dump_json())
            return 0

        bundle = load_config(args.config_dir, args.environment)
        ledger_path = bundle.settings.storage.state_root / "governance" / "admissions.sqlite3"
        if args.command in {"verify", "admit"}:
            admission = verify_deployment_admission(
                bundle,
                configured_artifact_paths(
                    bundle,
                    runtime_dependency_lock_path=args.dependency_lock_path,
                ),
                code_identity=args.code_identity,
                image_identity=args.image_identity,
            )
            if args.command == "verify":
                print(admission.model_dump_json())
                return 0
            ledger = DeploymentAdmissionLedger(ledger_path)
            try:
                admitted_record = ledger.admit(
                    admission,
                    actor=args.actor,
                    reason=args.reason,
                )
            finally:
                ledger.close()
            print(admitted_record.model_dump_json())
            return 0

        if args.command in {"verify-renewal", "renew"}:
            if bundle.settings.mode is not DeploymentMode.PRODUCTION:
                raise ValueError("deployment renewal is available only in production mode")
            approval_config = bundle.settings.approval
            if approval_config.public_key_id is None or approval_config.public_key_sha256 is None:
                raise ValueError("deployment renewal trust root is not configured")
            ledger = DeploymentAdmissionLedger(
                ledger_path,
                read_only=args.command == "verify-renewal",
            )
            try:
                current = ledger.get(args.deployment_id)
                if current is None:
                    raise ValueError("deployment admission is not registered")
                historical_admission = verify_deployment_admission(
                    bundle,
                    configured_artifact_paths(
                        bundle,
                        runtime_dependency_lock_path=args.dependency_lock_path,
                    ),
                    code_identity=args.code_identity,
                    image_identity=args.image_identity,
                    require_active_approval=False,
                )
                if historical_admission.admission_id != current.admission_id:
                    raise ValueError("renewal release bundle does not match the admitted identity")
                verified_renewal = verify_deployment_renewal(
                    paths=RenewalApprovalPaths(
                        renewal_path=args.renewal,
                        signature_path=args.signature,
                        public_key_path=args.public_key,
                    ),
                    current=current,
                    expected_key_id=approval_config.public_key_id,
                    expected_public_key_sha256=approval_config.public_key_sha256,
                )
                if args.command == "verify-renewal":
                    print(verified_renewal.model_dump_json())
                    return 0
                renewed = ledger.renew(
                    verified_renewal,
                    actor=args.actor,
                    reason=args.reason,
                )
            finally:
                ledger.close()
            print(renewed.model_dump_json())
            return 0

        read_only = args.command in {"status", "evaluate-canary"}
        ledger = DeploymentAdmissionLedger(ledger_path, read_only=read_only)
        try:
            if args.command == "status":
                status_record = (
                    ledger.get(args.deployment_id)
                    if args.deployment_id is not None
                    else ledger.active()
                )
                print(
                    json.dumps(None) if status_record is None else status_record.model_dump_json()
                )
                return 0
            if args.command in {"rollback", "revoke"}:
                target = (
                    DeploymentAdmissionState.ROLLED_BACK
                    if args.command == "rollback"
                    else DeploymentAdmissionState.REVOKED
                )
                deactivated_record = ledger.deactivate(
                    args.deployment_id,
                    target=target,
                    actor=args.actor,
                    reason=args.reason,
                )
                print(deactivated_record.model_dump_json())
                return 0
            if args.command == "evaluate-canary":
                evidence_record = ledger.get(args.deployment_id)
                if evidence_record is None:
                    raise ValueError("canary deployment is not registered")
                canary_observation = CanaryObservation.model_validate_json(
                    args.observation.read_bytes()
                )
                canary_report = evaluate_canary_evidence(
                    admission=evidence_record,
                    observation=canary_observation,
                    policy=load_canary_policy(args.policy),
                )
                payload = canary_report.canonical_bytes() + b"\n"
                if args.output is not None:
                    _atomic_write(args.output, payload)
                print(canary_report.model_dump_json())
                return 0 if canary_report.awaiting_production_approval else 1
        finally:
            ledger.close()
    except (ConfigLoadError, OSError, sqlite3.Error, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 2
    raise RuntimeError(f"unhandled command: {args.command}")


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
