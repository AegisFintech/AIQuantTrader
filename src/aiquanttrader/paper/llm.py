"""Bounded asynchronous OpenAI confirmation for paper-trading evidence only."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Callable
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from openai import AsyncOpenAI

from aiquanttrader.backtest.kernel import StrategyAction
from aiquanttrader.config.models import LlmConfirmationConfig
from aiquanttrader.domain.base import canonical_sha256
from aiquanttrader.paper.engine import PaperEngineCycle
from aiquanttrader.paper.llm_models import (
    LlmAssessment,
    LlmConfirmation,
    LlmConfirmationRequest,
)

SYSTEM_INSTRUCTIONS = """You are a BTC perpetual microstructure reviewer. Evaluate only the
causal numeric 15m/5m/1m setup supplied by the system. Never propose an order, leverage,
position size, or risk override. Return whether the already-generated setup is confirmed,
rejected, or uncertain for retrospective research. Treat missing or conflicting evidence as
uncertain. The maximum horizon is five minutes."""


def _create_openai_client(config: LlmConfirmationConfig, api_key: str) -> AsyncOpenAI:
    # Keep the optional network client out of healthcheck/status startup paths.
    from openai import AsyncOpenAI

    return AsyncOpenAI(
        api_key=api_key,
        timeout=float(config.timeout_seconds),
        max_retries=1,
    )


class ConfirmationProvider(Protocol):
    async def confirm(self, request: LlmConfirmationRequest) -> LlmConfirmation: ...

    async def close(self) -> None: ...


class OpenAIConfirmationProvider:
    """Responses API adapter with a strict Pydantic structured-output boundary."""

    def __init__(self, config: LlmConfirmationConfig) -> None:
        if not config.enabled:
            raise ValueError("OpenAI provider requires enabled LLM confirmation")
        try:
            with config.api_key_secret_path.open("r", encoding="utf-8") as handle:
                api_key = handle.read().strip()
        except OSError as exc:
            raise ValueError("OpenAI API key secret is unavailable") from exc
        if len(api_key) < 20 or any(character.isspace() for character in api_key):
            raise ValueError("OpenAI API key secret is malformed")
        self._config = config
        self._client = _create_openai_client(config, api_key)

    async def confirm(self, request: LlmConfirmationRequest) -> LlmConfirmation:
        started = time.perf_counter_ns()
        response = await self._client.responses.parse(
            model=self._config.model,
            instructions=SYSTEM_INSTRUCTIONS,
            input=request.model_dump_json(),
            text_format=LlmAssessment,
            max_output_tokens=self._config.maximum_output_tokens,
        )
        assessment: LlmAssessment | None = None
        for output in response.output:
            if output.type != "message":
                continue
            for content in output.content:
                if content.type == "output_text" and content.parsed is not None:
                    assessment = content.parsed
                    break
        if assessment is None:
            raise ValueError("OpenAI response did not contain a parsed assessment")
        completed_ts_ns = time.time_ns()
        latency_ms = Decimal(time.perf_counter_ns() - started) / Decimal("1000000")
        confirmation_id = hashlib.sha256(
            (f"{request.request_id}:{completed_ts_ns}:{assessment.model_dump_json()}").encode()
        ).hexdigest()
        return LlmConfirmation(
            confirmation_id=confirmation_id,
            request_id=request.request_id,
            run_id=request.run_id,
            completed_ts_ns=completed_ts_ns,
            model=self._config.model,
            latency_ms=latency_ms,
            assessment=assessment,
        )

    async def close(self) -> None:
        await self._client.close()


class LlmConfirmationWorker:
    """Drop-safe worker; no response is ever fed back into strategy or risk."""

    def __init__(
        self,
        config: LlmConfirmationConfig,
        provider: ConfirmationProvider,
        *,
        on_confirmation: Callable[[LlmConfirmation], None],
        on_error: Callable[[str], None],
    ) -> None:
        if not config.enabled:
            raise ValueError("LLM worker requires enabled confirmation configuration")
        self.config = config
        self.provider = provider
        self._on_confirmation = on_confirmation
        self._on_error = on_error
        self._queue: asyncio.Queue[LlmConfirmationRequest] = asyncio.Queue(
            maxsize=config.queue_capacity
        )
        self._last_enqueued_ts_ns: int | None = None

    def offer(self, run_id: str, cycle: PaperEngineCycle) -> bool:
        decision = cycle.strategy_decision
        if decision.action not in {StrategyAction.ENTER_LONG, StrategyAction.ENTER_SHORT}:
            return False
        if not cycle.decisions or not any(
            record.risk_decision.allowed for record in cycle.decisions
        ):
            return False
        minimum_ns = self.config.minimum_request_interval_seconds * 1_000_000_000
        if (
            self._last_enqueued_ts_ns is not None
            and cycle.features.receive_ts_ns - self._last_enqueued_ts_ns < minimum_ns
        ):
            return False
        request = confirmation_request(run_id, cycle)
        try:
            self._queue.put_nowait(request)
        except asyncio.QueueFull:
            self._on_error("queue_full")
            return False
        self._last_enqueued_ts_ns = cycle.features.receive_ts_ns
        return True

    async def run(self, stop: asyncio.Event) -> None:
        try:
            while not stop.is_set() or not self._queue.empty():
                try:
                    request = await asyncio.wait_for(self._queue.get(), timeout=0.25)
                except TimeoutError:
                    continue
                try:
                    confirmation = await self.provider.confirm(request)
                    self._on_confirmation(confirmation)
                except Exception as exc:
                    self._on_error(type(exc).__name__.lower())
                finally:
                    self._queue.task_done()
        finally:
            await self.provider.close()


def confirmation_request(run_id: str, cycle: PaperEngineCycle) -> LlmConfirmationRequest:
    structure = cycle.market_structure
    if structure is None or not structure.ready:
        raise ValueError("LLM confirmation requires ready causal market structure")
    decision = cycle.strategy_decision
    side: Literal["long", "short"] = (
        "long" if decision.action is StrategyAction.ENTER_LONG else "short"
    )
    identity = {
        "run_id": run_id,
        "observed_ts_ns": cycle.features.receive_ts_ns,
        "feature_snapshot_sha256": cycle.features.sha256(),
        "strategy_decision_sha256": decision.sha256(),
        "side": side,
    }
    return LlmConfirmationRequest(
        request_id=canonical_sha256(identity),
        run_id=run_id,
        observed_ts_ns=cycle.features.receive_ts_ns,
        side=side,
        feature_snapshot_sha256=cycle.features.sha256(),
        strategy_decision_sha256=decision.sha256(),
        market_price=cycle.features.midprice,
        spread_bps=cycle.features.spread_bps,
        book_imbalance=cycle.features.book_imbalance,
        trade_flow_imbalance=cycle.features.trade_flow_imbalance,
        volatility_regime=cycle.features.volatility_regime,
        expected_edge_bps=decision.expected_edge_bps,
        required_edge_bps=decision.required_edge_bps,
        confluence_score=decision.confluence_score,
        structure=structure,
    )
