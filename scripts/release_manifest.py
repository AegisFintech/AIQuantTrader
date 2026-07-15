#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aiquanttrader import release_manifest  # noqa: E402


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build AIQuantTrader release manifests.")
    parser.add_argument("--no-ea-manifest", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--ea-out", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        manifest_path = release_manifest.write_manifest(ROOT, args.out)
        print(f"[OK] manifest -> {display_path(manifest_path)}")
        if not args.no_ea_manifest:
            ea_manifest_path = release_manifest.write_ea_manifest(ROOT, args.ea_out)
            print(f"[OK] ea_manifest -> {display_path(ea_manifest_path)}")
        manifest = release_manifest.load_release_manifest(manifest_path)
        if not manifest.get("git_sha"):
            print("[NOTE] git metadata unavailable; git fields are empty", file=sys.stderr)
        if args.json:
            print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
