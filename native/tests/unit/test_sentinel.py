from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from prometheus_client import CollectorRegistry

from aiquanttrader_native.acceptance.audit import (
    OperationalEvidenceLog,
    read_operational_events,
)
from aiquanttrader_native.acceptance.models import (
    AcceptanceComponent,
    OperationalEventKind,
)
from aiquanttrader_native.config import load_config
from aiquanttrader_native.execution.heartbeat import HeartbeatPublisher
from aiquanttrader_native.execution.secrets import PrivateKey
from aiquanttrader_native.risk import KillSwitchStore
from aiquanttrader_native.sentinel.metrics import SentinelMetrics
from aiquanttrader_native.sentinel.service import HyperliquidControlClient, SafetySentinel

ACCOUNT = "0x" + "1" * 40
VAULT = "0x" + "2" * 40


class FakeControlClient:
    def __init__(self) -> None:
        self.deadlines: list[int | None] = []
        self.cancel_calls = 0
        self.fail_cancel = False
        self.fail_schedule = False

    def schedule_cancel(self, deadline_ms: int | None) -> None:
        if self.fail_schedule:
            raise RuntimeError("schedule failed")
        self.deadlines.append(deadline_ms)

    def cancel_all(self) -> int:
        self.cancel_calls += 1
        if self.fail_cancel:
            raise RuntimeError("cancel failed")
        return 2


def enabled_bundle(config_dir: Path) -> Any:
    return load_config(
        config_dir,
        "testnet",
        environ={
            "AQT_NATIVE__EXCHANGE__ACCOUNT_ADDRESS": ACCOUNT,
            "AQT_NATIVE__EXCHANGE__TRADING_WALLET_SECRET_PATH": (
                "/run/secrets/testnet-trading-wallet"
            ),
            "AQT_NATIVE__EXCHANGE__CONTROL_WALLET_SECRET_PATH": (
                "/run/secrets/testnet-control-wallet"
            ),
            "AQT_NATIVE__EXECUTION__ENABLED": "true",
            "AQT_NATIVE__SENTINEL__ENABLED": "true",
        },
    )


def write_healthy_heartbeat(bundle: Any, path: Path, now: int) -> None:
    kill = KillSwitchStore((path.parent / "kill.json").resolve())
    publisher = HeartbeatPublisher(
        path,
        environment="testnet",
        account_address=ACCOUNT,
        config_fingerprint=bundle.fingerprint,
        kill_switch=kill,
    )
    publisher.set_health(execution_healthy=True, reconciliation_complete=True)
    publisher.publish(now_ns=now)


def test_sentinel_renews_only_for_exact_healthy_heartbeat(config_dir: Path, tmp_path: Path) -> None:
    bundle = enabled_bundle(config_dir)
    now = 100_000_000_000
    clock = [now]
    heartbeat = (tmp_path / "heartbeat.json").resolve()
    write_healthy_heartbeat(bundle, heartbeat, now)
    client = FakeControlClient()
    audit_path = (tmp_path / "sentinel-events.jsonl").resolve()
    sentinel = SafetySentinel(
        bundle=bundle,
        heartbeat_path=heartbeat,
        client=client,
        metrics=SentinelMetrics(CollectorRegistry()),
        operational_log=OperationalEvidenceLog(
            audit_path,
            component=AcceptanceComponent.SENTINEL,
        ),
        clock_ns=lambda: clock[0],
    )
    assert sentinel.step()
    assert client.deadlines == [120_000]
    assert sentinel.step()
    assert len(client.deadlines) == 1

    heartbeat.write_text("{broken", encoding="utf-8")
    assert not sentinel.step()
    assert client.cancel_calls == 1
    assert not sentinel.step()
    assert client.cancel_calls == 1
    clock[0] += 5_000_000_000
    assert not sentinel.step()
    assert client.cancel_calls == 2
    events = read_operational_events(
        audit_path,
        expected_component=AcceptanceComponent.SENTINEL,
    )
    assert [event.kind for event in events] == [
        OperationalEventKind.DEADMAN_SCHEDULE,
        OperationalEventKind.HEARTBEAT_STATE,
        OperationalEventKind.SENTINEL_EMERGENCY_CANCEL,
        OperationalEventKind.HEARTBEAT_STATE,
    ]
    assert events[-2].order_count == 2


def test_sentinel_recovers_and_rejects_stale_or_mismatched_state(
    config_dir: Path, tmp_path: Path
) -> None:
    bundle = enabled_bundle(config_dir)
    now = 200_000_000_000
    heartbeat = (tmp_path / "heartbeat.json").resolve()
    write_healthy_heartbeat(bundle, heartbeat, now - 6_000_000_000)
    client = FakeControlClient()
    sentinel = SafetySentinel(
        bundle=bundle,
        heartbeat_path=heartbeat,
        client=client,
        metrics=SentinelMetrics(CollectorRegistry()),
        clock_ns=lambda: now,
    )
    assert not sentinel.step()
    assert client.cancel_calls == 1

    write_healthy_heartbeat(bundle, heartbeat, now)
    assert sentinel.step()
    assert client.deadlines

    payload = heartbeat.read_text(encoding="utf-8").replace(bundle.fingerprint, "b" * 64)
    heartbeat.write_text(payload, encoding="utf-8")
    assert not sentinel.step()
    assert client.cancel_calls == 2


def test_sentinel_cancels_when_durable_deployment_admission_is_inactive(
    config_dir: Path,
    tmp_path: Path,
) -> None:
    bundle = enabled_bundle(config_dir)
    now = 300_000_000_000
    heartbeat = (tmp_path / "heartbeat.json").resolve()
    write_healthy_heartbeat(bundle, heartbeat, now)
    guard = Mock()
    guard.is_active.return_value = False
    client = FakeControlClient()
    sentinel = SafetySentinel(
        bundle=bundle,
        heartbeat_path=heartbeat,
        client=client,
        metrics=SentinelMetrics(CollectorRegistry()),
        admission_guard=guard,
        clock_ns=lambda: now,
    )

    assert not sentinel.step()
    assert client.cancel_calls == 1


def test_sentinel_surfaces_exchange_failures(config_dir: Path, tmp_path: Path) -> None:
    bundle = enabled_bundle(config_dir)
    client = FakeControlClient()
    client.fail_cancel = True
    sentinel = SafetySentinel(
        bundle=bundle,
        heartbeat_path=(tmp_path / "missing.json").resolve(),
        client=client,
        metrics=SentinelMetrics(CollectorRegistry()),
        clock_ns=lambda: 1,
    )
    with pytest.raises(RuntimeError, match="cancel failed"):
        sentinel.step()

    now = 10_000_000_000
    heartbeat = (tmp_path / "healthy.json").resolve()
    write_healthy_heartbeat(bundle, heartbeat, now)
    client = FakeControlClient()
    client.fail_schedule = True
    sentinel = SafetySentinel(
        bundle=bundle,
        heartbeat_path=heartbeat,
        client=client,
        metrics=SentinelMetrics(CollectorRegistry()),
        clock_ns=lambda: now,
    )
    with pytest.raises(RuntimeError, match="schedule failed"):
        sentinel.step()


def test_audit_failure_cannot_prevent_primary_safety_action(
    config_dir: Path,
    tmp_path: Path,
) -> None:
    bundle = enabled_bundle(config_dir)
    audit_path = (tmp_path / "unwritable-policy.jsonl").resolve()
    audit = OperationalEvidenceLog(audit_path, component=AcceptanceComponent.SENTINEL)
    audit_path.touch(mode=0o620)
    audit_path.chmod(0o620)

    client = FakeControlClient()
    unhealthy = SafetySentinel(
        bundle=bundle,
        heartbeat_path=(tmp_path / "missing.json").resolve(),
        client=client,
        metrics=SentinelMetrics(CollectorRegistry()),
        operational_log=audit,
        clock_ns=lambda: 1,
    )
    with pytest.raises(ValueError, match="group/world writable"):
        unhealthy.step()
    assert client.cancel_calls == 1

    now = 10_000_000_000
    heartbeat = (tmp_path / "healthy-audit-failure.json").resolve()
    write_healthy_heartbeat(bundle, heartbeat, now)
    client = FakeControlClient()
    healthy = SafetySentinel(
        bundle=bundle,
        heartbeat_path=heartbeat,
        client=client,
        metrics=SentinelMetrics(CollectorRegistry()),
        operational_log=audit,
        clock_ns=lambda: now,
    )
    with pytest.raises(ValueError, match="group/world writable"):
        healthy.step()
    assert client.deadlines == [30_000]


def test_disabled_sentinel_is_rejected(config_dir: Path, tmp_path: Path) -> None:
    bundle = load_config(config_dir, "testnet", environ={})
    with pytest.raises(ValueError, match="disabled"):
        SafetySentinel(
            bundle=bundle,
            heartbeat_path=(tmp_path / "heartbeat").resolve(),
            client=FakeControlClient(),
            metrics=SentinelMetrics(CollectorRegistry()),
        )


def test_control_client_exposes_no_normal_order_method() -> None:
    assert not hasattr(HyperliquidControlClient, "order")
    assert not hasattr(HyperliquidControlClient, "modify_order")


def test_control_client_validates_open_orders_and_exchange_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    info = Mock()
    info.open_orders.return_value = [{"coin": "BTC", "oid": 7}]
    exchange = Mock()
    exchange.schedule_cancel.return_value = {"status": "ok"}
    exchange.bulk_cancel.return_value = {"status": "ok"}
    monkeypatch.setattr("aiquanttrader_native.sentinel.service.Account.from_key", Mock())
    monkeypatch.setattr("aiquanttrader_native.sentinel.service.Info", Mock(return_value=info))
    monkeypatch.setattr(
        "aiquanttrader_native.sentinel.service.Exchange", Mock(return_value=exchange)
    )
    client = HyperliquidControlClient(
        private_key=PrivateKey("1" * 64),
        base_url="https://api.hyperliquid-testnet.xyz/",
        account_address=ACCOUNT,
        timeout_seconds=10,
        vault_address=VAULT,
    )
    client.schedule_cancel(12345)
    assert client.cancel_all() == 1
    info.open_orders.assert_called_with(VAULT)
    exchange.bulk_cancel.assert_called_once_with([{"coin": "BTC", "oid": 7}])

    info.open_orders.return_value = []
    assert client.cancel_all() == 0
    info.open_orders.return_value = {"unexpected": True}
    with pytest.raises(RuntimeError, match="unexpected"):
        client.cancel_all()
    info.open_orders.return_value = ["bad"]
    with pytest.raises(RuntimeError, match="non-object"):
        client.cancel_all()
    info.open_orders.return_value = [{"coin": "BTC"}]
    with pytest.raises(RuntimeError, match="missing"):
        client.cancel_all()
    exchange.schedule_cancel.return_value = {"status": "err"}
    with pytest.raises(RuntimeError, match="scheduleCancel"):
        client.schedule_cancel(None)
