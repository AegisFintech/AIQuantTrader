from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest


def test_source_uses_only_canonical_package_name(project_root: Path) -> None:
    offenders: list[Path] = []
    for path in (project_root / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "aiquanttrader_native" in text:
            offenders.append(path)
    assert offenders == []


def test_retirement_boundary_has_evidence_commands_only(project_root: Path) -> None:
    retirement_root = project_root / "src" / "aiquanttrader" / "retirement"
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


def test_storage_expansion_preflight_is_read_only_and_preserves_reserves(
    project_root: Path,
) -> None:
    source = (project_root / "src" / "aiquanttrader" / "service" / "storage.py").read_text(
        encoding="utf-8"
    )
    prohibited = (
        "import subprocess",
        "import socket",
        "import urllib",
        "boto3",
        "modify_volume",
        "growpart",
        "resize2fs",
        ".unlink(",
        "rmtree(",
    )
    assert all(token not in source for token in prohibited)

    policy_path = project_root / "configs" / "operations" / "storage-expansion-v1.toml"
    with policy_path.open("rb") as handle:
        policy = tomllib.load(handle)
    assert policy["minimum_maintenance_headroom_bytes"] == 4 * 1024**3
    assert policy["allocation_increment_bytes"] == 10 * 1024**3
    assert policy["maximum_readiness_age_ns"] == 180_000_000_000

    cli = (project_root / "src" / "aiquanttrader" / "cli.py").read_text(encoding="utf-8")
    assert '"storage-expansion-preflight"' in cli
    assert (project_root / "schemas" / "storage.schema.json").is_file()
    assert (project_root / "docs" / "operations" / "STORAGE_EXPANSION_RUNBOOK.md").is_file()
    assert (
        project_root / "docs" / "architecture" / "diagrams" / "storage-expansion-preflight.mmd"
    ).is_file()


def test_container_is_pinned_non_root_and_read_only_by_policy(project_root: Path) -> None:
    dockerfile = (project_root / "Dockerfile").read_text(encoding="utf-8")
    compose = (project_root / "compose.yaml").read_text(encoding="utf-8")

    assert "python:3.12.13-slim-bookworm@sha256:" in dockerfile
    assert "UV_PROJECT_ENVIRONMENT=/opt/aiquanttrader/.venv" in dockerfile
    assert "/opt/aiquanttrader/.venv /opt/aiquanttrader/.venv" in dockerfile
    assert "uv sync --frozen --no-dev --no-editable --no-install-project" in dockerfile
    assert "FROM build-base AS builder" in dockerfile
    assert "FROM build-base AS package-builder" in dockerfile
    assert "uv build --wheel --out-dir /build/dist" in dockerfile
    assert "--no-cache-dir --no-deps /tmp/aiquanttrader-0.1.0-py3-none-any.whl" in dockerfile
    assert "USER 65532:65532" in dockerfile
    assert "COPY ." not in dockerfile
    assert "FROM builder AS research-builder" in dockerfile
    assert "uv sync --frozen --no-dev --no-editable --extra research" in dockerfile
    assert "FROM runtime-base AS research" in dockerfile
    assert "FROM build-base AS paper-builder" in dockerfile
    assert "uv sync --frozen --only-group paper-runtime" in dockerfile
    assert "FROM runtime-base AS paper" in dockerfile
    assert "COPY --from=paper-builder" in dockerfile
    assert "FROM runtime-base AS readiness" in dockerfile
    assert 'PYTHONPATH="/opt/aiquanttrader/src"' in dockerfile
    assert 'ENTRYPOINT ["aqt-research"]' in dockerfile
    assert "read_only: true" in compose
    assert 'user: "65532:65532"' in compose
    assert "no-new-privileges:true" in compose
    assert "cap_drop:" in compose and "- ALL" in compose
    normalizer = compose.split("  market-data-normalizer:", 1)[1].split("  paper-trader:", 1)[0]
    assert "normalizer-healthcheck" in normalizer
    assert "/var/lib/aiquanttrader/state" in normalizer
    paper = compose.split("  paper-trader:", 1)[1].split("  node-exporter:", 1)[0]
    assert "aiquanttrader-native-paper:0.1.0" in paper
    assert "target: paper" in paper
    assert "aqt-paper-healthcheck" in paper
    assert "- liveness" in paper
    lightweight_probe = (project_root / "src" / "aiquanttrader" / "paper_healthcheck.py").read_text(
        encoding="utf-8"
    )
    assert "from aiquanttrader" not in lightweight_probe
    assert "import aiquanttrader" not in lightweight_probe
    assert "pydantic" not in lightweight_probe
    readiness = compose.split("  research-readiness:", 1)[1].split("  prometheus:", 1)[0]
    assert 'profiles: ["monitoring"]' in readiness
    assert "native-data:/var/lib/aiquanttrader/data:ro" in readiness
    assert "aiquanttrader.research_readiness_healthcheck" in readiness
    assert "target: readiness" in readiness
    assert "9114:9114" in readiness
    readiness_probe = (
        project_root / "src" / "aiquanttrader" / "research_readiness_healthcheck.py"
    ).read_text(encoding="utf-8")
    assert "from aiquanttrader" not in readiness_probe
    assert "import aiquanttrader" not in readiness_probe
    assert "pydantic" not in readiness_probe


def test_paper_runtime_dependency_boundary_is_explicit(project_root: Path) -> None:
    with (project_root / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)
    paper_dependencies = set(project["dependency-groups"]["paper-runtime"])
    assert paper_dependencies == {
        "duckdb==1.5.4",
        "numpy==2.2.6",
        "openai==2.47.0",
        "prometheus-client==0.25.0",
        "pydantic==2.13.4",
        "websockets==17.0.1",
        "zstandard==0.25.0",
    }

    script = """
import builtins

blocked = {"Crypto", "hftbacktest", "hyperliquid", "nautilus_trader", "pyarrow"}
original_import = builtins.__import__

def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name.split(".", 1)[0] in blocked:
        raise RuntimeError(f"paper imported prohibited dependency: {name}")
    return original_import(name, globals, locals, fromlist, level)

builtins.__import__ = guarded_import
from aiquanttrader.paper import cli
from aiquanttrader.paper.config import load_paper_artifacts
assert cli.main is not None
assert load_paper_artifacts is not None
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project_root / "src")
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=project_root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


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
    assert "trading_wallet" not in service
    assert "control_wallet" not in service
    assert "account_address" not in service
    assert "${AQT_OPENAI_API_KEY_FILE:-/dev/null}:/run/secrets/openai_api_key:ro" in service
    assert "trading-wallet" not in service
    assert 'entrypoint: ["aqt-paper"]' in service

    paper_root = project_root / "src" / "aiquanttrader" / "paper"
    prohibited = ("from hyperliquid.", "HyperliquidLiveExecClientFactory", "submit_order(")
    offenders = [
        path.relative_to(project_root)
        for path in paper_root.rglob("*.py")
        if any(token in path.read_text(encoding="utf-8") for token in prohibited)
    ]
    assert offenders == []


def test_paper_monitor_is_local_pinned_read_only_and_metric_complete(project_root: Path) -> None:
    compose = (project_root / "compose.yaml").read_text(encoding="utf-8")
    prometheus = compose.split("  prometheus:", 1)[1].split("  grafana:", 1)[0]
    grafana = compose.split("  grafana:", 1)[1].split("  node-exporter:", 1)[0]
    node_exporter = compose.split("  node-exporter:", 1)[1].split("\nvolumes:", 1)[0]

    assert "prom/prometheus:v3.13.0-distroless@sha256:" in prometheus
    assert "grafana/grafana:13.1.0@sha256:" in grafana
    assert "prom/node-exporter:v1.12.1@sha256:" in node_exporter
    for service in (prometheus, grafana, node_exporter):
        assert "read_only: true" in service
        assert 'profiles: ["monitoring"]' in service
        assert "no-new-privileges:true" in service
        assert 'cap_drop: ["ALL"]' in service
    assert '"127.0.0.1:9090:9090"' in prometheus
    assert '"127.0.0.1:3000:3000"' in grafana
    assert "GF_AUTH_ANONYMOUS_ORG_ROLE: Viewer" in grafana
    assert 'GF_SECURITY_DISABLE_INITIAL_ADMIN_CREATION: "true"' in grafana
    assert "--path.rootfs=/host" in node_exporter
    assert "/:/host/root:ro,rslave" in node_exporter

    prometheus_config = (project_root / "observability" / "prometheus.yml").read_text(
        encoding="utf-8"
    )
    assert "job_name: aiquanttrader-paper" in prometheus_config
    assert 'targets: ["paper-trader:9112"]' in prometheus_config
    assert "job_name: aiquanttrader-node" in prometheus_config
    assert 'targets: ["node-exporter:9100"]' in prometheus_config

    dashboard = json.loads(
        (project_root / "observability/grafana/dashboards/paper-trading.json").read_text(
            encoding="utf-8"
        )
    )
    rendered = json.dumps(dashboard)
    assert dashboard["uid"] == "aqt-paper-trading"
    assert len(dashboard["panels"]) >= 44
    for metric in (
        "aqt_paper_market_states_total",
        "aqt_paper_fills_total",
        "aqt_paper_pnl_usd",
        "aqt_paper_daily_loss_fraction",
        "aqt_paper_drawdown_fraction",
        "aqt_paper_stale_trades_excluded_total",
        "aqt_paper_stale_books_excluded_total",
        "aqt_paper_stale_bbo_updates_excluded_total",
        "aqt_paper_market_state_l2_depth_used",
        "aqt_paper_market_state_depth_levels",
        "aqt_paper_feed_component_fresh",
        "aqt_paper_feed_component_age_seconds",
        "aqt_paper_feed_stale_after_seconds",
        "aqt_paper_feed_depth_stale_after_seconds",
        "aqt_paper_feed_blocked",
        "aqt_paper_adaptive_forecast_ready",
        "aqt_paper_adaptive_forecast_directional_accuracy",
        "node_cpu_seconds_total",
        "node_memory_MemAvailable_bytes",
        "node_filesystem_avail_bytes",
        "node_network_receive_bytes_total",
        "node_network_transmit_bytes_total",
        "node_disk_read_bytes_total",
        "node_disk_written_bytes_total",
        "node_boot_time_seconds",
        "node_load1",
        "aiquanttrader-paper",
        "aiquanttrader-node",
    ):
        assert metric in rendered
    paper_panels = dashboard["panels"]
    paper_panel_ids = [panel["id"] for panel in paper_panels]
    assert len(paper_panel_ids) == len(set(paper_panel_ids))
    runtime_panel = next(panel for panel in paper_panels if panel["id"] == 1)
    assert "LIVE DATA / PAPER ONLY" in runtime_panel["title"]
    assert "execution.enabled=false" in runtime_panel["description"]
    server_row = next(panel for panel in paper_panels if panel["id"] == 32)
    assert server_row["title"] == "Server heartbeat and resource telemetry"
    assert server_row["gridPos"]["y"] > 69
    server_titles = {panel["title"] for panel in paper_panels if panel["gridPos"]["y"] > 69}
    assert {
        "Host CPU",
        "Host memory",
        "Root disk usage",
        "Host bandwidth in / out",
        "Processor capacity",
        "Scrape heartbeat age",
    } <= server_titles
    server_metric_panels = [panel for panel in paper_panels if panel["id"] >= 33]
    assert len(server_metric_panels) == 12
    assert all(
        panel["datasource"] == {"type": "prometheus", "uid": "prometheus"}
        for panel in server_metric_panels
    )

    platform_dashboard = json.loads(
        (project_root / "observability/grafana/dashboards/platform-health.json").read_text(
            encoding="utf-8"
        )
    )
    platform_rendered = json.dumps(platform_dashboard)
    assert platform_dashboard["uid"] == "aqt-platform-health"
    for metric in (
        "node_cpu_seconds_total",
        "node_memory_MemAvailable_bytes",
        "node_filesystem_avail_bytes",
        "node_network_receive_bytes_total",
        "node_network_transmit_bytes_total",
        "aiquanttrader-paper",
        "aqt_paper_feed_component_fresh",
        "aqt_paper_feed_component_age_seconds",
        "aqt_paper_feed_stale_after_seconds",
        "aqt_paper_feed_depth_stale_after_seconds",
    ):
        assert metric in platform_rendered


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
    shadow_root = project_root / "src" / "aiquanttrader" / "shadow"
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
    source_root = project_root / "src" / "aiquanttrader"
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

    assert normal_order_calls == [Path("src/aiquanttrader/execution/strategy.py")]
    assert sdk_imports == [Path("src/aiquanttrader/sentinel/service.py")]
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
            "import aiquanttrader.research.models; import aiquanttrader.features",
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
    assert len(dashboard["panels"]) >= 10
    rendered = json.dumps(dashboard)
    assert "aqt_research_data_current_chain_started_timestamp_seconds" in rendered
    assert "aqt_research_data_latest_continuity_break_info" in rendered
    assert "aqt_research_data_continuity_breaks" in rendered


def test_rust_toolchain_is_pinned_without_placeholder_crates(project_root: Path) -> None:
    repository_root = project_root
    with (repository_root / "rust" / "rust-toolchain.toml").open("rb") as handle:
        toolchain = tomllib.load(handle)

    assert toolchain["toolchain"]["channel"] == "1.96.0"
    assert list((repository_root / "rust").rglob("Cargo.toml")) == []
    assert list((repository_root / "rust").rglob("Cargo.lock")) == []
    assert (repository_root / "rust" / "README.md").is_file()
