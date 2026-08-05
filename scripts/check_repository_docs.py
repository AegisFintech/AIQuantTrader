"""Validate repository-internal Markdown links and Mermaid entry points."""

from __future__ import annotations

import re
import sys
from pathlib import Path

LINK_PATTERN = re.compile(r"\[[^]]*\]\(([^)]+)\)")
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "#")
MERMAID_ENTRYPOINTS = ("flowchart", "sequenceDiagram", "classDiagram", "stateDiagram")
EXCLUDED_DIRECTORIES = frozenset(
    {".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".runtime", ".venv", "node_modules"}
)


def _repository_files(root: Path, pattern: str) -> list[Path]:
    return sorted(
        path
        for path in root.rglob(pattern)
        if not EXCLUDED_DIRECTORIES.intersection(path.relative_to(root).parts)
    )


def check_repository(root: Path) -> list[str]:
    failures: list[str] = []
    for document in _repository_files(root, "*.md"):
        text = document.read_text(encoding="utf-8")
        for target in LINK_PATTERN.findall(text):
            if not target or target.startswith(EXTERNAL_PREFIXES):
                continue
            relative = target.split("#", 1)[0]
            if relative and not (document.parent / relative).resolve().exists():
                failures.append(f"broken internal link: {document}: {target}")

    for diagram in _repository_files(root, "*.mmd"):
        lines = [line.strip() for line in diagram.read_text(encoding="utf-8").splitlines()]
        first = next((line for line in lines if line and not line.startswith("%%")), "")
        if not first.startswith(MERMAID_ENTRYPOINTS):
            failures.append(f"invalid Mermaid entry point: {diagram}: {first!r}")
    return failures


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    failures = check_repository(root)
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("repository documentation links and Mermaid entry points are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
