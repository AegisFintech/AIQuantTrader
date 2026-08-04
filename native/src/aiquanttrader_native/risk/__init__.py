"""Synchronous risk authority and persistent operator kill switch."""

from aiquanttrader_native.risk.authority import ApprovalError, RiskAuthority
from aiquanttrader_native.risk.kill_switch import KillSwitchRecord, KillSwitchStore

__all__ = ["ApprovalError", "KillSwitchRecord", "KillSwitchStore", "RiskAuthority"]
