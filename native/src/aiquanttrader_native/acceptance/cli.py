"""Credential-free testnet acceptance evidence command line."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path

from aiquanttrader_native.acceptance.collector import (
    assemble_testnet_observation,
    load_testnet_observation,
    verify_testnet_observation,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aqt-acceptance")
    commands = parser.add_subparsers(dest="command", required=True)
    assemble = commands.add_parser("assemble")
    assemble.add_argument("--evidence-root", type=Path, required=True)
    assemble.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--evidence-root", type=Path, required=True)
    verify.add_argument("--observation", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "assemble":
            observation = assemble_testnet_observation(args.evidence_root)
            _atomic_write_new(args.output, observation.canonical_bytes() + b"\n")
            print(observation.model_dump_json())
            return 0
        if args.command == "verify":
            observation = load_testnet_observation(args.observation)
            verified = verify_testnet_observation(args.evidence_root, observation)
            print(
                json.dumps(
                    {
                        "status": "verified",
                        "rehearsal_id": verified.rehearsal_id,
                        "evidence_bundle_sha256": verified.evidence_bundle_sha256,
                    },
                    sort_keys=True,
                )
            )
            return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 2
    raise RuntimeError(f"unhandled command: {args.command}")


def _atomic_write_new(path: Path, payload: bytes) -> None:
    if not path.is_absolute():
        raise ValueError("acceptance observation output path must be absolute")
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
            raise ValueError("acceptance observation output already exists") from exc
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
