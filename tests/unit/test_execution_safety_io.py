from __future__ import annotations

from pathlib import Path

import pytest

from aiquanttrader.execution.heartbeat import (
    HeartbeatPublisher,
    read_heartbeat,
)
from aiquanttrader.execution.secrets import PrivateKey, read_private_key
from aiquanttrader.risk import KillSwitchStore

ACCOUNT = "0x" + "1" * 40
FINGERPRINT = "a" * 64


def test_private_key_is_validated_and_never_rendered(tmp_path: Path) -> None:
    path = tmp_path / "key"
    path.write_text("1" * 64 + "\n", encoding="ascii")
    key = read_private_key(path)
    assert key.reveal() == "0x" + "1" * 64
    assert "1" * 8 not in repr(key)
    assert "redacted" in str(key)
    assert PrivateKey("0x" + "2" * 64).reveal().startswith("0x")

    path.write_text("not-a-key", encoding="ascii")
    with pytest.raises(ValueError, match="32-byte"):
        read_private_key(path)
    path.write_bytes(b"\xff")
    with pytest.raises(ValueError, match="ASCII"):
        read_private_key(path)
    path.write_bytes(b"1" * 257)
    with pytest.raises(ValueError, match="large"):
        read_private_key(path)


def test_private_key_rejects_directory_and_final_symlink(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="regular"):
        read_private_key(tmp_path)
    target = tmp_path / "target"
    target.write_text("1" * 64, encoding="ascii")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(OSError):
        read_private_key(link)


def test_kill_switch_is_persistent_audited_and_fail_closed(tmp_path: Path) -> None:
    now = 123
    path = (tmp_path / "state" / "operator-kill.json").resolve()
    store = KillSwitchStore(path, clock_ns=lambda: now)
    assert not store.read().active
    active = store.activate(actor="operator", reason="risk drill")
    assert active.active and store.read() == active
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    cleared = store.clear(actor="operator", reason="drill complete")
    assert not cleared.active
    audit = path.with_suffix(".json.audit.jsonl").read_text(encoding="utf-8")
    assert active.record_id in audit and cleared.record_id in audit

    path.write_text("{broken", encoding="utf-8")
    corrupt = store.read()
    assert corrupt.active
    assert "unreadable" in corrupt.reason

    with pytest.raises(ValueError, match="absolute"):
        KillSwitchStore(Path("relative"))


def test_heartbeat_publishes_health_and_operator_kill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kill = KillSwitchStore((tmp_path / "kill.json").resolve(), clock_ns=lambda: 10)
    path = (tmp_path / "heartbeat.json").resolve()
    publisher = HeartbeatPublisher(
        path,
        environment="testnet",
        account_address=ACCOUNT,
        config_fingerprint=FINGERPRINT,
        kill_switch=kill,
    )
    publisher.set_health(execution_healthy=True, reconciliation_complete=True)
    heartbeat = publisher.publish(now_ns=100)
    assert heartbeat.execution_healthy
    assert read_heartbeat(path) == heartbeat
    assert oct(path.stat().st_mode & 0o777) == "0o600"

    monkeypatch.setattr("aiquanttrader.execution.heartbeat.time.time_ns", lambda: 100)
    publisher.set_health(
        execution_healthy=True,
        reconciliation_complete=True,
        valid_for_ms=5,
    )
    assert not publisher.publish(now_ns=5_000_101).execution_healthy

    kill.activate(actor="operator", reason="halt")
    killed = publisher.publish(now_ns=200)
    assert killed.operator_kill
    assert not killed.execution_healthy

    with pytest.raises(ValueError, match="absolute"):
        HeartbeatPublisher(
            Path("relative"),
            environment="testnet",
            account_address=ACCOUNT,
            config_fingerprint=FINGERPRINT,
            kill_switch=kill,
        )
