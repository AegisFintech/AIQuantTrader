"""Deterministically assemble a testnet observation from retained run evidence."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from aiquanttrader.acceptance.audit import read_operational_events
from aiquanttrader.acceptance.models import (
    AcceptanceComponent,
    EvidenceArtifactBinding,
    EvidenceCategory,
    OperationalEventKind,
    OperationalEvidenceEvent,
    TestnetAcceptanceRunManifest,
    TestnetFinalVenueState,
    TestnetOperationalFacts,
    TestnetScenarioEvidence,
)
from aiquanttrader.domain.base import DomainModel, canonical_sha256
from aiquanttrader.domain.execution import ExecutionJournalEvent, ExecutionState
from aiquanttrader.governance.models import (
    TestnetDressRehearsalObservation,
    TestnetLifecycleScenario,
    TestnetScenarioResult,
)

MAX_BUNDLE_FILES = 256
MAX_BUNDLE_BYTES = 268_435_456
MAX_CONTROL_BYTES = 1_048_576

CONTROL_FILES = frozenset(
    {
        "run-manifest.json",
        "operational-facts.json",
        "final-venue-state.json",
    }
)

SCENARIO_CATEGORIES: dict[TestnetLifecycleScenario, frozenset[EvidenceCategory]] = {
    TestnetLifecycleScenario.PASSIVE_POST_ONLY: frozenset(
        {EvidenceCategory.EXECUTION_JOURNAL, EvidenceCategory.VENUE_ORDERS}
    ),
    TestnetLifecycleScenario.CROSSING_POST_ONLY_REJECT: frozenset(
        {EvidenceCategory.EXECUTION_JOURNAL, EvidenceCategory.VENUE_ORDERS}
    ),
    TestnetLifecycleScenario.NON_MARKETABLE_IOC: frozenset(
        {EvidenceCategory.EXECUTION_JOURNAL, EvidenceCategory.VENUE_ORDERS}
    ),
    TestnetLifecycleScenario.MARKETABLE_IOC: frozenset(
        {EvidenceCategory.EXECUTION_JOURNAL, EvidenceCategory.VENUE_FILLS}
    ),
    TestnetLifecycleScenario.CANCEL_REPLACE: frozenset(
        {EvidenceCategory.EXECUTION_JOURNAL, EvidenceCategory.VENUE_ORDERS}
    ),
    TestnetLifecycleScenario.PARTIAL_FILL_CANCEL: frozenset(
        {
            EvidenceCategory.EXECUTION_JOURNAL,
            EvidenceCategory.VENUE_ORDERS,
            EvidenceCategory.VENUE_FILLS,
        }
    ),
    TestnetLifecycleScenario.REDUCE_ONLY: frozenset(
        {
            EvidenceCategory.EXECUTION_JOURNAL,
            EvidenceCategory.VENUE_ACCOUNT,
            EvidenceCategory.VENUE_FILLS,
        }
    ),
    TestnetLifecycleScenario.DUPLICATE_INTENT: frozenset({EvidenceCategory.EXECUTION_JOURNAL}),
    TestnetLifecycleScenario.UNKNOWN_OUTCOME_RECONCILIATION: frozenset(
        {EvidenceCategory.EXECUTION_JOURNAL, EvidenceCategory.PROCESS_EVENTS}
    ),
    TestnetLifecycleScenario.NODE_RESTART_RECONCILIATION: frozenset(
        {
            EvidenceCategory.EXECUTION_JOURNAL,
            EvidenceCategory.EXECUTION_AUDIT,
            EvidenceCategory.PROCESS_EVENTS,
        }
    ),
    TestnetLifecycleScenario.STALE_DATA_KILL: frozenset(
        {
            EvidenceCategory.EXECUTION_AUDIT,
            EvidenceCategory.SENTINEL_AUDIT,
            EvidenceCategory.PROCESS_EVENTS,
        }
    ),
    TestnetLifecycleScenario.LOSS_DRAWDOWN_REDUCE_ONLY: frozenset(
        {EvidenceCategory.EXECUTION_AUDIT, EvidenceCategory.EXECUTION_JOURNAL}
    ),
    TestnetLifecycleScenario.OPERATOR_KILL: frozenset(
        {
            EvidenceCategory.EXECUTION_AUDIT,
            EvidenceCategory.SENTINEL_AUDIT,
            EvidenceCategory.KILL_SWITCH_AUDIT,
            EvidenceCategory.VENUE_ORDERS,
        }
    ),
    TestnetLifecycleScenario.TRADING_NODE_DEATH: frozenset(
        {
            EvidenceCategory.SENTINEL_AUDIT,
            EvidenceCategory.PROCESS_EVENTS,
            EvidenceCategory.VENUE_ORDERS,
        }
    ),
    TestnetLifecycleScenario.SENTINEL_DEATH: frozenset(
        {EvidenceCategory.VENUE_ORDERS, EvidenceCategory.PROCESS_EVENTS}
    ),
}

SCENARIO_CHECK_IDS: dict[TestnetLifecycleScenario, frozenset[str]] = {
    TestnetLifecycleScenario.PASSIVE_POST_ONLY: frozenset(
        {"post_only_rested", "client_order_identity", "cancel_confirmed"}
    ),
    TestnetLifecycleScenario.CROSSING_POST_ONLY_REJECT: frozenset({"venue_rejected", "no_fill"}),
    TestnetLifecycleScenario.NON_MARKETABLE_IOC: frozenset({"ioc_terminal", "no_false_acceptance"}),
    TestnetLifecycleScenario.MARKETABLE_IOC: frozenset({"fill_accounted", "remainder_terminal"}),
    TestnetLifecycleScenario.CANCEL_REPLACE: frozenset(
        {"old_leg_terminal", "replacement_identity", "no_overlap"}
    ),
    TestnetLifecycleScenario.PARTIAL_FILL_CANCEL: frozenset(
        {"cumulative_fill_exact", "residual_cancel_confirmed"}
    ),
    TestnetLifecycleScenario.REDUCE_ONLY: frozenset(
        {"absolute_position_reduced", "increase_denied"}
    ),
    TestnetLifecycleScenario.DUPLICATE_INTENT: frozenset(
        {"local_duplicate_denied", "single_venue_order"}
    ),
    TestnetLifecycleScenario.UNKNOWN_OUTCOME_RECONCILIATION: frozenset(
        {"unknown_recorded", "reconciled_by_client_id", "not_resubmitted"}
    ),
    TestnetLifecycleScenario.NODE_RESTART_RECONCILIATION: frozenset(
        {"reconciliation_precedes_approval", "orders_fills_position_match", "no_duplicate_order"}
    ),
    TestnetLifecycleScenario.STALE_DATA_KILL: frozenset(
        {"new_exposure_denied", "cancel_available"}
    ),
    TestnetLifecycleScenario.LOSS_DRAWDOWN_REDUCE_ONLY: frozenset(
        {"new_exposure_denied", "bounded_reduce_only_available"}
    ),
    TestnetLifecycleScenario.OPERATOR_KILL: frozenset({"approval_halted", "cancel_all_confirmed"}),
    TestnetLifecycleScenario.TRADING_NODE_DEATH: frozenset(
        {"sentinel_detected_failure", "cancel_all_confirmed", "deadman_observed"}
    ),
    TestnetLifecycleScenario.SENTINEL_DEATH: frozenset(
        {"scheduled_cancel_fired", "fired_within_bound"}
    ),
}


@dataclass(frozen=True, slots=True)
class ExecutionEvidenceStats:
    orders: int
    fills: int
    unknown_outcomes: int
    resolved_unknown_outcomes: int
    duplicate_venue_orders: int


def assemble_testnet_observation(root: Path) -> TestnetDressRehearsalObservation:
    """Build an observation without network, wallet, signer, or order capability."""

    evidence_root = _validated_root(root)
    manifest = _load_control(evidence_root / "run-manifest.json", TestnetAcceptanceRunManifest)
    facts = _load_control(evidence_root / "operational-facts.json", TestnetOperationalFacts)
    final_state = _load_control(evidence_root / "final-venue-state.json", TestnetFinalVenueState)
    bindings = {binding.relative_path: binding for binding in manifest.artifacts}
    scenarios = _load_scenarios(evidence_root, manifest, bindings)
    inventory = _validate_inventory(evidence_root, bindings)
    _validate_control_lineage(manifest, facts, final_state, scenarios, bindings)

    journal_path = _single_artifact_path(
        evidence_root, manifest.artifacts, EvidenceCategory.EXECUTION_JOURNAL
    )
    execution_audit_path = _single_artifact_path(
        evidence_root, manifest.artifacts, EvidenceCategory.EXECUTION_AUDIT
    )
    sentinel_audit_path = _single_artifact_path(
        evidence_root, manifest.artifacts, EvidenceCategory.SENTINEL_AUDIT
    )
    journal_stats = _execution_stats(
        journal_path,
        started_ts_ns=manifest.started_ts_ns,
        ended_ts_ns=manifest.ended_ts_ns,
    )
    execution_events = _events_in_interval(
        read_operational_events(
            execution_audit_path,
            expected_component=AcceptanceComponent.EXECUTION,
        ),
        manifest,
    )
    sentinel_events = _events_in_interval(
        read_operational_events(
            sentinel_audit_path,
            expected_component=AcceptanceComponent.SENTINEL,
        ),
        manifest,
    )
    if not any(
        event.kind is OperationalEventKind.RECONCILIATION and event.success
        for event in execution_events
    ):
        raise ValueError("execution evidence has no successful reconciliation event")
    if not any(
        event.kind is OperationalEventKind.DEADMAN_SCHEDULE and event.success
        for event in sentinel_events
    ):
        raise ValueError("sentinel evidence has no successful dead-man schedule")
    cancel_all_confirmations = int(
        any(
            event.success and event.kind is OperationalEventKind.EXECUTION_CANCEL_ALL
            for event in execution_events
        )
        and _passed_check(
            scenarios[TestnetLifecycleScenario.OPERATOR_KILL],
            "cancel_all_confirmed",
        )
    ) + int(
        any(
            event.success and event.kind is OperationalEventKind.SENTINEL_EMERGENCY_CANCEL
            for event in sentinel_events
        )
        and _passed_check(
            scenarios[TestnetLifecycleScenario.TRADING_NODE_DEATH],
            "cancel_all_confirmed",
        )
    )
    confirmed_inventory = _validate_inventory(evidence_root, bindings)
    if confirmed_inventory != inventory:
        raise ValueError("acceptance evidence changed during assembly")
    bundle_sha256 = canonical_sha256(
        {
            "schema_version": 1,
            "files": [
                {
                    "relative_path": relative_path,
                    "content_sha256": digest,
                    "byte_count": byte_count,
                }
                for relative_path, digest, byte_count in confirmed_inventory
            ],
        }
    )
    return TestnetDressRehearsalObservation(
        rehearsal_id=manifest.rehearsal_id,
        started_ts_ns=manifest.started_ts_ns,
        ended_ts_ns=manifest.ended_ts_ns,
        commit_sha=manifest.commit_sha,
        image_digest=manifest.image_digest,
        dependency_lock_sha256=manifest.dependency_lock_sha256,
        dataset_sha256=manifest.dataset_sha256,
        model_sha256=manifest.model_sha256,
        feature_schema_sha256=manifest.feature_schema_sha256,
        strategy_config_sha256=manifest.strategy_config_sha256,
        risk_policy_sha256=manifest.risk_policy_sha256,
        target_configuration_sha256=manifest.target_configuration_sha256,
        account_address=manifest.account_address,
        vault_address=manifest.vault_address,
        trading_wallet_address=manifest.trading_wallet_address,
        control_wallet_address=manifest.control_wallet_address,
        mainnet_credentials_present=facts.mainnet_credentials_present,
        orders=journal_stats.orders,
        fills=journal_stats.fills,
        unknown_outcomes=journal_stats.unknown_outcomes,
        resolved_unknown_outcomes=journal_stats.resolved_unknown_outcomes,
        reconciliation_failures=facts.reconciliation_failures,
        duplicate_venue_orders=journal_stats.duplicate_venue_orders,
        risk_breaches=facts.risk_breaches,
        cancel_all_confirmations=cancel_all_confirmations,
        deadman_cancellations=facts.deadman_cancellations,
        ending_position_base=final_state.position_base,
        ending_open_orders=len(final_state.open_order_ids),
        scenarios=tuple(
            TestnetScenarioResult(
                scenario=scenario,
                passed=scenarios[scenario].passed,
                evidence_sha256=_sha256(evidence_root / "scenarios" / f"{scenario.value}.json"),
            )
            for scenario in TestnetLifecycleScenario
        ),
        evidence_bundle_sha256=bundle_sha256,
    )


def verify_testnet_observation(
    root: Path,
    observation: TestnetDressRehearsalObservation,
) -> TestnetDressRehearsalObservation:
    assembled = assemble_testnet_observation(root)
    if assembled != observation:
        raise ValueError("testnet observation does not match the retained evidence bundle")
    return assembled


def load_testnet_observation(path: Path) -> TestnetDressRehearsalObservation:
    payload = _read_regular(path, maximum_bytes=MAX_CONTROL_BYTES)
    observation = TestnetDressRehearsalObservation.model_validate_json(payload)
    if payload != observation.canonical_bytes() + b"\n":
        raise ValueError("testnet observation is not canonical JSON")
    return observation


def _validated_root(root: Path) -> Path:
    if not root.is_absolute():
        raise ValueError("acceptance evidence root must be absolute")
    resolved = root.resolve(strict=True)
    if not resolved.is_dir() or resolved.is_symlink():
        raise ValueError("acceptance evidence root must be a non-symlink directory")
    return resolved


def _load_scenarios(
    root: Path,
    manifest: TestnetAcceptanceRunManifest,
    bindings: dict[str, EvidenceArtifactBinding],
) -> dict[TestnetLifecycleScenario, TestnetScenarioEvidence]:
    scenarios: dict[TestnetLifecycleScenario, TestnetScenarioEvidence] = {}
    categories = {path: binding.category for path, binding in bindings.items()}
    for scenario in TestnetLifecycleScenario:
        evidence = _load_control(
            root / "scenarios" / f"{scenario.value}.json",
            TestnetScenarioEvidence,
        )
        if evidence.scenario is not scenario:
            raise ValueError(f"scenario filename does not match its payload: {scenario.value}")
        if (
            evidence.started_ts_ns < manifest.started_ts_ns
            or evidence.ended_ts_ns > manifest.ended_ts_ns
        ):
            raise ValueError(f"scenario interval escapes the acceptance run: {scenario.value}")
        missing = set(evidence.artifact_paths) - set(bindings)
        if missing:
            raise ValueError(f"scenario references unbound artifacts: {sorted(missing)}")
        observed_categories = {categories[path] for path in evidence.artifact_paths}
        required_categories = SCENARIO_CATEGORIES[scenario]
        if not observed_categories.issuperset(required_categories):
            raise ValueError(f"scenario evidence categories are incomplete: {scenario.value}")
        observed_checks = {check.check_id for check in evidence.checks}
        if not observed_checks.issuperset(SCENARIO_CHECK_IDS[scenario]):
            raise ValueError(f"scenario required checks are incomplete: {scenario.value}")
        scenarios[scenario] = evidence
    return scenarios


def _validate_inventory(
    root: Path,
    bindings: dict[str, EvidenceArtifactBinding],
) -> tuple[tuple[str, str, int], ...]:
    scenario_files = {f"scenarios/{scenario.value}.json" for scenario in TestnetLifecycleScenario}
    expected = set(CONTROL_FILES) | scenario_files | set(bindings)
    actual: dict[str, Path] = {}
    total_bytes = 0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"acceptance evidence cannot contain symlinks: {path.name}")
        if path.is_dir():
            continue
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"acceptance evidence contains a non-regular file: {path.name}")
        if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ValueError(f"acceptance evidence file is group/world writable: {path.name}")
        relative = path.relative_to(root).as_posix()
        actual[relative] = path
        total_bytes += metadata.st_size
    if len(actual) > MAX_BUNDLE_FILES or total_bytes > MAX_BUNDLE_BYTES:
        raise ValueError("acceptance evidence bundle exceeds its hard resource bounds")
    if set(actual) != expected:
        missing = sorted(expected - set(actual))
        extra = sorted(set(actual) - expected)
        raise ValueError(
            f"acceptance evidence inventory mismatch: missing={missing}, extra={extra}"
        )
    inventory: list[tuple[str, str, int]] = []
    for relative, path in sorted(actual.items()):
        payload = _read_regular(path, maximum_bytes=67_108_864)
        digest = hashlib.sha256(payload).hexdigest()
        binding = bindings.get(relative)
        if binding is not None and (
            binding.content_sha256 != digest or binding.byte_count != len(payload)
        ):
            raise ValueError(f"acceptance artifact digest or size mismatch: {relative}")
        inventory.append((relative, digest, len(payload)))
    return tuple(inventory)


def _validate_control_lineage(
    manifest: TestnetAcceptanceRunManifest,
    facts: TestnetOperationalFacts,
    final_state: TestnetFinalVenueState,
    scenarios: dict[TestnetLifecycleScenario, TestnetScenarioEvidence],
    bindings: dict[str, EvidenceArtifactBinding],
) -> None:
    if (
        final_state.captured_ts_ns < manifest.started_ts_ns
        or final_state.captured_ts_ns > manifest.ended_ts_ns
    ):
        raise ValueError("final venue snapshot timestamp escapes the acceptance run")
    if final_state.account_address.lower() != manifest.account_address.lower() or _lower(
        final_state.vault_address
    ) != _lower(manifest.vault_address):
        raise ValueError("final venue snapshot belongs to a different account or vault")
    if set(facts.artifact_paths) - set(bindings):
        raise ValueError("operational facts reference unbound artifacts")
    fact_categories = {bindings[path].category for path in facts.artifact_paths}
    if not fact_categories.issuperset(
        {
            EvidenceCategory.VENUE_ACCOUNT,
            EvidenceCategory.PROCESS_EVENTS,
            EvidenceCategory.CONFIG_INSPECTION,
        }
    ):
        raise ValueError("operational facts lack venue, process, or credential-mount evidence")
    if (
        facts.deadman_cancellations
        and not scenarios[TestnetLifecycleScenario.SENTINEL_DEATH].passed
    ):
        raise ValueError("dead-man cancellation count lacks a passing sentinel-death drill")


def _single_artifact_path(
    root: Path,
    bindings: tuple[EvidenceArtifactBinding, ...],
    category: EvidenceCategory,
) -> Path:
    matches = [binding for binding in bindings if binding.category is category]
    if len(matches) != 1:
        raise ValueError(f"acceptance requires exactly one {category.value} artifact")
    return root / matches[0].relative_path


def _execution_stats(
    path: Path,
    *,
    started_ts_ns: int,
    ended_ts_ns: int,
) -> ExecutionEvidenceStats:
    uri = f"{path.as_uri()}?mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise ValueError("cannot open retained execution journal read-only") from exc
    try:
        check = connection.execute("PRAGMA quick_check").fetchone()
        if check is None or check[0] != "ok":
            raise ValueError("retained execution journal failed SQLite integrity check")
        rows = connection.execute(
            """
            SELECT sequence, event_id, intent_id, event_ts_ns, state, event_json
            FROM events ORDER BY sequence
            """
        ).fetchall()
        order_rows = connection.execute(
            """
            SELECT intent_id, client_order_id, venue_order_id, state,
                   updated_ts_ns, filled_quantity_base
            FROM orders ORDER BY intent_id
            """
        ).fetchall()
    except sqlite3.Error as exc:
        raise ValueError("retained execution journal schema is unavailable") from exc
    finally:
        connection.close()
    parsed_events: list[ExecutionJournalEvent] = []
    prior_sequence = 0
    for row in rows:
        event = ExecutionJournalEvent.model_validate_json(row[5])
        if row[0] <= prior_sequence:
            raise ValueError("execution journal sequence is not strictly increasing")
        prior_sequence = row[0]
        if (row[1], row[2], row[3], row[4]) != (
            event.event_id,
            event.intent_id,
            event.event_ts_ns,
            event.state.value,
        ):
            raise ValueError("execution journal columns disagree with the event payload")
        parsed_events.append(event)
    if any(
        event.event_ts_ns < started_ts_ns or event.event_ts_ns > ended_ts_ns
        for event in parsed_events
    ):
        raise ValueError("execution journal contains events outside the acceptance run")
    events = parsed_events
    timelines: dict[str, list[ExecutionJournalEvent]] = defaultdict(list)
    venue_intents: dict[str, set[str]] = defaultdict(set)
    submitted: set[str] = set()
    filled: set[str] = set()
    unknown: set[str] = set()
    for event in events:
        timeline = timelines[event.intent_id]
        if timeline and event.event_ts_ns < timeline[-1].event_ts_ns:
            raise ValueError("execution journal event time moves backward within an intent")
        timeline.append(event)
        if event.state is ExecutionState.SUBMITTED:
            submitted.add(event.intent_id)
        if event.state in {ExecutionState.PARTIALLY_FILLED, ExecutionState.FILLED}:
            filled.add(event.intent_id)
        if event.state is ExecutionState.UNKNOWN:
            unknown.add(event.intent_id)
        if event.venue_order_id is not None:
            venue_intents[event.venue_order_id].add(event.intent_id)
    if {str(row[0]) for row in order_rows} != set(timelines):
        raise ValueError("execution journal order and event inventories disagree")
    for row in order_rows:
        timeline = timelines[str(row[0])]
        final = timeline[-1]
        client_order_id = next(
            (event.client_order_id for event in reversed(timeline) if event.client_order_id),
            None,
        )
        venue_order_id = next(
            (event.venue_order_id for event in reversed(timeline) if event.venue_order_id),
            None,
        )
        if (row[1], row[2], row[3], row[4], row[5]) != (
            client_order_id,
            venue_order_id,
            final.state.value,
            final.event_ts_ns,
            str(final.filled_quantity_base),
        ):
            raise ValueError("execution journal order snapshot disagrees with its event history")
    resolved = 0
    resolved_states = {
        ExecutionState.ACCEPTED,
        ExecutionState.PARTIALLY_FILLED,
        ExecutionState.FILLED,
        ExecutionState.CANCELED,
        ExecutionState.REJECTED,
        ExecutionState.DENIED,
    }
    for intent_id in unknown:
        timeline = timelines[intent_id]
        last_unknown = max(
            index for index, event in enumerate(timeline) if event.state is ExecutionState.UNKNOWN
        )
        if any(event.state in resolved_states for event in timeline[last_unknown + 1 :]):
            resolved += 1
    duplicates = sum(
        len(intent_ids) - 1 for intent_ids in venue_intents.values() if len(intent_ids) > 1
    )
    return ExecutionEvidenceStats(
        orders=len(submitted),
        fills=len(filled),
        unknown_outcomes=len(unknown),
        resolved_unknown_outcomes=resolved,
        duplicate_venue_orders=duplicates,
    )


def _events_in_interval(
    events: tuple[OperationalEvidenceEvent, ...],
    manifest: TestnetAcceptanceRunManifest,
) -> tuple[OperationalEvidenceEvent, ...]:
    filtered = tuple(
        event
        for event in events
        if manifest.started_ts_ns <= event.event_ts_ns <= manifest.ended_ts_ns
    )
    if len(filtered) != len(events):
        raise ValueError("operational audit contains events outside the acceptance run")
    return filtered


def _passed_check(evidence: TestnetScenarioEvidence, check_id: str) -> bool:
    return any(check.check_id == check_id and check.passed for check in evidence.checks)


def _load_control[ModelT: DomainModel](path: Path, model: type[ModelT]) -> ModelT:
    payload = _read_regular(path, maximum_bytes=MAX_CONTROL_BYTES)
    value = model.model_validate_json(payload)
    if payload != value.canonical_bytes() + b"\n":
        raise ValueError(f"acceptance control file is not canonical JSON: {path.name}")
    return value


def _read_regular(path: Path, *, maximum_bytes: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"cannot open acceptance evidence file: {path.name}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"acceptance evidence is not regular: {path.name}")
        if metadata.st_size <= 0 or metadata.st_size > maximum_bytes:
            raise ValueError(f"acceptance evidence size is invalid: {path.name}")
        payload = bytearray()
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1_048_576))
            if not chunk:
                break
            payload.extend(chunk)
            remaining -= len(chunk)
        final = os.fstat(descriptor)
        identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if len(payload) != metadata.st_size or any(
            getattr(metadata, field) != getattr(final, field) for field in identity
        ):
            raise ValueError(f"acceptance evidence changed while read: {path.name}")
        return bytes(payload)
    finally:
        os.close(descriptor)


def _sha256(path: Path) -> str:
    return hashlib.sha256(_read_regular(path, maximum_bytes=MAX_CONTROL_BYTES)).hexdigest()


def _lower(value: str | None) -> str | None:
    return None if value is None else value.lower()
