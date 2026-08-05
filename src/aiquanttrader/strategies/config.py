"""Strict TOML loaders for versioned production strategy configurations."""

from __future__ import annotations

import tomllib
from pathlib import Path

from aiquanttrader.strategies.market_maker import AvellanedaStoikovConfig
from aiquanttrader.strategies.scalper import OrderFlowScalperConfig


def load_market_maker_config(path: Path) -> AvellanedaStoikovConfig:
    with path.open("rb") as handle:
        return AvellanedaStoikovConfig.model_validate(tomllib.load(handle))


def load_scalper_config(path: Path) -> OrderFlowScalperConfig:
    with path.open("rb") as handle:
        return OrderFlowScalperConfig.model_validate(tomllib.load(handle))
