from __future__ import annotations

import runpy
from collections.abc import Callable
from pathlib import Path
from typing import cast

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_repository_docs.py"
check_repository = cast(
    Callable[[Path], list[str]], runpy.run_path(str(SCRIPT))["check_repository"]
)


def test_repository_checker_ignores_dependency_directories(tmp_path: Path) -> None:
    dependency_docs = tmp_path / "native" / ".venv" / "package"
    dependency_docs.mkdir(parents=True)
    (dependency_docs / "README.md").write_text("[missing](missing.py)\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("repository docs\n", encoding="utf-8")

    assert check_repository(tmp_path) == []


def test_repository_checker_reports_owned_broken_links_and_diagrams(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("[missing](missing.md)\n", encoding="utf-8")
    diagrams = tmp_path / "docs" / "diagrams"
    diagrams.mkdir(parents=True)
    (diagrams / "invalid.mmd").write_text("not-a-mermaid-entrypoint\n", encoding="utf-8")

    failures = check_repository(tmp_path)

    assert len(failures) == 2
    assert failures[0].startswith("broken internal link:")
    assert failures[1].startswith("invalid Mermaid entry point:")
