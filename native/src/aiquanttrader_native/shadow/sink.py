"""Counterfactual-only command boundary with no exchange transport surface."""

from __future__ import annotations

import hashlib

from aiquanttrader_native.paper.engine import PaperEngineCycle
from aiquanttrader_native.paper.models import PaperCommandKind


class ShadowCommandSink:
    """Validate that every approved submit terminates in a recorded-only command."""

    capability = "counterfactual_only"

    def __init__(self, *, restored_commands: int = 0) -> None:
        if restored_commands < 0:
            raise ValueError("restored shadow command count cannot be negative")
        self.command_count = restored_commands
        self.last_cycle_sha256: str | None = None

    def accept(self, cycle: PaperEngineCycle) -> str:
        approved = sum(record.risk_decision.allowed for record in cycle.decisions)
        submits = sum(command.kind is PaperCommandKind.SUBMIT for command in cycle.commands)
        if approved != submits:
            raise ValueError("shadow command sink detected an incomplete approved-submit capture")
        if any(command.sink != self.capability for command in cycle.commands):
            raise ValueError("shadow command attempted to escape the counterfactual-only sink")
        payload = b"\n".join(command.canonical_bytes() for command in cycle.commands)
        digest = hashlib.sha256(payload).hexdigest()
        self.command_count += len(cycle.commands)
        self.last_cycle_sha256 = digest
        return digest
