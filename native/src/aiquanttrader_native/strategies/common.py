"""Side-effect-free strategy input, transition, and replay contracts."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, Protocol

from pydantic import Field

from aiquanttrader_native.backtest.kernel import KernelDecision
from aiquanttrader_native.domain.base import DomainModel
from aiquanttrader_native.features.models import MicrostructureSnapshot


class StrategyInput(DomainModel):
    features: MicrostructureSnapshot
    funding_rate: Decimal = Decimal("0")
    movement_forecast_bps: Decimal = Decimal("0")
    fill_forecast_bid: Annotated[Decimal, Field(ge=0, le=1)] | None = None
    fill_forecast_ask: Annotated[Decimal, Field(ge=0, le=1)] | None = None
    spread_expansion_forecast_bps: Annotated[Decimal, Field(ge=0)] = Decimal("0")
    estimated_taker_fee_bps: Annotated[Decimal, Field(ge=0)] = Decimal("4.5")
    estimated_slippage_bps: Annotated[Decimal, Field(ge=0)] = Decimal("1")
    model_artifact_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class StrategyTransition[MemoryT]:
    memory: MemoryT
    decision: KernelDecision


class StrategyKernel[MemoryT](Protocol):
    def decide(self, state: StrategyInput, memory: MemoryT) -> StrategyTransition[MemoryT]: ...


@dataclass(frozen=True, slots=True)
class StrategyTrace[MemoryT]:
    final_memory: MemoryT
    decisions: tuple[KernelDecision, ...]


def replay_strategy[MemoryT](
    *,
    kernel: StrategyKernel[MemoryT],
    initial_memory: MemoryT,
    states: Iterable[StrategyInput],
) -> StrategyTrace[MemoryT]:
    memory = initial_memory
    decisions: list[KernelDecision] = []
    for state in states:
        transition = kernel.decide(state, memory)
        memory = transition.memory
        decisions.append(transition.decision)
    return StrategyTrace(final_memory=memory, decisions=tuple(decisions))
