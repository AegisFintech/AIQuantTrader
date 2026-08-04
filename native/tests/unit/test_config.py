from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from aiquanttrader_native.config import ConfigLoadError, load_config
from aiquanttrader_native.config.models import (
    DeploymentMode,
    ExchangeNetwork,
    MarketDataConfig,
    SentinelConfig,
)

ACCOUNT = "0x" + "1" * 40
HASH = "a" * 64


@pytest.mark.parametrize(
    ("environment", "mode", "network"),
    [
        ("research", DeploymentMode.RESEARCH, ExchangeNetwork.TESTNET),
        ("testnet", DeploymentMode.TESTNET, ExchangeNetwork.TESTNET),
        ("paper", DeploymentMode.PAPER, ExchangeNetwork.MAINNET),
        ("shadow", DeploymentMode.SHADOW, ExchangeNetwork.MAINNET),
        ("canary", DeploymentMode.CANARY, ExchangeNetwork.MAINNET),
        ("production", DeploymentMode.PRODUCTION, ExchangeNetwork.MAINNET),
    ],
)
def test_checked_in_configs_are_disabled_and_valid(
    config_dir: Path,
    environment: str,
    mode: DeploymentMode,
    network: ExchangeNetwork,
) -> None:
    bundle = load_config(config_dir, environment, environ={})

    assert bundle.settings.mode is mode
    assert bundle.settings.exchange.network is network
    assert bundle.settings.instrument.instrument_id == "BTC-USD-PERP.HYPERLIQUID"
    assert not bundle.settings.execution.enabled
    assert not bundle.settings.can_submit_orders
    assert len(bundle.fingerprint) == 64


def test_testnet_execution_requires_account_and_secret(config_dir: Path) -> None:
    with pytest.raises(ConfigLoadError, match="account address"):
        load_config(
            config_dir,
            "testnet",
            environ={"AQT_NATIVE__EXECUTION__ENABLED": "true"},
        )


def test_testnet_execution_can_be_explicitly_enabled(config_dir: Path) -> None:
    bundle = load_config(
        config_dir,
        "testnet",
        environ={
            "AQT_NATIVE__EXCHANGE__ACCOUNT_ADDRESS": ACCOUNT,
            "AQT_NATIVE__EXCHANGE__TRADING_WALLET_SECRET_PATH": (
                "/run/secrets/testnet-trading-wallet"
            ),
            "AQT_NATIVE__EXCHANGE__CONTROL_WALLET_SECRET_PATH": (
                "/run/secrets/testnet-control-wallet"
            ),
            "AQT_NATIVE__EXECUTION__ENABLED": "true",
            "AQT_NATIVE__SENTINEL__ENABLED": "true",
        },
    )

    assert bundle.settings.can_submit_orders
    assert bundle.settings.exchange.account_address == ACCOUNT


def test_testnet_execution_requires_startup_reconciliation(config_dir: Path) -> None:
    with pytest.raises(ConfigLoadError, match="startup reconciliation"):
        load_config(
            config_dir,
            "testnet",
            environ={
                "AQT_NATIVE__EXCHANGE__ACCOUNT_ADDRESS": ACCOUNT,
                "AQT_NATIVE__EXCHANGE__TRADING_WALLET_SECRET_PATH": (
                    "/run/secrets/testnet-trading-wallet"
                ),
                "AQT_NATIVE__EXCHANGE__CONTROL_WALLET_SECRET_PATH": (
                    "/run/secrets/testnet-control-wallet"
                ),
                "AQT_NATIVE__EXECUTION__ENABLED": "true",
                "AQT_NATIVE__EXECUTION__RECONCILE_ON_STARTUP": "false",
                "AQT_NATIVE__SENTINEL__ENABLED": "true",
            },
        )


@pytest.mark.parametrize("environment", ["research", "paper", "shadow"])
def test_non_execution_modes_cannot_be_overridden(config_dir: Path, environment: str) -> None:
    with pytest.raises(ConfigLoadError, match="cannot enable execution"):
        load_config(
            config_dir,
            environment,
            environ={
                "AQT_NATIVE__EXCHANGE__ACCOUNT_ADDRESS": ACCOUNT,
                "AQT_NATIVE__EXCHANGE__TRADING_WALLET_SECRET_PATH": ("/run/secrets/trading-wallet"),
                "AQT_NATIVE__EXECUTION__ENABLED": "true",
                "AQT_NATIVE__SENTINEL__ENABLED": "true",
            },
        )


def test_mainnet_execution_is_unavailable_during_phase_4(config_dir: Path) -> None:
    with pytest.raises(ConfigLoadError, match="remain unavailable until Phase 9"):
        load_config(
            config_dir,
            "canary",
            environ={
                "AQT_NATIVE__EXCHANGE__ACCOUNT_ADDRESS": ACCOUNT,
                "AQT_NATIVE__EXCHANGE__TRADING_WALLET_SECRET_PATH": (
                    "/run/secrets/mainnet-trading-wallet"
                ),
                "AQT_NATIVE__EXCHANGE__CONTROL_WALLET_SECRET_PATH": (
                    "/run/secrets/mainnet-control-wallet"
                ),
                "AQT_NATIVE__EXECUTION__ENABLED": "true",
                "AQT_NATIVE__SENTINEL__ENABLED": "true",
            },
        )


def test_canary_rejects_even_complete_approval_until_phase_9(config_dir: Path) -> None:
    with pytest.raises(ConfigLoadError, match="remain unavailable until Phase 9"):
        load_config(
            config_dir,
            "canary",
            environ={
                "AQT_NATIVE__APPROVAL__APPROVAL_ID": "approval-001",
                "AQT_NATIVE__APPROVAL__ARTIFACT_MANIFEST_SHA256": HASH,
                "AQT_NATIVE__APPROVAL__MANIFEST_PATH": "/run/secrets/canary-manifest",
                "AQT_NATIVE__APPROVAL__PUBLIC_KEY_PATH": "/run/secrets/approver-public-key",
                "AQT_NATIVE__APPROVAL__SIGNATURE_PATH": "/run/secrets/canary-signature",
                "AQT_NATIVE__EXCHANGE__ACCOUNT_ADDRESS": ACCOUNT,
                "AQT_NATIVE__EXCHANGE__CONTROL_WALLET_SECRET_PATH": (
                    "/run/secrets/mainnet-control-wallet"
                ),
                "AQT_NATIVE__EXCHANGE__TRADING_WALLET_SECRET_PATH": (
                    "/run/secrets/mainnet-trading-wallet"
                ),
                "AQT_NATIVE__EXECUTION__ENABLED": "true",
                "AQT_NATIVE__SENTINEL__ENABLED": "true",
            },
        )


def test_production_approval_cannot_bypass_phase_9_lock(config_dir: Path) -> None:
    environ = {
        "AQT_NATIVE__APPROVAL__APPROVAL_ID": "approval-001",
        "AQT_NATIVE__APPROVAL__ARTIFACT_MANIFEST_SHA256": HASH,
        "AQT_NATIVE__APPROVAL__MANIFEST_PATH": "/run/secrets/production-manifest",
        "AQT_NATIVE__APPROVAL__PUBLIC_KEY_PATH": "/run/secrets/approver-public-key",
        "AQT_NATIVE__APPROVAL__SIGNATURE_PATH": "/run/secrets/production-signature",
        "AQT_NATIVE__EXCHANGE__ACCOUNT_ADDRESS": ACCOUNT,
        "AQT_NATIVE__EXCHANGE__CONTROL_WALLET_SECRET_PATH": ("/run/secrets/mainnet-control-wallet"),
        "AQT_NATIVE__EXCHANGE__TRADING_WALLET_SECRET_PATH": ("/run/secrets/mainnet-trading-wallet"),
        "AQT_NATIVE__EXECUTION__ENABLED": "true",
        "AQT_NATIVE__SENTINEL__ENABLED": "true",
    }
    with pytest.raises(ConfigLoadError, match="remain unavailable until Phase 9"):
        load_config(config_dir, "production", environ=environ)

    environ["AQT_NATIVE__APPROVAL__SCALE_APPROVAL_ID"] = "scale-001"
    with pytest.raises(ConfigLoadError, match="remain unavailable until Phase 9"):
        load_config(config_dir, "production", environ=environ)


@pytest.mark.parametrize(
    ("secret_reference", "message"),
    [
        ("not-a-secret-reference", "absolute paths"),
        ("/tmp/trading-wallet", "below /run/secrets"),
    ],
)
def test_secret_value_or_unscoped_path_is_rejected(
    config_dir: Path,
    secret_reference: str,
    message: str,
) -> None:
    with pytest.raises(ConfigLoadError, match=message):
        load_config(
            config_dir,
            "testnet",
            environ={
                "AQT_NATIVE__EXCHANGE__ACCOUNT_ADDRESS": ACCOUNT,
                "AQT_NATIVE__EXCHANGE__TRADING_WALLET_SECRET_PATH": secret_reference,
                "AQT_NATIVE__EXECUTION__ENABLED": "true",
            },
        )


def test_same_trading_and_control_wallet_is_rejected(config_dir: Path) -> None:
    secret_path = "/run/secrets/shared-wallet"
    with pytest.raises(ConfigLoadError, match="different secret files"):
        load_config(
            config_dir,
            "canary",
            environ={
                "AQT_NATIVE__EXCHANGE__TRADING_WALLET_SECRET_PATH": secret_path,
                "AQT_NATIVE__EXCHANGE__CONTROL_WALLET_SECRET_PATH": secret_path,
            },
        )


def test_unknown_override_is_rejected(config_dir: Path) -> None:
    with pytest.raises(ConfigLoadError, match="extra_forbidden"):
        load_config(
            config_dir,
            "paper",
            environ={"AQT_NATIVE__EXECUTION__UNREVIEWED_SWITCH": "true"},
        )


def test_paper_mode_rejects_every_exchange_identity_or_wallet_reference(
    config_dir: Path,
) -> None:
    for name, value in (
        ("AQT_NATIVE__EXCHANGE__ACCOUNT_ADDRESS", ACCOUNT),
        (
            "AQT_NATIVE__EXCHANGE__TRADING_WALLET_SECRET_PATH",
            "/run/secrets/mainnet-trading-wallet",
        ),
        (
            "AQT_NATIVE__EXCHANGE__CONTROL_WALLET_SECRET_PATH",
            "/run/secrets/mainnet-control-wallet",
        ),
    ):
        with pytest.raises(ConfigLoadError, match="paper mode forbids"):
            load_config(config_dir, "paper", environ={name: value})


def test_paper_engine_requires_paper_mode_and_public_data(config_dir: Path) -> None:
    with pytest.raises(ConfigLoadError, match="only in paper mode"):
        load_config(
            config_dir,
            "research",
            environ={"AQT_NATIVE__PAPER__ENABLED": "true"},
        )
    with pytest.raises(ConfigLoadError, match="requires public market data"):
        load_config(
            config_dir,
            "paper",
            environ={"AQT_NATIVE__MARKET_DATA__ENABLED": "false"},
        )


def test_malformed_structured_override_is_rejected(config_dir: Path) -> None:
    with pytest.raises(ConfigLoadError, match="invalid structured environment value"):
        load_config(
            config_dir,
            "paper",
            environ={"AQT_NATIVE__RISK__MAX_OPEN_ORDERS": "[broken"},
        )


def test_hard_risk_ceiling_is_enforced(config_dir: Path) -> None:
    with pytest.raises(ConfigLoadError, match="less than or equal to 5"):
        load_config(
            config_dir,
            "paper",
            environ={"AQT_NATIVE__RISK__MAX_LEVERAGE": "6"},
        )


def test_order_limit_cannot_exceed_inventory_limit(config_dir: Path) -> None:
    with pytest.raises(ConfigLoadError, match="cannot exceed max inventory"):
        load_config(
            config_dir,
            "paper",
            environ={
                "AQT_NATIVE__RISK__MAX_ORDER_NOTIONAL_USD": "2000",
                "AQT_NATIVE__RISK__MAX_INVENTORY_NOTIONAL_USD": "1000",
            },
        )


def test_fingerprint_is_deterministic_and_sensitive(config_dir: Path) -> None:
    first = load_config(config_dir, "paper", environ={})
    second = load_config(config_dir, "paper", environ={})
    changed = load_config(
        config_dir,
        "paper",
        environ={"AQT_NATIVE__OBSERVABILITY__HEALTH_PORT": "9200"},
    )

    assert first.fingerprint == second.fingerprint
    assert first.fingerprint != changed.fingerprint


@pytest.mark.parametrize("environment", ["../paper", "Paper", "paper.toml"])
def test_environment_name_cannot_escape_config_root(config_dir: Path, environment: str) -> None:
    with pytest.raises(ConfigLoadError, match="unsupported characters"):
        load_config(config_dir, environment, environ={})


def test_symlinked_environment_cannot_escape_config_root(tmp_path: Path) -> None:
    config_root = tmp_path / "config"
    config_root.mkdir()
    (config_root / "base.toml").write_text(
        (Path(__file__).resolve().parents[2] / "configs" / "base.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    outside = tmp_path / "paper.toml"
    outside.write_text('environment = "paper"\nmode = "paper"\n', encoding="utf-8")
    (config_root / "paper.toml").symlink_to(outside)

    with pytest.raises(ConfigLoadError, match="escapes the configuration root"):
        load_config(config_root, "paper", environ={})


def test_missing_environment_file_is_explicit(config_dir: Path) -> None:
    with pytest.raises(ConfigLoadError, match="required config file is missing"):
        load_config(config_dir, "missing", environ={})


def test_market_data_configuration_is_bounded_and_secret_referenced(config_dir: Path) -> None:
    paper = load_config(config_dir, "paper", environ={}).settings
    assert paper.market_data.enabled is True
    assert paper.market_data.sync_every_records == 1
    assert paper.execution.enabled is False
    assert paper.paper.scenario_path == Path("paper/baseline-v1.toml")
    assert paper.paper.sensitivity_scenario_paths == (Path("paper/pessimistic-v1.toml"),)

    with pytest.raises(ValidationError, match="unique"):
        MarketDataConfig(public_channels=("trades", "trades"))
    with pytest.raises(ValidationError, match="at least one"):
        MarketDataConfig(public_channels=())
    with pytest.raises(ValidationError, match="initial reconnect"):
        MarketDataConfig(reconnect_initial_ms=1_000, reconnect_max_ms=500)
    with pytest.raises(ValidationError, match="below /run/secrets"):
        MarketDataConfig(tardis_api_key_secret_path=Path("/tmp/key"))


def test_execution_wallet_names_and_endpoints_are_environment_scoped(config_dir: Path) -> None:
    base = {
        "AQT_NATIVE__EXCHANGE__ACCOUNT_ADDRESS": ACCOUNT,
        "AQT_NATIVE__EXCHANGE__TRADING_WALLET_SECRET_PATH": ("/run/secrets/testnet-trading-wallet"),
        "AQT_NATIVE__EXCHANGE__CONTROL_WALLET_SECRET_PATH": ("/run/secrets/testnet-control-wallet"),
        "AQT_NATIVE__EXECUTION__ENABLED": "true",
        "AQT_NATIVE__SENTINEL__ENABLED": "true",
    }
    with pytest.raises(ConfigLoadError, match=r"testnet-.*names"):
        load_config(
            config_dir,
            "testnet",
            environ={
                **base,
                "AQT_NATIVE__EXCHANGE__CONTROL_WALLET_SECRET_PATH": (
                    "/run/secrets/mainnet-control-wallet"
                ),
            },
        )
    with pytest.raises(ConfigLoadError, match="canonical Hyperliquid HTTP"):
        load_config(
            config_dir,
            "testnet",
            environ={**base, "AQT_NATIVE__EXCHANGE__HTTP_URL": "https://example.com"},
        )


def test_sentinel_timing_is_fail_closed() -> None:
    with pytest.raises(ValidationError, match="five seconds"):
        SentinelConfig(deadman_timeout_ms=10_000, deadman_renew_interval_ms=5_000)
    with pytest.raises(ValidationError, match="polling"):
        SentinelConfig(poll_interval_ms=5_000, heartbeat_stale_after_ms=5_000)
