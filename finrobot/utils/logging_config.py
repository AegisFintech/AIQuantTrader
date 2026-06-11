#!/usr/bin/env python3
"""
Consolidated Logging Configuration for FinRobot

All logging goes to a single file under the repo's `logs/` directory
(by default `logs/finrobot.log`), regardless of the absolute path the
repo lives at.

Usage:
    from finrobot.utils.logging_config import setup_logging, get_logger

    setup_logging()
    logger = get_logger("my_module")
    logger.info("Message here")
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Repo-relative log file path. Resolve to absolute so callers can pass the
# string straight to RotatingFileHandler.
_REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = Path(os.getenv("FINROBOT_LOG_DIR", _REPO_ROOT / "logs"))
LOG_FILE = LOG_DIR / "finrobot.log"
MAX_BYTES = 50 * 1024 * 1024  # 50MB
BACKUP_COUNT = 3

# Track if logging has been set up
_logging_configured = False


def setup_logging(level=logging.INFO):
    """Setup consolidated logging to single file."""
    global _logging_configured

    if _logging_configured:
        return

    # Ensure log directory exists
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Formatter
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Rotating file handler
    file_handler = RotatingFileHandler(
        str(LOG_FILE),
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT
    )
    file_handler.setFormatter(formatter)

    # Console handler (optional, for debugging)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    # Root logger configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # Suppress noisy third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)

    _logging_configured = True

    # Log that logging is set up
    logger = logging.getLogger("logging_config")
    logger.info(f"Logging initialized - all output to: {LOG_FILE}")


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the given name."""
    if not _logging_configured:
        setup_logging()
    return logging.getLogger(name)


def cleanup_old_logs():
    """Remove old log files that are no longer used."""
    repo_root = _REPO_ROOT
    old_logs = [
        repo_root / "trading_daemon.log",
        repo_root / "feedback_iterations.log",
        repo_root / "emergency_opencode.log",
        repo_root / "opencode_feedback.log",
        repo_root / "backtest_logs" / "backtest_engine.log",
        repo_root / "backtest_logs" / "console.log",
        repo_root / "multi_strategy.log",
    ]

    removed = []
    for log_file in old_logs:
        if log_file.exists():
            try:
                log_file.unlink()
                removed.append(str(log_file))
            except Exception as e:
                print(f"Failed to remove {log_file}: {e}")

    if removed:
        print(f"Cleaned up {len(removed)} old log files")

    return removed


if __name__ == "__main__":
    setup_logging()
    logger = get_logger("test")
    logger.info("Test message")
    print(f"Log file: {LOG_FILE}")
