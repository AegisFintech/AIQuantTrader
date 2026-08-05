from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
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


def test_retirement_boundary_has_evidence_commands_only(project_root: Path) -> None:
    retirement_root = project_root / "src" / "aiquanttrader_native" / "retirement"
    prohibited = (
        "import subprocess",
        "import socket",
        "import urllib",
        "from hyperliquid",
        "from nautilus_trader",
        "pm2 ",
        "apt-get",
        "docker compose",
    )
    offenders = [
        path.relative_to(project_root)
        for path in retirement_root.rglob("*.py")
        if any(token in path.read_text(encoding="utf-8") for token in prohibited)
    ]
    assert offenders == []

    cli = (retirement_root / "cli.py").read_text(encoding="utf-8")
    assert 'commands.add_parser("stop")' not in cli
    assert 'commands.add_parser("remove")' not in cli
    assert 'commands.add_parser("cleanup")' not in cli
    assert 'commands.add_parser("execute-cleanup")' not in cli
    assert 'commands.add_parser("revoke")' not in cli
    assert 'commands.add_parser("delete")' not in cli
    assert 'commands.add_parser("migrate-native")' not in cli


def test_all_checked_in_environments_default_to_execution_disabled(project_root: Path) -> None:
    for path in (project_root / "configs").glob("*.toml"):
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
        assert payload.get("execution", {}).get("enabled", False) is False, path
        assert payload.get("live_strategy", {}).get("enabled", False) is False, path


def test_container_is_pinned_non_root_and_read_only_by_policy(project_root: Path) -> None:
    dockerfile = (project_root / "Dockerfile").read_text(encoding="utf-8")
    compose = (project_root / "compose.yaml").read_text(encoding="utf-8")

    assert "python:3.12.13-slim-bookworm@sha256:" in dockerfile
    assert "UV_PROJECT_ENVIRONMENT=/opt/aiquanttrader/.venv" in dockerfile
    assert "/opt/aiquanttrader/.venv /opt/aiquanttrader/.venv" in dockerfile
    assert "USER 65532:65532" in dockerfile
    assert "COPY ." not in dockerfile
    assert "FROM builder AS research-builder" in dockerfile
    assert "uv sync --frozen --no-dev --no-editable --extra research" in dockerfile
    assert "FROM runtime-base AS research" in dockerfile
    assert 'ENTRYPOINT ["aqt-research"]' in dockerfile
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
    assert "testnet-sentinel-evidence:/var/lib/aiquanttrader/sentinel-state" in sentinel
    assert "native-data:/var/lib/aiquanttrader/data" not in sentinel
    assert "mainnet" not in overlay


def test_mainnet_overlay_is_exact_image_admission_gated_and_wallet_isolated(
    project_root: Path,
) -> None:
    overlay = (project_root / "compose.mainnet.yaml").read_text(encoding="utf-8")
    controller = overlay.split("  deployment-controller:", 1)[1].split("  trading-node:", 1)[0]
    trading = overlay.split("  trading-node:", 1)[1].split("  safety-sentinel:", 1)[0]
    sentinel = overlay.split("  safety-sentinel:", 1)[1].split("\nsecrets:", 1)[0]

    assert "@${AQT_MAINNET_IMAGE_DIGEST:" in overlay
    assert "AQT_MAINNET_LIVE_STRATEGY_ID:?set approved live strategy id" in overlay
    assert "build:" not in overlay
    assert 'profiles: ["mainnet-admission"]' in controller
    assert "secrets:" not in controller
    assert "operational-evidence-path" not in controller
    assert 'entrypoint: ["aqt-governance"]' in controller
    assert "source: mainnet_trading_wallet" in trading
    assert "source: mainnet_control_wallet" not in trading
    assert "source: mainnet_control_wallet" in sentinel
    assert "source: mainnet_trading_wallet" not in sentinel
    assert "mainnet-state:/var/lib/aiquanttrader/state:ro" in sentinel
    assert "mainnet-sentinel-evidence:/var/lib/aiquanttrader/sentinel-state" in sentinel
    assert "--operational-evidence-path" in sentinel
    assert "mainnet-data:/var/lib/aiquanttrader/data" not in sentinel
    assert "/run/approvals:ro" in controller
    assert "/run/approvals:ro" in trading
    assert "/run/approvals:ro" in sentinel

    dashboard = json.loads(
        (
            project_root / "observability" / "grafana" / "dashboards" / "production-governance.json"
        ).read_text(encoding="utf-8")
    )
    assert dashboard["uid"] == "aqt-production-governance"
    assert len(dashboard["panels"]) >= 6


def test_release_rehearsal_is_exact_image_testnet_only_and_wallet_isolated(
    project_root: Path,
) -> None:
    overlay = (project_root / "compose.rehearsal.yaml").read_text(encoding="utf-8")
    trading = overlay.split("  rehearsal-trading-node:", 1)[1].split("  rehearsal-sentinel:", 1)[0]
    sentinel = overlay.split("  rehearsal-sentinel:", 1)[1].split("\nsecrets:", 1)[0]

    assert "@${AQT_REHEARSAL_IMAGE_DIGEST:" in overlay
    assert "build:" not in overlay
    assert 'profiles: ["release-rehearsal"]' in trading
    assert 'profiles: ["release-rehearsal"]' in sentinel
    assert '"--environment", "testnet"' in trading
    assert "- testnet" in sentinel
    assert "AQT_RELEASE_COMMIT_SHA" in overlay
    assert "AQT_RELEASE_BEHAVIOR_SHA256" in overlay
    assert "AQT_REHEARSAL_STRATEGY_CONFIG_FILE:?set exact release strategy artifact" in trading
    assert "strategies/rehearsal-strategy.toml:ro" in trading
    assert "source: rehearsal_trading_wallet" in trading
    assert "source: rehearsal_control_wallet" not in trading
    assert "source: rehearsal_control_wallet" in sentinel
    assert "source: rehearsal_trading_wallet" not in sentinel
    assert "rehearsal-state:/var/lib/aiquanttrader/state:ro" in sentinel
    assert "rehearsal-sentinel-evidence:/var/lib/aiquanttrader/sentinel-state" in sentinel
    assert overlay.count("AQT_REHEARSAL_ID:?set unique rehearsal id") == 3
    assert "rehearsal-data:/var/lib/aiquanttrader/data" not in sentinel
    assert "mainnet" not in overlay.lower()


def test_paper_service_has_no_wallet_secret_or_exchange_order_capability(
    project_root: Path,
) -> None:
    compose = (project_root / "compose.yaml").read_text(encoding="utf-8")
    service = compose.split("  paper-trader:", 1)[1].split("\nvolumes:", 1)[0]
    assert "secrets:" not in service
    assert "/run/secrets" not in service
    assert "trading-wallet" not in service
    assert 'entrypoint: ["aqt-paper"]' in service

    paper_root = project_root / "src" / "aiquanttrader_native" / "paper"
    prohibited = ("from hyperliquid.", "HyperliquidLiveExecClientFactory", "submit_order(")
    offenders = [
        path.relative_to(project_root)
        for path in paper_root.rglob("*.py")
        if any(token in path.read_text(encoding="utf-8") for token in prohibited)
    ]
    assert offenders == []


def test_shadow_engine_has_no_network_secret_or_exchange_order_capability(
    project_root: Path,
) -> None:
    compose = (project_root / "compose.shadow.yaml").read_text(encoding="utf-8")
    engine = compose.split("  shadow-engine:", 1)[1].split("  shadow-observer:", 1)[0]
    gateway = compose.split("  shadow-gateway:", 1)[1].split("  shadow-engine:", 1)[0]
    observer = compose.split("  shadow-observer:", 1)[1].split("\nvolumes:", 1)[0]

    assert "network_mode: none" in engine
    assert "shadow-ingress:/var/lib/aiquanttrader/ingress:ro" in engine
    assert "ports:" not in engine
    assert "secrets:" not in engine
    assert "/run/secrets" not in engine
    assert "shadow-gateway-state:/var/lib/aiquanttrader/state" in gateway
    assert "shadow-state:/var/lib/aiquanttrader/state" not in gateway
    assert "shadow-state:/var/lib/aiquanttrader/state:ro" in observer
    assert "AQT_NATIVE_IMAGE_DIGEST" in engine

    engine_modules = ("service.py", "sink.py", "security.py")
    prohibited = (
        "from hyperliquid.",
        "from nautilus_trader.adapters.hyperliquid",
        "HyperliquidLiveExecClientFactory",
        "submit_order(",
        "websockets.connect",
    )
    shadow_root = project_root / "src" / "aiquanttrader_native" / "shadow"
    offenders = [
        name
        for name in engine_modules
        if any(token in (shadow_root / name).read_text(encoding="utf-8") for token in prohibited)
    ]
    assert offenders == []

    dashboard = json.loads(
        (project_root / "observability/grafana/dashboards/shadow-trading.json").read_text(
            encoding="utf-8"
        )
    )
    assert dashboard["uid"] == "aqt-shadow-trading"
    assert any("network_egress_capability" in str(panel) for panel in dashboard["panels"])


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


def test_shadow_compose_renders_exact_image_when_docker_is_available(project_root: Path) -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose is unavailable")
    environment = os.environ.copy()
    environment.update(
        {
            "AQT_NATIVE_IMAGE_REPOSITORY": "registry.invalid/aiquanttrader-native",
            "AQT_NATIVE_IMAGE_DIGEST": "sha256:" + "a" * 64,
            "AQT_NATIVE_CODE_IDENTITY": "ci-contract",
        }
    )
    subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "compose.shadow.yaml",
            "config",
            "--quiet",
        ],
        cwd=project_root,
        env=environment,
        check=True,
    )


def test_mainnet_compose_renders_exact_image_when_docker_is_available(project_root: Path) -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose is unavailable")
    environment = os.environ.copy()
    environment.update(
        {
            "AQT_MAINNET_IMAGE_REPOSITORY": "registry.invalid/aiquanttrader-native",
            "AQT_MAINNET_IMAGE_DIGEST": "sha256:" + "a" * 64,
            "AQT_MAINNET_ACCOUNT_ADDRESS": "0x" + "1" * 40,
            "AQT_MAINNET_DEPLOYMENT_ID": "deployment-001",
            "AQT_MAINNET_CANARY_APPROVAL_ID": "approval-001",
            "AQT_MAINNET_MANIFEST_SHA256": "b" * 64,
            "AQT_MAINNET_PUBLIC_KEY_ID": "approver-001",
            "AQT_MAINNET_PUBLIC_KEY_SHA256": "c" * 64,
            "AQT_MAINNET_ENVIRONMENT": "canary",
            "AQT_MAINNET_COMMIT_SHA": "d" * 40,
            "AQT_MAINNET_LIVE_STRATEGY_ID": "order-flow-scalper-v1",
            "AQT_APPROVAL_BUNDLE_DIR": "/tmp/approval-bundle",
            "AQT_MAINNET_TRADING_WALLET_FILE": "/tmp/mainnet-trading-wallet",
            "AQT_MAINNET_CONTROL_WALLET_FILE": "/tmp/mainnet-control-wallet",
        }
    )
    subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "compose.mainnet.yaml",
            "--profile",
            "mainnet-live",
            "config",
            "--quiet",
        ],
        cwd=project_root,
        env=environment,
        check=True,
    )


def test_rehearsal_compose_renders_exact_image_when_docker_is_available(
    project_root: Path,
) -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose is unavailable")
    environment = os.environ.copy()
    environment.update(
        {
            "AQT_REHEARSAL_ID": "rehearsal-001",
            "AQT_REHEARSAL_IMAGE_REPOSITORY": "registry.invalid/aiquanttrader-native",
            "AQT_REHEARSAL_IMAGE_DIGEST": "sha256:" + "a" * 64,
            "AQT_REHEARSAL_COMMIT_SHA": "b" * 40,
            "AQT_REHEARSAL_BEHAVIOR_SHA256": "c" * 64,
            "AQT_REHEARSAL_ACCOUNT_ADDRESS": "0x" + "1" * 40,
            "AQT_REHEARSAL_TRADING_WALLET_FILE": "/tmp/rehearsal-trading-wallet",
            "AQT_REHEARSAL_CONTROL_WALLET_FILE": "/tmp/rehearsal-control-wallet",
            "AQT_REHEARSAL_STRATEGY_CONFIG_FILE": "/tmp/rehearsal-strategy.toml",
        }
    )
    subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "compose.rehearsal.yaml",
            "--profile",
            "release-rehearsal",
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
    assert "pycryptodome==3.23.0" in payload["project"]["dependencies"]
    assert payload["project"]["optional-dependencies"]["research"] == [
        "catboost==1.2.10",
        "lightgbm==4.7.0",
        "xgboost-cpu==3.3.0",
    ]


def test_research_modules_cold_import_and_dashboard_is_valid(project_root: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import aiquanttrader_native.research.models; import aiquanttrader_native.features",
        ],
        cwd=project_root,
        check=True,
    )
    dashboard = json.loads(
        (project_root / "observability" / "grafana" / "dashboards" / "research.json").read_text(
            encoding="utf-8"
        )
    )
    assert dashboard["uid"] == "aqt-research-governance"
    assert len(dashboard["panels"]) >= 5


def test_rust_toolchain_is_pinned_without_placeholder_crates(project_root: Path) -> None:
    repository_root = project_root.parent
    with (repository_root / "rust" / "rust-toolchain.toml").open("rb") as handle:
        toolchain = tomllib.load(handle)

    assert toolchain["toolchain"]["channel"] == "1.96.0"
    assert list((repository_root / "rust").rglob("Cargo.toml")) == []
    assert list((repository_root / "rust").rglob("Cargo.lock")) == []
    assert (repository_root / "rust" / "README.md").is_file()
