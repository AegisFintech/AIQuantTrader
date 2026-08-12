"""Strict loading of the exact feature and strategy artifacts used by live execution."""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiquanttrader.config.loader import ConfigBundle
from aiquanttrader.features.models import FeatureEngineConfig
from aiquanttrader.strategies.market_maker import AvellanedaStoikovConfig
from aiquanttrader.strategies.scalper import OrderFlowScalperConfig
from aiquanttrader.strategies.smart_money_scalper import SmartMoneyScalperConfig

LiveKernelConfig = AvellanedaStoikovConfig | OrderFlowScalperConfig | SmartMoneyScalperConfig
MAX_LIVE_ARTIFACT_BYTES = 1_048_576


@dataclass(frozen=True, slots=True)
class LiveStrategyArtifacts:
    feature_config: FeatureEngineConfig
    strategy_config: LiveKernelConfig
    feature_config_sha256: str
    strategy_config_sha256: str
    feature_config_path: Path
    strategy_config_path: Path


def load_live_strategy_artifacts(
    config_dir: Path,
    bundle: ConfigBundle,
    *,
    approved_strategy_path: Path | None = None,
) -> LiveStrategyArtifacts:
    """Load live artifacts; mainnet callers inject the already verified bundle path."""

    settings = bundle.settings
    if not settings.execution.enabled or not settings.live_strategy.enabled:
        raise ValueError("live strategy artifacts require enabled execution and strategy")
    root = config_dir.resolve(strict=True)
    live = settings.live_strategy
    feature_path = _resolve_below(root, live.feature_config_path)
    strategy_path = (
        _resolve_regular(approved_strategy_path)
        if approved_strategy_path is not None
        else _resolve_below(root, live.strategy_config_path)
    )
    feature = FeatureEngineConfig.model_validate(_read_toml(feature_path))
    strategy_payload = _read_toml(strategy_path)
    strategy: LiveKernelConfig
    if live.strategy_id == "avellaneda-stoikov-v1":
        strategy = AvellanedaStoikovConfig.model_validate(strategy_payload)
    elif live.strategy_id == "smart-money-scalper-v1":
        strategy = SmartMoneyScalperConfig.model_validate(strategy_payload)
    else:
        strategy = OrderFlowScalperConfig.model_validate(strategy_payload)
    if strategy.strategy_id != live.strategy_id:
        raise ValueError("live strategy artifact does not match configured strategy_id")
    if strategy.order_quantity_base > settings.risk.max_order_size_base:
        raise ValueError("live strategy order quantity exceeds the hard order-size limit")
    if (
        not isinstance(strategy, SmartMoneyScalperConfig)
        and strategy.max_abs_inventory_base > settings.risk.max_position_size_base
    ):
        raise ValueError("live strategy inventory bound exceeds the hard position limit")
    if isinstance(strategy, AvellanedaStoikovConfig) and settings.risk.max_open_orders < 2:
        raise ValueError("Avellaneda-Stoikov requires capacity for one bid and one ask")
    return LiveStrategyArtifacts(
        feature_config=feature,
        strategy_config=strategy,
        feature_config_sha256=_sha256(feature_path),
        strategy_config_sha256=_sha256(strategy_path),
        feature_config_path=feature_path,
        strategy_config_path=strategy_path,
    )


def _resolve_below(root: Path, relative: Path) -> Path:
    candidate = _resolve_regular(root / relative)
    if not candidate.is_relative_to(root):
        raise ValueError(f"live artifact escapes configuration root: {relative}")
    return candidate


def _resolve_regular(path: Path | None) -> Path:
    if path is None:
        raise ValueError("live artifact path is missing")
    candidate = path.resolve(strict=True)
    if not candidate.is_file() or candidate.stat().st_size > MAX_LIVE_ARTIFACT_BYTES:
        raise ValueError(f"live artifact is not a bounded regular file: {path}")
    return candidate


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid live TOML artifact {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"live artifact must contain one TOML table: {path}")
    return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
