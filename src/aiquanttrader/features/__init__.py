"""Causal incremental feature contracts and engine."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiquanttrader.features.engine import IncrementalFeatureEngine, replay_features
from aiquanttrader.features.models import (
    MODEL_FEATURE_SCHEMA,
    FeatureDatasetManifest,
    FeatureDefinition,
    FeatureEngineConfig,
    FeatureSchema,
    InventoryState,
    MicrostructureSnapshot,
    VolatilityRegime,
)

if TYPE_CHECKING:
    from aiquanttrader.features.storage import write_feature_dataset

__all__ = [
    "MODEL_FEATURE_SCHEMA",
    "FeatureDatasetManifest",
    "FeatureDefinition",
    "FeatureEngineConfig",
    "FeatureSchema",
    "IncrementalFeatureEngine",
    "InventoryState",
    "MicrostructureSnapshot",
    "VolatilityRegime",
    "replay_features",
    "write_feature_dataset",
]


def __getattr__(name: str) -> object:
    if name == "write_feature_dataset":
        from aiquanttrader.features.storage import write_feature_dataset

        return write_feature_dataset
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
