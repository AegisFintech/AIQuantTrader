"""Auditable negative controls over immutable feature evidence."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Literal

import pyarrow.parquet as pq

from aiquanttrader.backtest.kernel import StrategyAction
from aiquanttrader.backtest.models import ExecutionScenario
from aiquanttrader.features.models import (
    MODEL_FEATURE_SCHEMA,
    FeatureDatasetManifest,
    MicrostructureSnapshot,
)
from aiquanttrader.market_data.io import sha256_file
from aiquanttrader.research.models import NoSignalControlReport
from aiquanttrader.strategies.common import StrategyInput
from aiquanttrader.strategies.scalper import (
    OrderFlowScalperConfig,
    OrderFlowScalperKernel,
    ScalperMemory,
)

NO_SIGNAL_CONTROL_ID: Literal["neutral-alpha-order-flow-v1"] = "neutral-alpha-order-flow-v1"
_NEUTRAL_ALPHA = {
    "book_imbalance": Decimal("0"),
    "trade_flow_imbalance": Decimal("0"),
    "mid_return_bps": Decimal("0"),
}


def run_no_signal_control(
    *,
    feature_path: Path,
    feature_manifest_path: Path,
    strategy: OrderFlowScalperConfig,
    scenario: ExecutionScenario,
) -> NoSignalControlReport:
    """Replay neutral alpha through the real kernel without inventing a zero count."""

    manifest = FeatureDatasetManifest.model_validate_json(feature_manifest_path.read_bytes())
    if sha256_file(feature_path) != manifest.file_sha256:
        raise ValueError("feature dataset does not match its immutable manifest")
    if manifest.feature_schema_sha256 != MODEL_FEATURE_SCHEMA.sha256():
        raise ValueError("feature dataset schema is not supported by the no-signal control")

    kernel = OrderFlowScalperKernel(strategy)
    memory = ScalperMemory()
    observations = 0
    ready_observations = 0
    decisions = 0
    first_receive_ts_ns: int | None = None
    last_receive_ts_ns: int | None = None
    parquet = pq.ParquetFile(feature_path)
    for batch in parquet.iter_batches(batch_size=4_096):
        for row in batch.to_pylist():
            snapshot = MicrostructureSnapshot.model_validate(row)
            observations += 1
            ready_observations += int(snapshot.ready)
            if first_receive_ts_ns is None:
                first_receive_ts_ns = snapshot.receive_ts_ns
            last_receive_ts_ns = snapshot.receive_ts_ns
            neutral = snapshot.model_copy(update=_NEUTRAL_ALPHA)
            transition = kernel.decide(
                StrategyInput(
                    features=neutral,
                    movement_forecast_bps=Decimal("0"),
                    estimated_maker_fee_bps=max(scenario.maker_fee_bps, Decimal("0")),
                    estimated_taker_fee_bps=max(scenario.taker_fee_bps, Decimal("0")),
                    estimated_slippage_bps=scenario.taker_slippage_bps,
                ),
                memory,
            )
            memory = transition.memory
            decision = transition.decision
            if (
                decision.action is not StrategyAction.HOLD
                or decision.submit
                or decision.cancel_intent_ids
            ):
                decisions += 1

    if observations != manifest.row_count:
        raise ValueError("feature dataset row count does not match its immutable manifest")
    if first_receive_ts_ns != manifest.first_receive_ts_ns:
        raise ValueError("feature dataset first timestamp does not match its manifest")
    if last_receive_ts_ns != manifest.last_receive_ts_ns:
        raise ValueError("feature dataset last timestamp does not match its manifest")
    assert first_receive_ts_ns is not None
    assert last_receive_ts_ns is not None
    return NoSignalControlReport(
        control_id=NO_SIGNAL_CONTROL_ID,
        feature_dataset_sha256=manifest.feature_dataset_id,
        feature_file_sha256=manifest.file_sha256,
        feature_schema_sha256=manifest.feature_schema_sha256,
        strategy_configuration_sha256=strategy.sha256(),
        scenario_sha256=scenario.sha256(),
        observation_count=observations,
        ready_observation_count=ready_observations,
        decision_count=decisions,
        first_receive_ts_ns=first_receive_ts_ns,
        last_receive_ts_ns=last_receive_ts_ns,
    )
