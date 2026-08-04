"""Fail-closed native deployment configuration models."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import (
    AnyHttpUrl,
    AnyUrl,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from aiquanttrader_native.domain.base import canonical_sha256

EthereumAddress = Annotated[str, StringConstraints(pattern=r"^0x[0-9a-fA-F]{40}$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class FrozenModel(BaseModel):
    """Immutable configuration with unknown-key rejection."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class DeploymentMode(StrEnum):
    RESEARCH = "research"
    TESTNET = "testnet"
    PAPER = "paper"
    SHADOW = "shadow"
    CANARY = "canary"
    PRODUCTION = "production"


class ExchangeNetwork(StrEnum):
    TESTNET = "testnet"
    MAINNET = "mainnet"


class InstrumentConfig(FrozenModel):
    venue: Literal["HYPERLIQUID"] = "HYPERLIQUID"
    instrument_id: Literal["BTC-USD-PERP.HYPERLIQUID"] = "BTC-USD-PERP.HYPERLIQUID"
    raw_symbol: Literal["BTC"] = "BTC"


def _validate_secret_reference(value: Path | None) -> Path | None:
    if value is None:
        return None
    if not value.is_absolute():
        raise ValueError("secret references must be absolute paths")
    try:
        value.relative_to("/run/secrets")
    except ValueError as exc:
        raise ValueError("secret references must be below /run/secrets") from exc
    if value == Path("/run/secrets"):
        raise ValueError("secret reference must identify a file")
    return value


class ExchangeConfig(FrozenModel):
    network: ExchangeNetwork
    http_url: AnyHttpUrl
    websocket_url: AnyUrl
    account_address: EthereumAddress | None = None
    trading_wallet_secret_path: Path | None = None
    control_wallet_secret_path: Path | None = None

    @model_validator(mode="after")
    def validate_secret_references(self) -> Self:
        trading = _validate_secret_reference(self.trading_wallet_secret_path)
        control = _validate_secret_reference(self.control_wallet_secret_path)
        if trading is not None and trading == control:
            raise ValueError("trading and control wallets must use different secret files")
        return self


class ExecutionConfig(FrozenModel):
    enabled: bool = False
    max_inflight_requests: int = Field(default=20, ge=1, le=100)
    reconcile_on_startup: bool = True
    unknown_order_timeout_ms: int = Field(default=5_000, ge=1_000, le=60_000)


class MarketDataConfig(FrozenModel):
    enabled: bool = False
    public_channels: tuple[Literal["l2Book", "trades", "bbo", "activeAssetCtx"], ...] = (
        "l2Book",
        "trades",
        "bbo",
        "activeAssetCtx",
    )
    segment_duration_seconds: int = Field(default=3_600, ge=60, le=3_600)
    sync_every_records: int = Field(default=100, ge=1, le=10_000)
    stale_after_seconds: int = Field(default=15, ge=5, le=120)
    ping_interval_seconds: int = Field(default=30, ge=5, le=50)
    max_frame_bytes: int = Field(default=8_388_608, ge=65_536, le=67_108_864)
    reconnect_initial_ms: int = Field(default=250, ge=50, le=5_000)
    reconnect_max_ms: int = Field(default=5_000, ge=250, le=60_000)
    reconnect_jitter_fraction: Decimal = Field(default=Decimal("0.2"), ge=0, le=1)
    outbound_messages_per_minute: int = Field(default=120, ge=1, le=2_000)
    minimum_free_bytes: int = Field(default=5_368_709_120, ge=67_108_864)
    minimum_free_fraction: Decimal = Field(default=Decimal("0.10"), gt=0, le=Decimal("0.50"))
    metrics_host: str = "0.0.0.0"
    metrics_port: int = Field(default=9109, ge=1024, le=65535)
    tardis_api_key_secret_path: Path | None = None

    @model_validator(mode="after")
    def validate_recorder_bounds(self) -> Self:
        if len(set(self.public_channels)) != len(self.public_channels):
            raise ValueError("market-data channels must be unique")
        if not self.public_channels:
            raise ValueError("at least one market-data channel is required")
        if self.reconnect_initial_ms > self.reconnect_max_ms:
            raise ValueError("initial reconnect delay cannot exceed maximum reconnect delay")
        if self.ping_interval_seconds >= 60:
            raise ValueError("application ping interval must remain below venue idle timeout")
        _validate_secret_reference(self.tardis_api_key_secret_path)
        return self


class RiskLimits(FrozenModel):
    """Deployment limits clamped to application-level hard ceilings."""

    daily_loss_fraction: Decimal = Field(default=Decimal("0.005"), gt=0, le=Decimal("0.02"))
    max_drawdown_fraction: Decimal = Field(default=Decimal("0.01"), gt=0, le=Decimal("0.05"))
    max_leverage: Decimal = Field(default=Decimal("1.0"), gt=0, le=Decimal("5.0"))
    max_order_notional_usd: Decimal = Field(default=Decimal("250"), gt=0, le=Decimal("10000"))
    max_inventory_notional_usd: Decimal = Field(default=Decimal("1000"), gt=0, le=Decimal("50000"))
    max_open_orders: int = Field(default=4, ge=1, le=20)
    max_orders_per_second: int = Field(default=5, ge=1, le=10)
    public_data_stale_after_ms: int = Field(default=1_500, ge=500, le=10_000)
    private_data_stale_after_ms: int = Field(default=3_000, ge=1_000, le=30_000)

    @model_validator(mode="after")
    def order_must_fit_inventory_limit(self) -> Self:
        if self.max_order_notional_usd > self.max_inventory_notional_usd:
            raise ValueError("max order notional cannot exceed max inventory notional")
        return self


class StorageConfig(FrozenModel):
    data_root: Path = Path("/var/lib/aiquanttrader/data")
    state_root: Path = Path("/var/lib/aiquanttrader/state")

    @model_validator(mode="after")
    def paths_must_be_absolute_and_distinct(self) -> Self:
        if not self.data_root.is_absolute() or not self.state_root.is_absolute():
            raise ValueError("storage roots must be absolute")
        if self.data_root == self.state_root:
            raise ValueError("data and state roots must be distinct")
        return self


class ObservabilityConfig(FrozenModel):
    health_host: str = "0.0.0.0"
    health_port: int = Field(default=9108, ge=1024, le=65535)


class ApprovalConfig(FrozenModel):
    approval_id: str | None = None
    scale_approval_id: str | None = None
    artifact_manifest_sha256: Sha256 | None = None
    manifest_path: Path | None = None
    signature_path: Path | None = None
    public_key_path: Path | None = None

    def complete_for(self, mode: DeploymentMode) -> bool:
        common = (
            self.approval_id,
            self.artifact_manifest_sha256,
            self.manifest_path,
            self.signature_path,
            self.public_key_path,
        )
        if not all(item is not None for item in common):
            return False
        return mode is not DeploymentMode.PRODUCTION or self.scale_approval_id is not None


class NativeSettings(FrozenModel):
    config_version: Literal[1] = 1
    environment: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]*$")]
    mode: DeploymentMode
    instrument: InstrumentConfig = InstrumentConfig()
    exchange: ExchangeConfig
    execution: ExecutionConfig = ExecutionConfig()
    market_data: MarketDataConfig = MarketDataConfig()
    risk: RiskLimits = RiskLimits()
    storage: StorageConfig = StorageConfig()
    observability: ObservabilityConfig = ObservabilityConfig()
    approval: ApprovalConfig = ApprovalConfig()

    @model_validator(mode="after")
    def enforce_deployment_boundary(self) -> Self:
        if (
            self.mode is DeploymentMode.TESTNET
            and self.exchange.network is not ExchangeNetwork.TESTNET
        ):
            raise ValueError("testnet mode requires the Hyperliquid testnet network")

        if (
            self.mode in {DeploymentMode.CANARY, DeploymentMode.PRODUCTION}
            and self.exchange.network is not ExchangeNetwork.MAINNET
        ):
            raise ValueError("canary and production modes require mainnet")

        if self.execution.enabled:
            allowed = {
                DeploymentMode.TESTNET,
                DeploymentMode.CANARY,
                DeploymentMode.PRODUCTION,
            }
            if self.mode not in allowed:
                raise ValueError("research, paper, and shadow modes cannot enable execution")
            if self.exchange.account_address is None:
                raise ValueError("enabled execution requires an account address")
            if self.exchange.trading_wallet_secret_path is None:
                raise ValueError("enabled execution requires a trading-wallet secret reference")

        if self.execution.enabled and self.exchange.network is ExchangeNetwork.MAINNET:
            if self.mode not in {DeploymentMode.CANARY, DeploymentMode.PRODUCTION}:
                raise ValueError("mainnet execution is restricted to canary and production modes")
            if self.exchange.control_wallet_secret_path is None:
                raise ValueError("mainnet execution requires an independent control wallet")
            if not self.approval.complete_for(self.mode):
                raise ValueError("mainnet execution requires a complete artifact-bound approval")

        return self

    @property
    def can_submit_orders(self) -> bool:
        """Return the validated execution capability, never strategy intent."""

        return self.execution.enabled

    def fingerprint(self) -> str:
        """Hash the complete non-secret effective configuration."""

        return canonical_sha256(self.model_dump(mode="json"))
