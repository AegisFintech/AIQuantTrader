"""Frozen testnet and canary gates which stop at human approval boundaries."""

from __future__ import annotations

import time
import tomllib
from decimal import Decimal
from pathlib import Path

from aiquanttrader_native.domain.base import canonical_sha256
from aiquanttrader_native.domain.governance import PromotionStage
from aiquanttrader_native.governance.models import (
    CanaryEvidencePolicy,
    CanaryEvidenceReport,
    CanaryGateResult,
    CanaryObservation,
    DeploymentAdmissionRecord,
    DeploymentAdmissionState,
    TestnetDressRehearsalObservation,
    TestnetDressRehearsalPolicy,
    TestnetDressRehearsalReport,
    TestnetEvidenceGate,
    TestnetGateResult,
)


def load_canary_policy(path: Path) -> CanaryEvidencePolicy:
    return CanaryEvidencePolicy.model_validate(_load_policy(path, label="canary evidence"))


def load_testnet_policy(path: Path) -> TestnetDressRehearsalPolicy:
    return TestnetDressRehearsalPolicy.model_validate(_load_policy(path, label="testnet rehearsal"))


def _load_policy(path: Path, *, label: str) -> dict[str, object]:
    resolved = path.resolve(strict=True)
    size = resolved.stat().st_size
    if not resolved.is_file() or size <= 0 or size > 1_048_576:
        raise ValueError(f"{label} policy path is invalid")
    with resolved.open("rb") as handle:
        payload = tomllib.load(handle)
    return payload


def evaluate_testnet_evidence(
    *,
    observation: TestnetDressRehearsalObservation,
    policy: TestnetDressRehearsalPolicy,
    generated_ts_ns: int | None = None,
) -> TestnetDressRehearsalReport:
    duration = observation.ended_ts_ns - observation.started_ts_ns
    scenarios = {result.scenario: result for result in observation.scenarios}
    required = set(policy.required_scenarios)
    observed = set(scenarios)
    exact_matrix = observed == required
    passed_matrix = exact_matrix and all(scenarios[item].passed for item in required)
    gates = (
        _testnet_gate(
            TestnetEvidenceGate.POLICY_FROZEN,
            policy.frozen_at_ns <= observation.started_ts_ns,
            policy.frozen_at_ns,
            f"<= {observation.started_ts_ns}",
        ),
        _testnet_gate(
            TestnetEvidenceGate.OBSERVATION,
            duration >= policy.minimum_observation_ns,
            duration,
            policy.minimum_observation_ns,
        ),
        _testnet_gate(
            TestnetEvidenceGate.ORDERS,
            observation.orders >= policy.minimum_orders,
            observation.orders,
            policy.minimum_orders,
        ),
        _testnet_gate(
            TestnetEvidenceGate.FILLS,
            observation.fills >= policy.minimum_fills,
            observation.fills,
            policy.minimum_fills,
        ),
        _testnet_gate(
            TestnetEvidenceGate.SCENARIO_MATRIX,
            exact_matrix,
            sorted(item.value for item in observed),
            sorted(item.value for item in required),
        ),
        _testnet_gate(TestnetEvidenceGate.SCENARIO_RESULTS, passed_matrix, passed_matrix, True),
        _testnet_gate(
            TestnetEvidenceGate.UNKNOWN_OUTCOMES_RESOLVED,
            observation.resolved_unknown_outcomes == observation.unknown_outcomes,
            f"{observation.resolved_unknown_outcomes}/{observation.unknown_outcomes}",
            "all",
        ),
        _testnet_gate(
            TestnetEvidenceGate.RECONCILIATION_FAILURES,
            observation.reconciliation_failures == 0,
            observation.reconciliation_failures,
            0,
        ),
        _testnet_gate(
            TestnetEvidenceGate.DUPLICATE_VENUE_ORDERS,
            observation.duplicate_venue_orders == 0,
            observation.duplicate_venue_orders,
            0,
        ),
        _testnet_gate(
            TestnetEvidenceGate.RISK_BREACHES,
            observation.risk_breaches == 0,
            observation.risk_breaches,
            0,
        ),
        _testnet_gate(
            TestnetEvidenceGate.CANCEL_ALL,
            observation.cancel_all_confirmations >= policy.minimum_cancel_all_confirmations,
            observation.cancel_all_confirmations,
            policy.minimum_cancel_all_confirmations,
        ),
        _testnet_gate(
            TestnetEvidenceGate.DEADMAN_CANCELLATION,
            observation.deadman_cancellations >= policy.minimum_deadman_cancellations,
            observation.deadman_cancellations,
            policy.minimum_deadman_cancellations,
        ),
        _testnet_gate(
            TestnetEvidenceGate.FLAT_FINAL_STATE,
            observation.ending_position_base == 0 and observation.ending_open_orders == 0,
            f"position={observation.ending_position_base},orders={observation.ending_open_orders}",
            "position=0,orders=0",
        ),
        _testnet_gate(
            TestnetEvidenceGate.NO_MAINNET_CREDENTIALS,
            observation.mainnet_credentials_present is False,
            observation.mainnet_credentials_present,
            False,
        ),
    )
    payload = {
        "schema_version": 1,
        "rehearsal_id": observation.rehearsal_id,
        "policy_id": policy.policy_id,
        "policy_sha256": policy.sha256(),
        "observation_sha256": observation.sha256(),
        "generated_ts_ns": time.time_ns() if generated_ts_ns is None else generated_ts_ns,
        "commit_sha": observation.commit_sha,
        "image_digest": observation.image_digest,
        "dependency_lock_sha256": observation.dependency_lock_sha256,
        "dataset_sha256": observation.dataset_sha256,
        "model_sha256": observation.model_sha256,
        "feature_schema_sha256": observation.feature_schema_sha256,
        "strategy_config_sha256": observation.strategy_config_sha256,
        "risk_policy_sha256": observation.risk_policy_sha256,
        "target_configuration_sha256": observation.target_configuration_sha256,
        "account_address": observation.account_address,
        "vault_address": observation.vault_address,
        "trading_wallet_address": observation.trading_wallet_address,
        "control_wallet_address": observation.control_wallet_address,
        "observation_ns": duration,
        "orders": observation.orders,
        "fills": observation.fills,
        "gates": [gate.model_dump(mode="json") for gate in gates],
    }
    return TestnetDressRehearsalReport.model_validate(
        {
            "report_id": canonical_sha256(payload),
            "awaiting_canary_approval": all(gate.passed for gate in gates),
            **payload,
        }
    )


def evaluate_canary_evidence(
    *,
    admission: DeploymentAdmissionRecord,
    observation: CanaryObservation,
    policy: CanaryEvidencePolicy,
    generated_ts_ns: int | None = None,
) -> CanaryEvidenceReport:
    if admission.state is not DeploymentAdmissionState.ACTIVE:
        raise ValueError("canary evidence requires the active admitted deployment")
    if admission.stage is not PromotionStage.APPROVED_CANARY:
        raise ValueError("canary evidence requires an approved-canary admission")
    if observation.deployment_id != admission.deployment_id:
        raise ValueError("canary observation deployment does not match admission")
    if observation.admission_id != admission.admission_id:
        raise ValueError("canary observation admission identity does not match")
    duration = observation.ended_ts_ns - observation.started_ts_ns
    rejection_fraction = (
        Decimal(observation.rejected_orders) / Decimal(observation.orders)
        if observation.orders
        else Decimal("1")
    )
    gates = (
        _gate(
            "observation",
            duration >= policy.minimum_observation_ns,
            duration,
            policy.minimum_observation_ns,
        ),
        _gate(
            "orders",
            observation.orders >= policy.minimum_orders,
            observation.orders,
            policy.minimum_orders,
        ),
        _gate(
            "fills",
            observation.fills >= policy.minimum_fills,
            observation.fills,
            policy.minimum_fills,
        ),
        _gate(
            "maker_fills",
            observation.maker_fills >= policy.minimum_maker_fills,
            observation.maker_fills,
            policy.minimum_maker_fills,
        ),
        _gate(
            "rejection_fraction",
            rejection_fraction <= policy.maximum_rejection_fraction,
            rejection_fraction,
            policy.maximum_rejection_fraction,
        ),
        _gate(
            "unknown_outcomes", observation.unknown_outcomes == 0, observation.unknown_outcomes, 0
        ),
        _gate(
            "reconciliation_failures",
            observation.reconciliation_failures == 0,
            observation.reconciliation_failures,
            0,
        ),
        _gate(
            "fee_attribution",
            observation.fee_events >= observation.fills,
            observation.fee_events,
            observation.fills,
        ),
        _gate(
            "funding_attribution",
            observation.funding_events >= 1,
            observation.funding_events,
            ">= 1",
        ),
        _gate(
            "positive_post_cost_pnl",
            not policy.require_positive_post_cost_pnl or observation.post_cost_pnl_usd > 0,
            observation.post_cost_pnl_usd,
            "> 0",
        ),
        _gate(
            "drawdown",
            observation.maximum_drawdown_fraction <= policy.maximum_drawdown_fraction,
            observation.maximum_drawdown_fraction,
            policy.maximum_drawdown_fraction,
        ),
        _gate(
            "adverse_markout",
            observation.mean_adverse_markout_bps >= -policy.maximum_adverse_markout_bps,
            observation.mean_adverse_markout_bps,
            f">= {-policy.maximum_adverse_markout_bps}",
        ),
        _gate(
            "capital",
            observation.maximum_account_equity_usd <= admission.capital_limit_usd,
            observation.maximum_account_equity_usd,
            admission.capital_limit_usd,
        ),
        _gate(
            "drills",
            set(observation.completed_drills) >= set(policy.required_drills),
            sorted(observation.completed_drills),
            sorted(policy.required_drills),
        ),
    )
    payload = {
        "schema_version": 1,
        "deployment_id": observation.deployment_id,
        "admission_id": observation.admission_id,
        "policy_id": policy.policy_id,
        "policy_sha256": policy.sha256(),
        "observation_sha256": observation.sha256(),
        "generated_ts_ns": time.time_ns() if generated_ts_ns is None else generated_ts_ns,
        "gates": [gate.model_dump(mode="json") for gate in gates],
    }
    return CanaryEvidenceReport.model_validate(
        {
            "report_id": canonical_sha256(payload),
            "awaiting_production_approval": all(gate.passed for gate in gates),
            **payload,
        }
    )


def _gate(name: str, passed: bool, actual: object, required: object) -> CanaryGateResult:
    return CanaryGateResult(
        gate=name,
        passed=passed,
        actual=str(actual),
        required=str(required),
    )


def _testnet_gate(
    name: TestnetEvidenceGate,
    passed: bool,
    actual: object,
    required: object,
) -> TestnetGateResult:
    return TestnetGateResult(
        gate=name,
        passed=passed,
        actual=str(actual),
        required=str(required),
    )
