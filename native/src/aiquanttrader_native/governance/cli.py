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

from aiquanttrader_native.config import ConfigLoadError, load_config
from aiquanttrader_native.domain.governance import DeploymentApproval
from aiquanttrader_native.governance.approval import (
    configured_artifact_paths,
    verify_deployment_admission,
)
from aiquanttrader_native.governance.bundle import (
    load_release_bundle_spec,
    prepare_release_bundle,
    release_behavior_configuration,
)
from aiquanttrader_native.governance.evidence import (
    evaluate_canary_evidence,
    evaluate_testnet_evidence,
    load_canary_policy,
    load_testnet_policy,
)
from aiquanttrader_native.governance.ledger import DeploymentAdmissionLedger
from aiquanttrader_native.governance.models import (
    CanaryObservation,
    DeploymentAdmissionState,
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
