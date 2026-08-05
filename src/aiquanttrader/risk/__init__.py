"""Synchronous risk authority and persistent operator kill switch."""

from aiquanttrader.risk.authority import ApprovalError, RiskAuthority
from aiquanttrader.risk.kill_switch import KillSwitchRecord, KillSwitchStore

__all__ = ["ApprovalError", "KillSwitchRecord", "KillSwitchStore", "RiskAuthority"]
