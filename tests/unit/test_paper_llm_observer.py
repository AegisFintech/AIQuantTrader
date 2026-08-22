from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from aiquanttrader.backtest.kernel import (
    KernelBookLevel,
    KernelDecision,
    KernelMarketState,
    StrategyAction,
)
from aiquanttrader.config.models import LlmConfirmationConfig
from aiquanttrader.domain.execution import (
    OrderIntent,
    OrderKind,
    RiskDecision,
    RiskReason,
    RiskState,
)
from aiquanttrader.domain.market import OrderSide
from aiquanttrader.features.engine import IncrementalFeatureEngine
from aiquanttrader.features.market_structure import (
    DealingRangeZone,
    SmartMoneySnapshot,
    StructureDirection,
    TimeframeStructure,
)
from aiquanttrader.features.models import FeatureEngineConfig, MicrostructureSnapshot
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


def _features() -> MicrostructureSnapshot:
    config = FeatureEngineConfig(
        depth_levels=1,
        warmup_samples=2,
        maximum_input_age_ns=1_000,
        low_volatility_bps=Decimal("1"),
        high_volatility_bps=Decimal("1000"),
    )
    engine = IncrementalFeatureEngine(config)

    def market(sequence: int) -> KernelMarketState:
        observed = 1_000 + sequence
        return KernelMarketState(
            exchange_ts_ns=observed,
            book_exchange_ts_ns=observed,
            observed_ts_ns=observed,
            sequence=sequence,
            bids=(KernelBookLevel(price=Decimal("99.5"), size=Decimal("2")),),
            asks=(KernelBookLevel(price=Decimal("100.5"), size=Decimal("1")),),
        )

    engine.update(market(0))
    return engine.update(market(1))


def _timeframe(seconds: int, bullish: bool) -> TimeframeStructure:
    direction = StructureDirection.BULLISH if bullish else StructureDirection.BEARISH
    return TimeframeStructure.model_validate(
        {
            "timeframe_seconds": seconds,
            "closed_bars": 20,
            "last_closed_ts_ns": 1,
            "close": "100",
            "direction": direction,
            "zone": DealingRangeZone.DISCOUNT if bullish else DealingRangeZone.PREMIUM,
            "support": "99",
            "resistance": "101",
            "bullish_bos": bullish,
            "bearish_bos": not bullish,
        }
    )


def _structure(observed_ts_ns: int, *, bullish: bool = True) -> SmartMoneySnapshot:
    return SmartMoneySnapshot(
        observed_ts_ns=observed_ts_ns,
        revision=10,
        ready=True,
        one_minute=_timeframe(60, bullish),
        five_minute=_timeframe(300, bullish),
        fifteen_minute=_timeframe(900, bullish),
        long_confluence=7 if bullish else 1,
        short_confluence=1 if bullish else 7,
        long_reasons=("15m_bias", "5m_structure", "1m_bos") if bullish else (),
        short_reasons=("15m_bias", "5m_structure", "1m_bos") if not bullish else (),
    )


def _cycle(*, allowed: bool = True, bullish: bool = True) -> PaperEngineCycle:
    features = _features()
    side = OrderSide.BUY if bullish else OrderSide.SELL
    intent = OrderIntent(
        intent_id="reactive-entry",
        strategy_id="smart-money-scalper-v3",
        side=side,
        kind=OrderKind.LIMIT,
        quantity_base=Decimal("0.001"),
        limit_price=features.best_bid if bullish else features.best_ask,
        post_only=True,
        created_ts_ns=features.receive_ts_ns,
        rationale="qualified deterministic v3 setup",
    )
    decision = KernelDecision(
        submit=(intent,),
        action=StrategyAction.ENTER_LONG if bullish else StrategyAction.ENTER_SHORT,
        reason="reactive_smart_money_entry",
        expected_edge_bps=Decimal("8"),
        required_edge_bps=Decimal("6.725"),
        confluence_score=7,
        reference_price=features.midprice,
    )
    risk = RiskDecision(
        decision_id="risk-llm-observer",
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
        record_id="record-llm-observer",
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
        market_structure=_structure(features.receive_ts_ns, bullish=bullish),
        strategy_decision=decision,
        decisions=(record,),
        orders=(),
        fills=(),
        markouts=(),
        drift_report=None,
        risk_state=RiskState.ACTIVE,
        risk_reasons=risk.reasons,
        commands=(),
    )


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


def test_worker_is_bounded_non_authoritative_and_contains_provider_errors() -> None:
    cycle = _cycle()
    with pytest.raises(ValueError, match="requires enabled confirmation"):
        LlmConfirmationWorker(
            LlmConfirmationConfig(),
            FakeProvider(),
            on_confirmation=lambda _: None,
            on_error=lambda _: None,
        )

    provider = FakeProvider()
    confirmations: list[LlmConfirmation] = []
    errors: list[str] = []
    worker = LlmConfirmationWorker(
        LlmConfirmationConfig(enabled=True, minimum_request_interval_seconds=15),
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
    assert errors == [] and provider.closed

    class ErrorProvider(FakeProvider):
        async def confirm(self, request: LlmConfirmationRequest) -> LlmConfirmation:
            raise RuntimeError(request.request_id)

    async def exercise_failure_and_idle() -> tuple[list[str], bool]:
        failed = ErrorProvider()
        captured: list[str] = []
        error_worker = LlmConfirmationWorker(
            LlmConfirmationConfig(enabled=True),
            failed,
            on_confirmation=lambda _: None,
            on_error=captured.append,
        )
        assert error_worker.offer("paper-test", cycle)
        stopped = asyncio.Event()
        stopped.set()
        await error_worker.run(stopped)

        idle = FakeProvider()
        idle_worker = LlmConfirmationWorker(
            LlmConfirmationConfig(enabled=True),
            idle,
            on_confirmation=lambda _: None,
            on_error=captured.append,
        )
        idle_stop = asyncio.Event()
        task = asyncio.create_task(idle_worker.run(idle_stop))
        await asyncio.sleep(0.3)
        idle_stop.set()
        await task
        return captured, failed.closed and idle.closed

    captured, closed = asyncio.run(exercise_failure_and_idle())
    assert captured == ["runtimeerror"] and closed


def test_worker_rejects_unqualified_rate_limited_and_full_queue_cycles() -> None:
    cycle = _cycle()
    errors: list[str] = []
    worker = LlmConfirmationWorker(
        LlmConfirmationConfig(enabled=True, minimum_request_interval_seconds=15, queue_capacity=1),
        FakeProvider(),
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
    assert not worker.offer("paper-test", _cycle(allowed=False))
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


def test_confirmation_request_is_deterministic_typed_and_fail_closed() -> None:
    long_cycle = _cycle()
    request = confirmation_request("paper-test", long_cycle)
    assert request.side == "long"
    assert request.strategy_id == "smart-money-scalper-v3"
    assert request.request_id == confirmation_request("paper-test", long_cycle).request_id
    assert confirmation_request("paper-test", _cycle(bullish=False)).side == "short"

    with pytest.raises(ValueError, match="ready causal market structure"):
        confirmation_request("paper-test", replace(long_cycle, market_structure=None))
    with pytest.raises(ValueError, match="ready causal market structure"):
        assert long_cycle.market_structure is not None
        confirmation_request(
            "paper-test",
            replace(
                long_cycle,
                market_structure=long_cycle.market_structure.model_copy(update={"ready": False}),
            ),
        )
    with pytest.raises(ValueError, match="submitted entry intent"):
        confirmation_request(
            "paper-test",
            replace(
                long_cycle,
                strategy_decision=long_cycle.strategy_decision.model_copy(update={"submit": ()}),
            ),
        )
    unsupported = long_cycle.strategy_decision.submit[0].model_copy(
        update={"strategy_id": "order-flow-scalper-v1"}
    )
    with pytest.raises(ValueError, match="does not support"):
        confirmation_request(
            "paper-test",
            replace(
                long_cycle,
                strategy_decision=long_cycle.strategy_decision.model_copy(
                    update={"submit": (unsupported,)}
                ),
            ),
        )


def test_openai_provider_validates_secret_and_parses_typed_response(
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
        parsed: LlmAssessment | None = assessment

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

    client = Client()
    monkeypatch.setattr("aiquanttrader.paper.llm._create_openai_client", lambda *_: client)
    provider = OpenAIConfirmationProvider(config)
    confirmation = asyncio.run(provider.confirm(confirmation_request("paper-test", _cycle())))
    assert confirmation.assessment == assessment
    assert confirmation.model == "gpt-5.6-terra"
    asyncio.run(provider.close())
    assert client.closed

    client.responses.parsed = None
    provider = OpenAIConfirmationProvider(config)
    with pytest.raises(ValueError, match="parsed assessment"):
        asyncio.run(provider.confirm(confirmation_request("paper-test", _cycle())))
