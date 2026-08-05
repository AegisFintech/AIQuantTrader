from __future__ import annotations

import hashlib
import json
import sqlite3
import stat
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from aiquanttrader_native.acceptance.audit import (
    OperationalEvidenceLog,
    read_operational_events,
)
from aiquanttrader_native.acceptance.cli import main as acceptance_main
from aiquanttrader_native.acceptance.collector import (
    SCENARIO_CATEGORIES,
    SCENARIO_CHECK_IDS,
    assemble_testnet_observation,
    load_testnet_observation,
    verify_testnet_observation,
)
from aiquanttrader_native.acceptance.models import (
    AcceptanceComponent,
    EvidenceArtifactBinding,
    EvidenceCategory,
    OperationalEventKind,
)
from aiquanttrader_native.acceptance.models import (
    TestnetAcceptanceRunManifest as AcceptanceRunManifest,
)
from aiquanttrader_native.acceptance.models import (
    TestnetFinalVenueState as FinalVenueState,
)
from aiquanttrader_native.acceptance.models import (
    TestnetOperationalFacts as OperationalFacts,
)
from aiquanttrader_native.acceptance.models import (
    TestnetScenarioCheck as ScenarioCheck,
)
from aiquanttrader_native.acceptance.models import (
    TestnetScenarioEvidence as ScenarioEvidence,
)
from aiquanttrader_native.domain.execution import (
    ExecutionJournalEvent,
    ExecutionState,
    RiskState,
)
from aiquanttrader_native.execution.journal import ExecutionJournal
from aiquanttrader_native.governance.models import TestnetLifecycleScenario as LifecycleScenario

START = 100
END = 1_000
ACCOUNT = "0x" + "1" * 40
TRADING = "0x" + "2" * 40
CONTROL = "0x" + "3" * 40


def _write_model(path: Path, model: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(model.canonical_bytes() + b"\n")  # type: ignore[attr-defined]


def _journal(path: Path) -> None:
    journal = ExecutionJournal(path)
    for index in range(15):
        intent_id = f"intent-{index}"
        client_id = f"client-{index}"
        venue_id = f"venue-{index}"
        base_ts = 200 + index * 10
        journal.begin(
            ExecutionJournalEvent(
                event_id=f"pending-{index}",
                intent_id=intent_id,
                client_order_id=client_id,
                state=ExecutionState.PENDING_SUBMIT,
                event_ts_ns=base_ts,
                detail="testnet evidence pending submit",
                source="risk",
            )
        )
        journal.append(
            ExecutionJournalEvent(
                event_id=f"submitted-{index}",
                intent_id=intent_id,
                client_order_id=client_id,
                venue_order_id=venue_id,
                state=ExecutionState.SUBMITTED,
                event_ts_ns=base_ts + 1,
                detail="testnet evidence submitted",
                source="nautilus",
            )
        )
        if index < 4:
            journal.append(
                ExecutionJournalEvent(
                    event_id=f"filled-{index}",
                    intent_id=intent_id,
                    client_order_id=client_id,
                    venue_order_id=venue_id,
                    state=ExecutionState.FILLED,
                    event_ts_ns=base_ts + 2,
                    filled_quantity_base=Decimal("0.001"),
                    detail="testnet evidence fill",
                    source="nautilus",
                )
            )
        elif index == 4:
            journal.append(
                ExecutionJournalEvent(
                    event_id="unknown-4",
                    intent_id=intent_id,
                    client_order_id=client_id,
                    venue_order_id=venue_id,
                    state=ExecutionState.UNKNOWN,
                    event_ts_ns=base_ts + 2,
                    detail="injected transport ambiguity",
                    source="reconciliation",
                )
            )
            journal.append(
                ExecutionJournalEvent(
                    event_id="resolved-4",
                    intent_id=intent_id,
                    client_order_id=client_id,
                    venue_order_id=venue_id,
                    state=ExecutionState.ACCEPTED,
                    event_ts_ns=base_ts + 3,
                    detail="venue reconciliation resolved order",
                    source="reconciliation",
                )
            )
    journal.close()


def _raw_artifacts(root: Path) -> dict[EvidenceCategory, Path]:
    raw = root / "raw"
    raw.mkdir(parents=True)
    paths = {
        EvidenceCategory.EXECUTION_JOURNAL: raw / "execution-journal.sqlite3",
        EvidenceCategory.EXECUTION_AUDIT: raw / "execution-events.jsonl",
        EvidenceCategory.SENTINEL_AUDIT: raw / "sentinel-events.jsonl",
        EvidenceCategory.EXECUTION_METRICS: raw / "execution-metrics.prom",
        EvidenceCategory.SENTINEL_METRICS: raw / "sentinel-metrics.prom",
        EvidenceCategory.VENUE_ORDERS: raw / "venue-orders.json",
        EvidenceCategory.VENUE_FILLS: raw / "venue-fills.json",
        EvidenceCategory.VENUE_ACCOUNT: raw / "venue-account.json",
        EvidenceCategory.KILL_SWITCH_AUDIT: raw / "kill-switch.audit.jsonl",
        EvidenceCategory.PROCESS_EVENTS: raw / "process-events.jsonl",
        EvidenceCategory.CONFIG_INSPECTION: raw / "compose-inspection.txt",
    }
    _journal(paths[EvidenceCategory.EXECUTION_JOURNAL])
    execution_log = OperationalEvidenceLog(
        paths[EvidenceCategory.EXECUTION_AUDIT],
        component=AcceptanceComponent.EXECUTION,
    )
    execution_log.append(
        kind=OperationalEventKind.RECONCILIATION,
        event_ts_ns=150,
        success=True,
        detail="testnet reconciliation complete",
        order_count=0,
    )
    execution_log.append(
        kind=OperationalEventKind.RISK_STATE,
        event_ts_ns=160,
        success=True,
        detail="active",
        risk_state=RiskState.ACTIVE,
    )
    execution_log.append(
        kind=OperationalEventKind.EXECUTION_CANCEL_ALL,
        event_ts_ns=800,
        success=True,
        detail="cancel-all confirmed",
        order_count=1,
    )
    sentinel_log = OperationalEvidenceLog(
        paths[EvidenceCategory.SENTINEL_AUDIT],
        component=AcceptanceComponent.SENTINEL,
    )
    sentinel_log.append(
        kind=OperationalEventKind.DEADMAN_SCHEDULE,
        event_ts_ns=170,
        success=True,
        detail="dead-man scheduled",
    )
    sentinel_log.append(
        kind=OperationalEventKind.SENTINEL_EMERGENCY_CANCEL,
        event_ts_ns=850,
        success=True,
        detail="emergency cancel confirmed",
        order_count=1,
    )
    for category, path in paths.items():
        if category in {
            EvidenceCategory.EXECUTION_JOURNAL,
            EvidenceCategory.EXECUTION_AUDIT,
            EvidenceCategory.SENTINEL_AUDIT,
        }:
            continue
        path.write_bytes(f"retained {category.value} evidence\n".encode())
    return paths


def _build_bundle(tmp_path: Path) -> tuple[Path, AcceptanceRunManifest]:
    root = (tmp_path / "evidence").resolve()
    paths = _raw_artifacts(root)
    bindings = tuple(
        EvidenceArtifactBinding(
            category=category,
            relative_path=path.relative_to(root).as_posix(),
            content_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            byte_count=path.stat().st_size,
            captured_start_ts_ns=START,
            captured_end_ts_ns=END,
        )
        for category, path in paths.items()
    )
    manifest = AcceptanceRunManifest(
        rehearsal_id="rehearsal-001",
        started_ts_ns=START,
        ended_ts_ns=END,
        commit_sha="a" * 40,
        image_digest="sha256:" + "b" * 64,
        dependency_lock_sha256="1" * 64,
        dataset_sha256="2" * 64,
        model_sha256="3" * 64,
        feature_schema_sha256="4" * 64,
        strategy_config_sha256="5" * 64,
        risk_policy_sha256="6" * 64,
        target_configuration_sha256="7" * 64,
        account_address=ACCOUNT,
        trading_wallet_address=TRADING,
        control_wallet_address=CONTROL,
        artifacts=bindings,
    )
    _write_model(root / "run-manifest.json", manifest)
    by_category = {binding.category: binding.relative_path for binding in bindings}
    _write_model(
        root / "operational-facts.json",
        OperationalFacts(
            reconciliation_failures=0,
            risk_breaches=0,
            deadman_cancellations=1,
            artifact_paths=(
                by_category[EvidenceCategory.VENUE_ACCOUNT],
                by_category[EvidenceCategory.PROCESS_EVENTS],
                by_category[EvidenceCategory.CONFIG_INSPECTION],
            ),
        ),
    )
    _write_model(
        root / "final-venue-state.json",
        FinalVenueState(
            captured_ts_ns=950,
            account_address=ACCOUNT,
            position_base=Decimal("0"),
        ),
    )
    for scenario in LifecycleScenario:
        categories = SCENARIO_CATEGORIES[scenario]
        _write_model(
            root / "scenarios" / f"{scenario.value}.json",
            ScenarioEvidence(
                scenario=scenario,
                started_ts_ns=180,
                ended_ts_ns=900,
                checks=tuple(
                    ScenarioCheck(
                        check_id=check_id,
                        passed=True,
                        actual="confirmed",
                        required="confirmed",
                    )
                    for check_id in sorted(SCENARIO_CHECK_IDS[scenario])
                ),
                artifact_paths=tuple(sorted(by_category[category] for category in categories)),
            ),
        )
    return root, manifest


def _rebind_artifact(root: Path, category: EvidenceCategory) -> None:
    manifest_path = root / "run-manifest.json"
    manifest = AcceptanceRunManifest.model_validate_json(manifest_path.read_bytes())
    artifacts = []
    for artifact in manifest.artifacts:
        if artifact.category is not category:
            artifacts.append(artifact)
            continue
        path = root / artifact.relative_path
        artifacts.append(
            artifact.model_copy(
                update={
                    "content_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "byte_count": path.stat().st_size,
                }
            )
        )
    _write_model(manifest_path, manifest.model_copy(update={"artifacts": tuple(artifacts)}))


def test_assembler_derives_complete_observation_from_retained_evidence(tmp_path: Path) -> None:
    root, manifest = _build_bundle(tmp_path)

    observation = assemble_testnet_observation(root)

    assert observation.rehearsal_id == manifest.rehearsal_id
    assert observation.orders == 15
    assert observation.fills == 4
    assert observation.unknown_outcomes == 1
    assert observation.resolved_unknown_outcomes == 1
    assert observation.duplicate_venue_orders == 0
    assert observation.cancel_all_confirmations == 2
    assert observation.deadman_cancellations == 1
    assert observation.ending_position_base == 0
    assert observation.ending_open_orders == 0
    assert len(observation.scenarios) == len(LifecycleScenario)
    assert all(result.passed for result in observation.scenarios)
    assert verify_testnet_observation(root, observation) == observation


def test_assembler_rejects_tampering_extras_and_broken_scenario_lineage(
    tmp_path: Path,
) -> None:
    root, _manifest = _build_bundle(tmp_path)
    process_events = root / "raw" / "process-events.jsonl"
    process_events.write_bytes(b"tampered\n")
    with pytest.raises(ValueError, match="digest or size mismatch"):
        assemble_testnet_observation(root)

    root, _manifest = _build_bundle(tmp_path / "extra")
    (root / "undeclared.txt").write_text("extra", encoding="utf-8")
    with pytest.raises(ValueError, match="inventory mismatch"):
        assemble_testnet_observation(root)

    root, _manifest = _build_bundle(tmp_path / "lineage")
    scenario_path = root / "scenarios" / "operator_kill.json"
    scenario = ScenarioEvidence.model_validate_json(scenario_path.read_bytes())
    _write_model(
        scenario_path,
        scenario.model_copy(update={"artifact_paths": ("raw/execution-events.jsonl",)}),
    )
    with pytest.raises(ValueError, match="categories are incomplete"):
        assemble_testnet_observation(root)

    root, _manifest = _build_bundle(tmp_path / "checks")
    scenario_path = root / "scenarios" / "operator_kill.json"
    scenario = ScenarioEvidence.model_validate_json(scenario_path.read_bytes())
    _write_model(
        scenario_path,
        scenario.model_copy(update={"checks": scenario.checks[:-1]}),
    )
    with pytest.raises(ValueError, match="required checks are incomplete"):
        assemble_testnet_observation(root)


def test_assembler_rejects_symlink_and_noncanonical_controls(tmp_path: Path) -> None:
    root, _manifest = _build_bundle(tmp_path)
    target = root / "raw" / "execution-metrics.prom"
    target.unlink()
    target.symlink_to(root / "raw" / "sentinel-metrics.prom")
    with pytest.raises(ValueError, match="symlinks"):
        assemble_testnet_observation(root)

    root, _manifest = _build_bundle(tmp_path / "canonical")
    facts_path = root / "operational-facts.json"
    payload = json.loads(facts_path.read_text(encoding="utf-8"))
    facts_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    with pytest.raises(ValueError, match="not canonical"):
        assemble_testnet_observation(root)


def test_operational_log_detects_chain_tampering_and_component_mismatch(tmp_path: Path) -> None:
    path = (tmp_path / "events.jsonl").resolve()
    log = OperationalEvidenceLog(path, component=AcceptanceComponent.EXECUTION)
    first = log.append(
        kind=OperationalEventKind.RECONCILIATION,
        event_ts_ns=1,
        success=True,
        detail="ready",
    )
    second = log.append(
        kind=OperationalEventKind.RISK_STATE,
        event_ts_ns=2,
        success=True,
        detail="active",
        risk_state=RiskState.ACTIVE,
    )
    assert second.prior_event_sha256 == first.sha256()
    restarted = OperationalEvidenceLog(path, component=AcceptanceComponent.EXECUTION)
    third = restarted.append(
        kind=OperationalEventKind.EXECUTION_CANCEL_ALL,
        event_ts_ns=3,
        success=True,
        detail="cancel command accepted",
        order_count=1,
    )
    assert third.sequence == 3
    assert third.prior_event_sha256 == second.sha256()
    assert len(read_operational_events(path)) == 3
    with pytest.raises(ValueError, match="component"):
        read_operational_events(path, expected_component=AcceptanceComponent.SENTINEL)

    payload = path.read_bytes().replace(b'"detail":"ready"', b'"detail":"badly"')
    path.write_bytes(payload)
    with pytest.raises(ValueError, match="hash chain"):
        read_operational_events(path)


def test_operational_log_rejects_unsafe_or_incomplete_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="absolute"):
        OperationalEvidenceLog(Path("relative.jsonl"), component=AcceptanceComponent.EXECUTION)

    path = (tmp_path / "events.jsonl").resolve()
    log = OperationalEvidenceLog(path, component=AcceptanceComponent.EXECUTION)
    log.append(
        kind=OperationalEventKind.RECONCILIATION,
        event_ts_ns=1,
        success=True,
        detail="ready",
    )
    path.write_bytes(path.read_bytes().rstrip(b"\n"))
    with pytest.raises(ValueError, match="incomplete"):
        read_operational_events(path)

    path.write_bytes(path.read_bytes() + b"\n")
    path.chmod(0o620)
    with pytest.raises(ValueError, match="group/world writable"):
        read_operational_events(path)
    path.chmod(0o600)

    oversized = (tmp_path / "oversized.jsonl").resolve()
    monkeypatch.setattr("aiquanttrader_native.acceptance.audit.MAX_AUDIT_BYTES", 1)
    with pytest.raises(ValueError, match="hard size bound"):
        OperationalEvidenceLog(
            oversized,
            component=AcceptanceComponent.EXECUTION,
        ).append(
            kind=OperationalEventKind.RECONCILIATION,
            event_ts_ns=1,
            success=True,
            detail="ready",
        )


def test_acceptance_models_reject_ambiguous_controls(tmp_path: Path) -> None:
    root, manifest = _build_bundle(tmp_path)
    artifact = manifest.artifacts[0]
    artifact_payload = artifact.model_dump(mode="python")
    for artifact_update, expected in (
        ({"relative_path": "../journal"}, "traversal-free"),
        ({"captured_start_ts_ns": 2, "captured_end_ts_ns": 1}, "reversed"),
    ):
        with pytest.raises(ValidationError, match=expected):
            EvidenceArtifactBinding.model_validate({**artifact_payload, **artifact_update})

    manifest_payload = manifest.model_dump(mode="python")
    duplicate_path = manifest.artifacts[-1].model_copy(
        update={"relative_path": manifest.artifacts[0].relative_path}
    )
    invalid_manifests = (
        ({"ended_ts_ns": START}, "positive interval"),
        ({"artifacts": (*manifest.artifacts[:-1], duplicate_path)}, "paths must be unique"),
        (
            {
                "artifacts": (
                    *manifest.artifacts[:-1],
                    manifest.artifacts[-1].model_copy(
                        update={"category": manifest.artifacts[0].category}
                    ),
                )
            },
            "categories are incomplete",
        ),
        (
            {
                "artifacts": (
                    manifest.artifacts[0].model_copy(update={"captured_start_ts_ns": START - 1}),
                    *manifest.artifacts[1:],
                )
            },
            "starts before",
        ),
        (
            {
                "artifacts": (
                    manifest.artifacts[0].model_copy(update={"captured_end_ts_ns": END + 1}),
                    *manifest.artifacts[1:],
                )
            },
            "ends after",
        ),
    )
    for manifest_update, expected in invalid_manifests:
        with pytest.raises(ValidationError, match=expected):
            AcceptanceRunManifest.model_validate({**manifest_payload, **manifest_update})

    scenario_path = root / "scenarios" / "passive_post_only.json"
    scenario = ScenarioEvidence.model_validate_json(scenario_path.read_bytes())
    scenario_payload = scenario.model_dump(mode="python")
    invalid_scenarios = (
        ({"ended_ts_ns": scenario.started_ts_ns}, "positive interval"),
        ({"checks": (scenario.checks[0], scenario.checks[0])}, "check identities"),
        ({"artifact_paths": (scenario.artifact_paths[0],) * 2}, "references must be unique"),
        ({"artifact_paths": ("../escape",)}, "cannot traverse"),
        ({"invalidating_events": ("fault", "fault")}, "invalidating events"),
    )
    for scenario_update, expected in invalid_scenarios:
        with pytest.raises(ValidationError, match=expected):
            ScenarioEvidence.model_validate({**scenario_payload, **scenario_update})
    assert not scenario.model_copy(update={"invalidating_events": ("fault",)}).passed
    assert not scenario.model_copy(
        update={"checks": (scenario.checks[0].model_copy(update={"passed": False}),)}
    ).passed


def test_acceptance_models_reject_invalid_final_facts_and_events() -> None:
    with pytest.raises(ValidationError, match="order identities"):
        FinalVenueState(
            captured_ts_ns=1,
            account_address=ACCOUNT,
            position_base=Decimal("0"),
            open_order_ids=("one", "one"),
        )
    for paths, expected in (
        (("raw/source", "raw/source"), "references must be unique"),
        (("../source",), "cannot traverse"),
    ):
        with pytest.raises(ValidationError, match=expected):
            OperationalFacts(
                reconciliation_failures=0,
                risk_breaches=0,
                deadman_cancellations=0,
                artifact_paths=paths,
            )

    base_event = {
        "event_id": "event-1",
        "sequence": 1,
        "component": AcceptanceComponent.EXECUTION,
        "kind": OperationalEventKind.RECONCILIATION,
        "event_ts_ns": 1,
        "success": True,
        "detail": "ready",
    }
    invalid_events = (
        ({"kind": OperationalEventKind.DEADMAN_SCHEDULE}, "sentinel-only"),
        (
            {
                "component": AcceptanceComponent.SENTINEL,
                "kind": OperationalEventKind.RECONCILIATION,
            },
            "execution-only",
        ),
        ({"prior_event_sha256": "a" * 64}, "first operational event"),
        ({"sequence": 2}, "later operational events"),
        ({"kind": OperationalEventKind.RISK_STATE}, "require the observed state"),
        ({"risk_state": RiskState.ACTIVE}, "only risk-state events"),
    )
    from aiquanttrader_native.acceptance.models import OperationalEvidenceEvent

    for event_update, expected in invalid_events:
        with pytest.raises(ValidationError, match=expected):
            OperationalEvidenceEvent.model_validate({**base_event, **event_update})


def test_collector_rejects_nonreproducible_observation_and_invalid_lineage(
    tmp_path: Path,
) -> None:
    root, _manifest = _build_bundle(tmp_path)
    observation = assemble_testnet_observation(root)
    with pytest.raises(ValueError, match="does not match"):
        verify_testnet_observation(root, observation.model_copy(update={"orders": 16}))
    with pytest.raises(ValueError, match="absolute"):
        assemble_testnet_observation(Path("relative"))

    observation_path = (tmp_path / "observation.json").resolve()
    observation_path.write_text(observation.model_dump_json(indent=2), encoding="utf-8")
    with pytest.raises(ValueError, match="not canonical"):
        load_testnet_observation(observation_path)

    final_path = root / "final-venue-state.json"
    final_state = FinalVenueState.model_validate_json(final_path.read_bytes())
    _write_model(
        final_path,
        final_state.model_copy(update={"account_address": "0x" + "9" * 40}),
    )
    with pytest.raises(ValueError, match="different account"):
        assemble_testnet_observation(root)


def test_collector_rejects_unsafe_permissions_and_failed_deadman_claim(tmp_path: Path) -> None:
    root, _manifest = _build_bundle(tmp_path)
    metrics = root / "raw" / "execution-metrics.prom"
    metrics.chmod(0o620)
    with pytest.raises(ValueError, match="group/world writable"):
        assemble_testnet_observation(root)

    root, _manifest = _build_bundle(tmp_path / "deadman")
    scenario_path = root / "scenarios" / "sentinel_death.json"
    scenario = ScenarioEvidence.model_validate_json(scenario_path.read_bytes())
    _write_model(
        scenario_path,
        scenario.model_copy(
            update={
                "checks": (
                    scenario.checks[0].model_copy(update={"passed": False}),
                    *scenario.checks[1:],
                )
            }
        ),
    )
    with pytest.raises(ValueError, match="dead-man cancellation count"):
        assemble_testnet_observation(root)


def test_collector_rejects_internally_inconsistent_execution_database(tmp_path: Path) -> None:
    root, _manifest = _build_bundle(tmp_path)
    journal = root / "raw" / "execution-journal.sqlite3"
    connection = sqlite3.connect(journal)
    connection.execute("UPDATE events SET state = 'accepted' WHERE sequence = 1")
    connection.commit()
    connection.close()
    _rebind_artifact(root, EvidenceCategory.EXECUTION_JOURNAL)
    with pytest.raises(ValueError, match="columns disagree"):
        assemble_testnet_observation(root)

    root, _manifest = _build_bundle(tmp_path / "snapshot")
    journal = root / "raw" / "execution-journal.sqlite3"
    connection = sqlite3.connect(journal)
    connection.execute("UPDATE orders SET state = 'denied' WHERE intent_id = 'intent-0'")
    connection.commit()
    connection.close()
    _rebind_artifact(root, EvidenceCategory.EXECUTION_JOURNAL)
    with pytest.raises(ValueError, match="snapshot disagrees"):
        assemble_testnet_observation(root)


def test_acceptance_cli_writes_once_and_verifies(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, _manifest = _build_bundle(tmp_path)
    output = (tmp_path / "observation.json").resolve()

    assert acceptance_main(["assemble", "--evidence-root", str(root), "--output", str(output)]) == 0
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert acceptance_main(["assemble", "--evidence-root", str(root), "--output", str(output)]) == 2
    capsys.readouterr()
    assert (
        acceptance_main(["verify", "--evidence-root", str(root), "--observation", str(output)]) == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "verified"


def test_acceptance_models_reject_incomplete_or_ambiguous_identity(tmp_path: Path) -> None:
    _root, manifest = _build_bundle(tmp_path)
    with pytest.raises(ValidationError, match="identities must be distinct"):
        AcceptanceRunManifest.model_validate(
            {
                **manifest.model_dump(mode="python"),
                "control_wallet_address": manifest.trading_wallet_address,
            }
        )
    with pytest.raises(ValidationError, match="at least 11 items"):
        AcceptanceRunManifest.model_validate(
            {
                **manifest.model_dump(mode="python"),
                "artifacts": manifest.artifacts[:-1],
            }
        )


def test_acceptance_cli_rejects_relative_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, _manifest = _build_bundle(tmp_path)
    assert (
        acceptance_main(["assemble", "--evidence-root", str(root), "--output", "relative.json"])
        == 2
    )
    assert "must be absolute" in capsys.readouterr().err
