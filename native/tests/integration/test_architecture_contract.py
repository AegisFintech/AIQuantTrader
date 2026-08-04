from __future__ import annotations

import os
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest


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


def test_execution_and_control_wallets_are_process_isolated(project_root: Path) -> None:
    overlay = (project_root / "compose.testnet.yaml").read_text(encoding="utf-8")
    trading = overlay.split("  trading-node:", 1)[1].split("  safety-sentinel:", 1)[0]
    sentinel = overlay.split("  safety-sentinel:", 1)[1].split("\nsecrets:", 1)[0]

    assert "source: testnet_trading_wallet" in trading
    assert "source: testnet_control_wallet" not in trading
    assert "source: testnet_control_wallet" in sentinel
    assert "source: testnet_trading_wallet" not in sentinel
    assert "native-state:/var/lib/aiquanttrader/state:ro" in sentinel
    assert "native-data:/var/lib/aiquanttrader/data" not in sentinel
    assert "mainnet" not in overlay


def test_testnet_compose_overlay_renders_when_docker_is_available(project_root: Path) -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose is unavailable")
    environment = os.environ.copy()
    environment.update(
        {
            "AQT_TESTNET_ACCOUNT_ADDRESS": "0x" + "1" * 40,
            "AQT_TESTNET_TRADING_WALLET_FILE": "/tmp/testnet-trading-wallet",
            "AQT_TESTNET_CONTROL_WALLET_FILE": "/tmp/testnet-control-wallet",
        }
    )
    subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "compose.yaml",
            "-f",
            "compose.testnet.yaml",
            "--profile",
            "execution-testnet",
            "config",
            "--quiet",
        ],
        cwd=project_root,
        env=environment,
        check=True,
    )


def test_only_gateway_calls_nautilus_and_sdk_cannot_place_orders(project_root: Path) -> None:
    source_root = project_root / "src" / "aiquanttrader_native"
    normal_order_calls: list[Path] = []
    sdk_imports: list[Path] = []
    for path in source_root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if any(
            call in source
            for call in (
                "super().submit_order(",
                "super().modify_order(",
                "super().cancel_order(",
            )
        ):
            normal_order_calls.append(path.relative_to(project_root))
        if "from hyperliquid." in source:
            sdk_imports.append(path.relative_to(project_root))

    assert normal_order_calls == [Path("src/aiquanttrader_native/execution/strategy.py")]
    assert sdk_imports == [Path("src/aiquanttrader_native/sentinel/service.py")]
    sentinel = (source_root / "sentinel" / "service.py").read_text(encoding="utf-8")
    assert "._exchange.order(" not in sentinel
    assert "._exchange.modify_order(" not in sentinel


def test_dependency_and_tool_versions_are_pinned(project_root: Path) -> None:
    with (project_root / "pyproject.toml").open("rb") as handle:
        payload = tomllib.load(handle)

    assert payload["project"]["requires-python"] == "==3.12.*"
    assert payload["tool"]["uv"]["required-version"] == "==0.11.29"
    assert "nautilus-trader==1.230.0" in payload["project"]["dependencies"]
    assert "hyperliquid-python-sdk==0.24.0" in payload["project"]["dependencies"]
    assert "hftbacktest==2.4.4" in payload["project"]["dependencies"]


def test_rust_toolchain_is_pinned_without_placeholder_crates(project_root: Path) -> None:
    repository_root = project_root.parent
    with (repository_root / "rust" / "rust-toolchain.toml").open("rb") as handle:
        toolchain = tomllib.load(handle)

    assert toolchain["toolchain"]["channel"] == "1.96.0"
    assert list((repository_root / "rust").rglob("Cargo.toml")) == []
    assert list((repository_root / "rust").rglob("Cargo.lock")) == []
    assert (repository_root / "rust" / "README.md").is_file()
