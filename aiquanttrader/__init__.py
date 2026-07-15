"""
AIQuantTrader - Self-Improving Autonomous Algorithmic Trading System
"""

__version__ = "2.0.0"
__author__ = "AIQuantTrader Team"

# Import main components for easy access
from aiquanttrader.utils.config import settings
from aiquanttrader.utils.logging_config import setup_logging, get_logger

__all__ = [
    "settings",
    "setup_logging",
    "get_logger",
]
