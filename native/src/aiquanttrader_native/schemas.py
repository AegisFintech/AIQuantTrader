"""Deterministic JSON Schema export for versioned native contracts."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from aiquanttrader_native.domain.data import (
    DatasetManifest,
    NormalizedSegmentManifest,
    RawSegmentManifest,
    RecorderState,
    TardisFileManifest,
)
from aiquanttrader_native.domain.execution import (
    ExecutionJournalEvent,
    OrderIntent,
    RiskDecision,
    RiskSnapshot,
    TradingHeartbeat,
)
from aiquanttrader_native.domain.features import FeatureSnapshot
from aiquanttrader_native.domain.governance import DeploymentApproval, ExperimentManifest
from aiquanttrader_native.domain.market import DataCapabilities, MarketEvent

SchemaFactory = Callable[[], dict[str, Any]]


def _model_schema(
    model: type[
        DatasetManifest
        | DeploymentApproval
        | ExperimentManifest
        | FeatureSnapshot
        | NormalizedSegmentManifest
        | RawSegmentManifest
        | RecorderState
        | TardisFileManifest
    ],
) -> dict[str, Any]:
    return model.model_json_schema()


SCHEMAS: dict[str, SchemaFactory] = {
    "data-capabilities.schema.json": DataCapabilities.model_json_schema,
    "deployment-approval.schema.json": lambda: _model_schema(DeploymentApproval),
    "experiment.schema.json": lambda: _model_schema(ExperimentManifest),
    "execution.schema.json": lambda: TypeAdapter(
        OrderIntent | RiskSnapshot | RiskDecision | ExecutionJournalEvent | TradingHeartbeat
    ).json_schema(),
    "features.schema.json": lambda: _model_schema(FeatureSnapshot),
    "market-data.schema.json": lambda: TypeAdapter(MarketEvent).json_schema(),
    "dataset-manifest.schema.json": lambda: _model_schema(DatasetManifest),
    "normalized-segment-manifest.schema.json": lambda: _model_schema(NormalizedSegmentManifest),
    "raw-segment-manifest.schema.json": lambda: _model_schema(RawSegmentManifest),
    "recorder-state.schema.json": lambda: _model_schema(RecorderState),
    "tardis-file-manifest.schema.json": lambda: _model_schema(TardisFileManifest),
}


def render_schemas() -> dict[str, str]:
    """Render every checked-in schema with stable formatting."""

    return {
        filename: json.dumps(factory(), indent=2, sort_keys=True, ensure_ascii=True) + "\n"
        for filename, factory in sorted(SCHEMAS.items())
    }


def export_schemas(output: Path, *, check: bool) -> tuple[Path, ...]:
    """Write schemas, or verify the checked-in files byte-for-byte."""

    rendered = render_schemas()
    expected_paths = tuple(output / name for name in rendered)
    if check:
        failures: list[str] = []
        for path, content in zip(expected_paths, rendered.values(), strict=True):
            if not path.is_file():
                failures.append(f"missing schema: {path}")
            elif path.read_text(encoding="utf-8") != content:
                failures.append(f"stale schema: {path}")
        unexpected = sorted(
            path for path in output.glob("*.schema.json") if path not in expected_paths
        )
        failures.extend(f"unexpected schema: {path}" for path in unexpected)
        if failures:
            raise ValueError("; ".join(failures))
        return expected_paths

    output.mkdir(parents=True, exist_ok=True)
    for path, content in zip(expected_paths, rendered.values(), strict=True):
        path.write_text(content, encoding="utf-8")
    return expected_paths
