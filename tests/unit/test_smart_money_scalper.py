from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from aiquanttrader.backtest.kernel import (
    KernelBookLevel,
    KernelMarketState,
    StrategyAction,
)
from aiquanttrader.config.models import LlmConfirmationConfig
from aiquanttrader.domain.execution import RiskDecision, RiskReason, RiskState
from aiquanttrader.features.engine import IncrementalFeatureEngine
from aiquanttrader.features.market_structure import (
    CausalMarketStructureEngine,
    DealingRangeZone,
    SmartMoneySnapshot,
    StructureDirection,
    StructureEngineConfig,
    TimeframeStructure,
)
from aiquanttrader.features.models import (
    FeatureEngineConfig,
    MicrostructureSnapshot,
    VolatilityRegime,
)
from aiquanttrader.paper.engine import PaperEngineCycle
from aiquanttrader.paper.llm import (
    LlmConfirmationWorker,
    OpenAIConfirmationProvider,
    confirmation_request,
)
from aiquanttrader.paper.llm_models import (
    LlmAssessment,
    LlmConfirmation,
    LlmConfirmationRequest,
    LlmVerdict,
)
from aiquanttrader.paper.models import PaperDecisionRecord
from aiquanttrader.strategies.common import StrategyInput, StrategyTransition
from aiquanttrader.strategies.smart_money_scalper import (
    SmartMoneyScalperConfig,
    SmartMoneyScalperKernel,
    SmartMoneyScalperMemory,
)

MINUTE_NS = 60_000_000_000


def market(sequence: int, price: Decimal, *, offset_ns: int = 1) -> KernelMarketState:
    observed = (sequence + 1) * MINUTE_NS + offset_ns
    return KernelMarketState(
        exchange_ts_ns=observed,
        book_exchange_ts_ns=observed,
        observed_ts_ns=observed,
        sequence=sequence,
        bids=(KernelBookLevel(price=price - Decimal("0.5"), size=Decimal("2")),),
        asks=(KernelBookLevel(price=price + Decimal("0.5"), size=Decimal("1")),),
    )


def ready_feature(price: Decimal = Decimal("100")) -> MicrostructureSnapshot:
    config = FeatureEngineConfig(
        depth_levels=1,
        warmup_samples=2,
        maximum_input_age_ns=1_000,
        low_volatility_bps=Decimal("1"),
        high_volatility_bps=Decimal("1000"),
    )
    engine = IncrementalFeatureEngine(config)
    engine.update(market(0, price))
    return engine.update(market(1, price)).model_copy(
        update={"volatility_regime": VolatilityRegime.NORMAL}
    )


def timeframe(seconds: int, direction: StructureDirection) -> TimeframeStructure:
    return TimeframeStructure.model_validate(
        {
            "timeframe_seconds": seconds,
            "closed_bars": 20,
            "last_closed_ts_ns": 1,
            "close": "100",
            "direction": direction,
            "zone": DealingRangeZone.DISCOUNT,
            "support": "99",
            "resistance": "101",
            "bullish_bos": direction is StructureDirection.BULLISH,
        }
    )


def bullish_structure(observed_ts_ns: int) -> SmartMoneySnapshot:
    return SmartMoneySnapshot(
        observed_ts_ns=observed_ts_ns,
        revision=10,
        ready=True,
        one_minute=timeframe(60, StructureDirection.BULLISH),
        five_minute=timeframe(300, StructureDirection.BULLISH),
        fifteen_minute=timeframe(900, StructureDirection.BULLISH),
        long_confluence=7,
        short_confluence=1,
        long_reasons=("15m_bias", "5m_structure", "1m_bos"),
    )


def test_causal_structure_uses_only_closed_bars_and_restores_fail_closed() -> None:
    engine = CausalMarketStructureEngine(
        StructureEngineConfig(
            minimum_1m_bars=5,
            minimum_5m_bars=5,
            minimum_15m_bars=3,
        )
    )
    latest = None
    for sequence in range(61):
        latest = engine.update(market(sequence, Decimal("100") + sequence))
    assert latest is not None and latest.ready
    assert latest.fifteen_minute.direction is StructureDirection.BULLISH

    before = latest.model_copy(update={"observed_ts_ns": 0})
    intrabar = market(60, Decimal("25"), offset_ns=30_000_000_000).model_copy(
        update={"sequence": 10_000}
    )
    after = engine.update(intrabar).model_copy(update={"observed_ts_ns": 0})
    assert after == before

    restored = CausalMarketStructureEngine(
        engine.config,
        restored_state=engine.state,
    )
    restored_revision = restored.state.revision
    restored.update(
        market(60, Decimal("150"), offset_ns=40_000_000_000).model_copy(update={"sequence": 10_001})
    )
    assert restored.state.revision == restored_revision


def test_smart_money_scalper_enters_once_and_forces_bounded_exits() -> None:
    features = ready_feature()
    structure = bullish_structure(features.receive_ts_ns)
    kernel = SmartMoneyScalperKernel(
        SmartMoneyScalperConfig(
            reject_high_volatility=False,
            cooldown_ns=0,
            maximum_spread_bps=Decimal("200"),
        )
    )
    entered = kernel.decide(
        StrategyInput(
            features=features,
            market_structure=structure,
            estimated_taker_fee_bps=Decimal("1"),
            estimated_slippage_bps=Decimal("0"),
        ),
        SmartMoneyScalperMemory(),
    )
    assert entered.decision.action is StrategyAction.ENTER_LONG
    assert entered.decision.submit[0].reduce_only is False

    opened = entered.memory.synchronize_position(
        Decimal("0.001"), Decimal("100"), features.receive_ts_ns
    )
    aged_ts_ns = features.receive_ts_ns + 91_000_000_000
    aged_features = features.model_copy(
        update={"receive_ts_ns": aged_ts_ns, "computed_ts_ns": aged_ts_ns}
    )
    exited = kernel.decide(
        StrategyInput(
            features=aged_features,
            market_structure=structure.model_copy(
                update={"observed_ts_ns": aged_features.receive_ts_ns}
            ),
            position_average_entry_price=Decimal("100"),
            position_opened_ts_ns=features.receive_ts_ns,
        ),
        opened,
    )
    assert exited.decision.action is StrategyAction.EXIT_TIME_LIMIT
    assert exited.decision.reason == "ninety_second_no_progress_exit"
    assert exited.decision.submit[0].reduce_only
    assert exited.decision.submit[0].quantity_base == Decimal("0.001")


class FakeProvider:
    def __init__(self) -> None:
        self.closed = False

    async def confirm(self, request: LlmConfirmationRequest) -> LlmConfirmation:
        return LlmConfirmation(
            confirmation_id="f" * 64,
            request_id=request.request_id,
            run_id=request.run_id,
            completed_ts_ns=request.observed_ts_ns + 1,
            model="gpt-5.6-terra",
            latency_ms=Decimal("12"),
            assessment=LlmAssessment(
                verdict=LlmVerdict.CONFIRM,
                confidence=Decimal("0.8"),
                rationale="Causal directions and order flow agree.",
                invalidation_price=Decimal("99"),
                expected_horizon_seconds=60,
            ),
        )

    async def close(self) -> None:
        self.closed = True


def test_llm_worker_records_shadow_evidence_without_strategy_authority() -> None:
    features = ready_feature()
    structure = bullish_structure(features.receive_ts_ns)
    transition = SmartMoneyScalperKernel(
        SmartMoneyScalperConfig(
            reject_high_volatility=False,
            cooldown_ns=0,
            maximum_spread_bps=Decimal("200"),
        )
    ).decide(
        StrategyInput(
            features=features,
            market_structure=structure,
            estimated_taker_fee_bps=Decimal("0"),
            estimated_slippage_bps=Decimal("0"),
        ),
        SmartMoneyScalperMemory(),
    )
    intent = transition.decision.submit[0]
    risk = RiskDecision(
        decision_id="risk-1",
        intent_sha256=intent.sha256(),
        snapshot_sha256="a" * 64,
        limits_sha256="b" * 64,
        state=RiskState.ACTIVE,
        allowed=True,
        reasons=(RiskReason.APPROVED,),
        issued_ts_ns=features.receive_ts_ns,
        expires_ts_ns=features.receive_ts_ns + 1,
        approval_signature="c" * 64,
    )
    record = PaperDecisionRecord(
        record_id="record-1",
        sequence=0,
        decision_ts_ns=features.receive_ts_ns,
        feature_snapshot_sha256=features.sha256(),
        strategy_id=intent.strategy_id,
        intent=intent,
        risk_decision=risk,
        independent=True,
    )
    cycle = PaperEngineCycle(
        features=features,
        market_structure=structure,
        strategy_decision=transition.decision,
        decisions=(record,),
        orders=(),
        fills=(),
        markouts=(),
        drift_report=None,
        risk_state=RiskState.ACTIVE,
        risk_reasons=(RiskReason.APPROVED,),
        commands=(),
    )
    provider = FakeProvider()
    confirmations: list[LlmConfirmation] = []
    errors: list[str] = []
    worker = LlmConfirmationWorker(
        LlmConfirmationConfig(
            enabled=True,
            minimum_request_interval_seconds=15,
        ),
        provider,
        on_confirmation=confirmations.append,
        on_error=errors.append,
    )
    assert worker.offer("paper-test", cycle)
    assert not worker.offer("paper-test", cycle)
    stop = asyncio.Event()
    stop.set()
    asyncio.run(worker.run(stop))
    assert confirmations[0].authority == "shadow_only_no_execution"
    assert confirmations[0].assessment.verdict is LlmVerdict.CONFIRM
    assert errors == []
    assert provider.closed


def entry_cycle(*, allowed: bool = True) -> PaperEngineCycle:
    features = ready_feature()
    structure = bullish_structure(features.receive_ts_ns)
    transition = SmartMoneyScalperKernel(
        SmartMoneyScalperConfig(
            reject_high_volatility=False,
            cooldown_ns=0,
            maximum_spread_bps=Decimal("200"),
        )
    ).decide(
        StrategyInput(
            features=features,
            market_structure=structure,
            estimated_taker_fee_bps=Decimal("0"),
            estimated_slippage_bps=Decimal("0"),
        ),
        SmartMoneyScalperMemory(),
    )
    intent = transition.decision.submit[0]
    risk = RiskDecision(
        decision_id="risk-cycle",
        intent_sha256=intent.sha256(),
        snapshot_sha256="a" * 64,
        limits_sha256="b" * 64,
        state=RiskState.ACTIVE,
        allowed=allowed,
        reasons=(RiskReason.APPROVED,) if allowed else (RiskReason.OPERATOR_KILL,),
        issued_ts_ns=features.receive_ts_ns,
        expires_ts_ns=features.receive_ts_ns + 1,
        approval_signature="c" * 64 if allowed else None,
    )
    record = PaperDecisionRecord(
        record_id="record-cycle",
        sequence=0,
        decision_ts_ns=features.receive_ts_ns,
        feature_snapshot_sha256=features.sha256(),
        strategy_id=intent.strategy_id,
        intent=intent,
        risk_decision=risk,
        independent=True,
    )
    return PaperEngineCycle(
        features=features,
        market_structure=structure,
        strategy_decision=transition.decision,
        decisions=(record,),
        orders=(),
        fills=(),
        markouts=(),
        drift_report=None,
        risk_state=RiskState.ACTIVE,
        risk_reasons=risk.reasons,
        commands=(),
    )


def test_smart_money_config_and_memory_fail_closed() -> None:
    with pytest.raises(ValidationError, match="soft holding limit"):
        SmartMoneyScalperConfig(
            soft_holding_limit_ns=300 * 1_000_000_000,
            hard_holding_limit_ns=300 * 1_000_000_000,
        )
    with pytest.raises(ValidationError, match="cannot exceed five minutes"):
        SmartMoneyScalperConfig(
            soft_holding_limit_ns=299 * 1_000_000_000,
            hard_holding_limit_ns=301 * 1_000_000_000,
        )
    with pytest.raises(ValidationError, match="break-even trigger"):
        SmartMoneyScalperConfig(break_even_trigger_bps=Decimal("20"))
    with pytest.raises(ValidationError, match="break-even offset"):
        SmartMoneyScalperConfig(break_even_offset_bps=Decimal("13"))

    with pytest.raises(ValueError, match="average entry price"):
        SmartMoneyScalperMemory().synchronize_position(Decimal("1"), None, 10)
    opened = SmartMoneyScalperMemory().synchronize_position(Decimal("1"), Decimal("100"), 10)
    still_open = opened.synchronize_position(Decimal("2"), Decimal("101"), 20)
    assert still_open.position_opened_ts_ns == 10
    flat = still_open.synchronize_position(Decimal("0"), None, 30)
    assert flat.last_flat_ts_ns == 30
    assert flat.synchronize_position(Decimal("0"), None, 40).last_flat_ts_ns == 30


def test_smart_money_entry_gates_and_short_entry() -> None:
    features = ready_feature()
    bullish = bullish_structure(features.receive_ts_ns)
    kernel = SmartMoneyScalperKernel(
        SmartMoneyScalperConfig(maximum_spread_bps=Decimal("200"), cooldown_ns=20)
    )

    warmup = kernel.decide(
        StrategyInput(features=features.model_copy(update={"ready": False})),
        SmartMoneyScalperMemory(),
    )
    assert warmup.decision.action is StrategyAction.WARMUP
    spread = SmartMoneyScalperKernel(SmartMoneyScalperConfig()).decide(
        StrategyInput(features=features, market_structure=bullish), SmartMoneyScalperMemory()
    )
    assert spread.decision.action is StrategyAction.BLOCKED_SPREAD
    volatile = kernel.decide(
        StrategyInput(
            features=features.model_copy(update={"volatility_regime": VolatilityRegime.HIGH}),
            market_structure=bullish,
        ),
        SmartMoneyScalperMemory(),
    )
    assert volatile.decision.action is StrategyAction.BLOCKED_VOLATILITY
    cooldown = kernel.decide(
        StrategyInput(features=features, market_structure=bullish),
        SmartMoneyScalperMemory(last_flat_ts_ns=features.receive_ts_ns - 1),
    )
    assert cooldown.decision.reason == "post_exit_cooldown"
    duplicate = kernel.decide(
        StrategyInput(features=features, market_structure=bullish),
        SmartMoneyScalperMemory(last_entry_structure_revision=bullish.revision),
    )
    assert duplicate.decision.reason == "one_entry_attempt_per_closed_bar_revision"

    neutral = bullish.model_copy(
        update={
            "fifteen_minute": bullish.fifteen_minute.model_copy(
                update={"direction": StructureDirection.NEUTRAL}
            )
        }
    )
    assert (
        kernel.decide(
            StrategyInput(features=features, market_structure=neutral), SmartMoneyScalperMemory()
        ).decision.reason
        == "15m_direction_neutral"
    )
    weak = bullish.model_copy(update={"long_confluence": 2})
    assert (
        kernel.decide(
            StrategyInput(features=features, market_structure=weak), SmartMoneyScalperMemory()
        ).decision.action
        is StrategyAction.BLOCKED_CONFLUENCE
    )
    expensive = kernel.decide(
        StrategyInput(
            features=features,
            market_structure=bullish,
            estimated_taker_fee_bps=Decimal("20"),
            estimated_slippage_bps=Decimal("20"),
        ),
        SmartMoneyScalperMemory(),
    )
    assert expensive.decision.action is StrategyAction.BLOCKED_COST

    bearish = bullish.model_copy(
        update={
            "one_minute": timeframe(60, StructureDirection.BEARISH),
            "five_minute": timeframe(300, StructureDirection.BEARISH),
            "fifteen_minute": timeframe(900, StructureDirection.BEARISH),
            "long_confluence": 1,
            "short_confluence": 7,
            "short_reasons": ("15m_bias", "5m_structure", "1m_bos"),
        }
    )
    short = kernel.decide(
        StrategyInput(
            features=features,
            market_structure=bearish,
            estimated_taker_fee_bps=Decimal("0"),
            estimated_slippage_bps=Decimal("0"),
        ),
        SmartMoneyScalperMemory(),
    )
    assert short.decision.action is StrategyAction.ENTER_SHORT
    assert short.decision.target_price is not None
    assert short.decision.reference_price is not None
    assert short.decision.target_price < short.decision.reference_price


def test_smart_money_position_exit_matrix() -> None:
    base_features = ready_feature()
    structure = bullish_structure(base_features.receive_ts_ns)
    kernel = SmartMoneyScalperKernel(
        SmartMoneyScalperConfig(
            reject_high_volatility=False,
            maximum_spread_bps=Decimal("200"),
            exit_retry_ns=250_000_000,
        )
    )
    no_context = kernel.decide(
        StrategyInput(features=base_features), SmartMoneyScalperMemory(inventory_base=Decimal("1"))
    )
    assert no_context.decision.reason == "awaiting_confirmed_position_context"

    def decide_at(
        price: str,
        age_seconds: int,
        *,
        inventory: str = "1",
        memory_updates: dict[str, object] | None = None,
        flow: str = "0",
        snapshot: SmartMoneySnapshot | None = structure,
    ) -> StrategyTransition[SmartMoneyScalperMemory]:
        observed = base_features.receive_ts_ns + age_seconds * 1_000_000_000
        features = base_features.model_copy(
            update={
                "midprice": Decimal(price),
                "receive_ts_ns": observed,
                "computed_ts_ns": observed,
                "trade_flow_imbalance": Decimal(flow),
            }
        )
        values: dict[str, object] = {
            "inventory_base": Decimal(inventory),
            "average_entry_price": Decimal("100"),
            "position_opened_ts_ns": base_features.receive_ts_ns,
        }
        values.update(memory_updates or {})
        return kernel.decide(
            StrategyInput(features=features, market_structure=snapshot),
            SmartMoneyScalperMemory.model_validate(values),
        )

    assert decide_at("100.05", 20).decision.action is StrategyAction.HOLD
    assert decide_at("100.21", 20).decision.action is StrategyAction.EXIT_TAKE_PROFIT
    assert decide_at("99.87", 20).decision.reason == "hard_stop_reached"
    break_even = decide_at("100.09", 20, memory_updates={"peak_favorable_bps": Decimal("14")})
    assert break_even.decision.reason == "break_even_stop_reached"
    assert decide_at("100.10", 300).decision.reason == "five_minute_hard_limit"

    opposite_one = structure.one_minute.model_copy(update={"bearish_bos": True})
    opposite = structure.model_copy(update={"one_minute": opposite_one})
    assert (
        decide_at("100", 20, flow="-0.5", snapshot=opposite).decision.action
        is StrategyAction.EXIT_OPPOSITE_FLOW
    )
    pending = decide_at(
        "100.21",
        20,
        memory_updates={"last_order_ts_ns": base_features.receive_ts_ns + 20_000_000_000},
    )
    assert pending.decision.reason == "exit_order_pending_activation"

    short_one = timeframe(60, StructureDirection.BULLISH).model_copy(update={"bullish_bos": True})
    short_structure = structure.model_copy(update={"one_minute": short_one, "short_confluence": 6})
    short_exit = decide_at("100", 20, inventory="-1", flow="0.5", snapshot=short_structure)
    assert short_exit.decision.action is StrategyAction.EXIT_OPPOSITE_FLOW
    assert short_exit.decision.confluence_score == 6
    assert short_exit.decision.submit[0].side.value == "buy"
    assert decide_at("100", 20, snapshot=None).decision.action is StrategyAction.HOLD
    no_structure_exit = decide_at("100.21", 20, snapshot=None)
    assert no_structure_exit.decision.confluence_score == 0


def test_llm_worker_rejects_unqualified_and_reports_queue_pressure() -> None:
    cycle = entry_cycle()
    provider = FakeProvider()
    errors: list[str] = []
    with pytest.raises(ValueError, match="requires enabled confirmation"):
        LlmConfirmationWorker(
            LlmConfirmationConfig(),
            provider,
            on_confirmation=lambda _: None,
            on_error=errors.append,
        )
    worker = LlmConfirmationWorker(
        LlmConfirmationConfig(enabled=True, minimum_request_interval_seconds=15, queue_capacity=1),
        provider,
        on_confirmation=lambda _: None,
        on_error=errors.append,
    )
    assert not worker.offer(
        "paper-test",
        replace(
            cycle,
            strategy_decision=cycle.strategy_decision.model_copy(
                update={"action": StrategyAction.HOLD, "submit": ()}
            ),
        ),
    )
    assert not worker.offer("paper-test", replace(cycle, decisions=()))
    assert not worker.offer("paper-test", entry_cycle(allowed=False))
    assert worker.offer("paper-test", cycle)
    later_features = cycle.features.model_copy(
        update={
            "receive_ts_ns": cycle.features.receive_ts_ns + 16_000_000_000,
            "computed_ts_ns": cycle.features.computed_ts_ns + 16_000_000_000,
        }
    )
    assert cycle.market_structure is not None
    later = replace(
        cycle,
        features=later_features,
        market_structure=cycle.market_structure.model_copy(
            update={"observed_ts_ns": later_features.receive_ts_ns}
        ),
    )
    assert not worker.offer("paper-test", later)
    assert errors == ["queue_full"]


def test_llm_worker_contains_provider_failure_and_idle_timeout() -> None:
    class ErrorProvider(FakeProvider):
        async def confirm(self, request: LlmConfirmationRequest) -> LlmConfirmation:
            raise RuntimeError(request.request_id)

    async def exercise() -> tuple[list[str], bool]:
        provider = ErrorProvider()
        errors: list[str] = []
        worker = LlmConfirmationWorker(
            LlmConfirmationConfig(enabled=True),
            provider,
            on_confirmation=lambda _: None,
            on_error=errors.append,
        )
        assert worker.offer("paper-test", entry_cycle())
        stop = asyncio.Event()
        stop.set()
        await worker.run(stop)

        idle_provider = FakeProvider()
        idle_worker = LlmConfirmationWorker(
            LlmConfirmationConfig(enabled=True),
            idle_provider,
            on_confirmation=lambda _: None,
            on_error=errors.append,
        )
        idle_stop = asyncio.Event()
        task = asyncio.create_task(idle_worker.run(idle_stop))
        await asyncio.sleep(0.3)
        idle_stop.set()
        await task
        return errors, provider.closed and idle_provider.closed

    errors, closed = asyncio.run(exercise())
    assert errors == ["runtimeerror"]
    assert closed


def test_confirmation_request_requires_ready_structure() -> None:
    cycle = entry_cycle()
    request = confirmation_request("paper-test", cycle)
    assert request.side == "long"
    assert request.strategy_id == "smart-money-scalper-v1"
    assert request.request_id == confirmation_request("paper-test", cycle).request_id
    v2_intent = cycle.strategy_decision.submit[0].model_copy(
        update={"strategy_id": "smart-money-scalper-v2"}
    )
    v2_cycle = replace(
        cycle,
        strategy_decision=cycle.strategy_decision.model_copy(update={"submit": (v2_intent,)}),
    )
    v2_request = confirmation_request("paper-test", v2_cycle)
    assert v2_request.strategy_id == "smart-money-scalper-v2"
    assert v2_request.request_id != request.request_id
    with pytest.raises(ValueError, match="ready causal market structure"):
        confirmation_request("paper-test", replace(cycle, market_structure=None))
    with pytest.raises(ValueError, match="submitted entry intent"):
        confirmation_request(
            "paper-test",
            replace(
                cycle,
                strategy_decision=cycle.strategy_decision.model_copy(update={"submit": ()}),
            ),
        )


def test_openai_provider_validates_secret_and_parses_structured_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="requires enabled"):
        OpenAIConfirmationProvider(LlmConfirmationConfig())
    config = LlmConfirmationConfig(enabled=True)

    def missing_secret(self: Path, *args: object, **kwargs: object) -> StringIO:
        raise OSError(self)

    monkeypatch.setattr(Path, "open", missing_secret)
    with pytest.raises(ValueError, match="secret is unavailable"):
        OpenAIConfirmationProvider(config)
    monkeypatch.setattr(Path, "open", lambda self, *args, **kwargs: StringIO("too short\n"))
    with pytest.raises(ValueError, match="secret is malformed"):
        OpenAIConfirmationProvider(config)
    monkeypatch.setattr(
        Path,
        "open",
        lambda self, *args, **kwargs: StringIO("test-provider-secret-abcdefghijklmnopqrstuvwxyz\n"),
    )

    assessment = LlmAssessment(
        verdict=LlmVerdict.UNCERTAIN,
        confidence=Decimal("0.4"),
        rationale="The timeframes conflict.",
        expected_horizon_seconds=45,
    )

    class Responses:
        def __init__(self) -> None:
            self.parsed: LlmAssessment | None = assessment

        async def parse(self, **kwargs: object) -> SimpleNamespace:
            assert kwargs["model"] == "gpt-5.6-terra"
            return SimpleNamespace(
                output=[
                    SimpleNamespace(type="reasoning", content=[]),
                    SimpleNamespace(
                        type="message",
                        content=[
                            SimpleNamespace(type="refusal", parsed=None),
                            SimpleNamespace(type="output_text", parsed=self.parsed),
                        ],
                    ),
                ]
            )

    class Client:
        def __init__(self) -> None:
            self.responses = Responses()
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    provider = OpenAIConfirmationProvider(config)
    client = Client()
    monkeypatch.setattr(provider, "_client", client)
    confirmation = asyncio.run(provider.confirm(confirmation_request("paper-test", entry_cycle())))
    assert confirmation.assessment == assessment
    assert confirmation.model == "gpt-5.6-terra"
    asyncio.run(provider.close())
    assert client.closed

    client.responses.parsed = None
    provider = OpenAIConfirmationProvider(config)
    monkeypatch.setattr(provider, "_client", client)
    with pytest.raises(ValueError, match="parsed assessment"):
        asyncio.run(provider.confirm(confirmation_request("paper-test", entry_cycle())))
