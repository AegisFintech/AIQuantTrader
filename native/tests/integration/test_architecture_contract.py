from __future__ import annotations

import tomllib
from pathlib import Path


def test_native_source_cannot_import_legacy_package(project_root: Path) -> None:
    offenders: list[Path] = []
    for path in (project_root / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "import aiquanttrader\n" in text or "from aiquanttrader " in text:
            offenders.append(path)
    assert offenders == []


def test_all_checked_in_environments_default_to_execution_disabled(project_root: Path) -> None:
    for path in (project_root / "configs").glob("*.toml"):
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
        assert payload.get("execution", {}).get("enabled", False) is False, path


def test_container_is_pinned_non_root_and_read_only_by_policy(project_root: Path) -> None:
    dockerfile = (project_root / "Dockerfile").read_text(encoding="utf-8")
    compose = (project_root / "compose.yaml").read_text(encoding="utf-8")

    assert "python:3.12.13-slim-bookworm@sha256:" in dockerfile
    assert "UV_PROJECT_ENVIRONMENT=/opt/aiquanttrader/.venv" in dockerfile
    assert "/opt/aiquanttrader/.venv /opt/aiquanttrader/.venv" in dockerfile
    assert "USER 65532:65532" in dockerfile
    assert "COPY ." not in dockerfile
    assert "read_only: true" in compose
    assert 'user: "65532:65532"' in compose
    assert "no-new-privileges:true" in compose
    assert "cap_drop:" in compose and "- ALL" in compose


def test_dependency_and_tool_versions_are_pinned(project_root: Path) -> None:
    with (project_root / "pyproject.toml").open("rb") as handle:
        payload = tomllib.load(handle)

    assert payload["project"]["requires-python"] == "==3.12.*"
    assert payload["tool"]["uv"]["required-version"] == "==0.11.29"
    assert "nautilus-trader==1.230.0" in payload["project"]["optional-dependencies"]["execution"]
    assert "hftbacktest==2.4.4" in payload["project"]["optional-dependencies"]["research"]


def test_rust_toolchain_is_pinned_without_placeholder_crates(project_root: Path) -> None:
    repository_root = project_root.parent
    with (repository_root / "rust" / "rust-toolchain.toml").open("rb") as handle:
        toolchain = tomllib.load(handle)

    assert toolchain["toolchain"]["channel"] == "1.96.0"
    assert list((repository_root / "rust").rglob("Cargo.toml")) == []
    assert list((repository_root / "rust").rglob("Cargo.lock")) == []
    assert (repository_root / "rust" / "README.md").is_file()
