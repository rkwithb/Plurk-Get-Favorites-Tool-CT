# Copyright (c) 2026 rkwithb
# Licensed under Apache License 2.0 (Non-Commercial Use Only)
# Disclaimer: Use at your own risk.

"""
core/logger.py

Centralised singleton logger for Plurk-Get-Favorites-Tool-CT.
- Call setup_logger() once at app launch (GUI or CLI).
- All modules use get_logger() for the shared logger instance.
- Call shutdown_logger() before exiting to flush log file.
- Log files written to <BASE_DIR>/log/session_YYYYMMDD_HHMMSS.log
- Works in script mode and PyInstaller frozen binary mode.

Buffering strategy:
  File opened in line-buffered mode (buffering=1).
  Log lines flushed to disk immediately, not buffered.
  Trade-off: more disk writes, but every line guaranteed on disk.
  Critical for crash/kill scenarios where buffered data is lost.
  Performance impact negligible (bottleneck is network I/O).

Log retention:
  setup_logger() keeps recent MAX_SESSION_LOGS session files.
  Oldest files deleted at launch before new session file created.
  Retention summary written to the new session log.
  Cleanup message returned to caller for UI or CLI display.
"""

import logging
import platform
from datetime import datetime
from pathlib import Path

from core.paths import BASE_DIR

# Shared logger name used across all modules
_LOGGER_NAME = "plurk_fav"

# Tracks whether setup_logger() has already been called
_initialized = False

# Maximum number of session log files to keep on disk.
# When exceeded, the oldest files are deleted at the next launch.
MAX_SESSION_LOGS = 20


def _build_session_header(log_path: Path, mode: str) -> str:
    """
    Build session header block written at top of each log file.
    Captures environment snapshot for easy debugging.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    os_info = f"{platform.system()} {platform.release()}"
    py_ver = platform.python_version()

    lines = [
        "=" * 56,
        "  Plurk Favorites — Session Start",
        f"  Time    : {now}",
        f"  OS      : {os_info}",
        f"  Python  : {py_ver}",
        f"  Mode    : {mode}",
        f"  Log     : {log_path}",
        "=" * 56,
    ]
    return "\n".join(lines)


def _cleanup_old_logs(log_folder: Path,
                      logger: logging.Logger) -> str | None:
    """
    Delete oldest session logs if count exceeds MAX_SESSION_LOGS.
    Called from setup_logger() after new session file created.

    Writes retention summary to the log file:
      - Total count of existing files found.
      - List of deleted filenames if any deleted.

    Returns cleanup message string if files deleted,
    or None if no deletion necessary.
    """
    session_files = sorted(log_folder.glob("session_*.log"))
    total = len(session_files)

    logger.info(
        f"Log retention: {total} session file(s) found "
        f"(max {MAX_SESSION_LOGS})"
    )

    if total <= MAX_SESSION_LOGS:
        return None

    to_delete = session_files[:total - MAX_SESSION_LOGS]
    deleted_names = []

    for f in to_delete:
        try:
            f.unlink()
            deleted_names.append(f.name)
        except OSError as e:
            logger.warning(f"Log retention: failed to delete {f.name} — {e}")

    if deleted_names:
        names_str = ", ".join(deleted_names)
        logger.info(
            f"Log retention: deleted {len(deleted_names)} files: {names_str}"
        )
        return (
            f"[i] Deleted {len(deleted_names)} old session log "
            f"file(s): {names_str}"
        )

    return None


def setup_logger(mode: str = "GUI") -> tuple[Path, str | None]:
    """
    Initialise singleton file logger (call once at app launch).

    Args:
        mode: "GUI" or "CLI" — recorded in session header.

    Returns:
        (log_path, cleanup_msg) where:
          log_path: Path to log file created for this session.
          cleanup_msg: Message if old logs deleted, else None.

    Behaviour:
        - Creates <BASE_DIR>/log/ if not exists.
        - Names file session_YYYYMMDD_HHMMSS.log.
        - Opens file line-buffered so every line written immediately.
        - Writes session header block as first entry.
        - Runs log retention cleanup (keeps MAX_SESSION_LOGS files).
        - Subsequent calls are no-ops (returns path and None).
    """
    global _initialized

    logger = logging.getLogger(_LOGGER_NAME)

    # Guard: do not re-initialise if already set up
    if _initialized:
        return _get_existing_log_path(logger), None

    logger.setLevel(logging.DEBUG)

    # Log folder sits under BASE_DIR
    # Location same regardless of script/frozen mode
    log_folder = BASE_DIR / "log"
    log_folder.mkdir(parents=True, exist_ok=True)

    # Timestamped session filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_folder / f"session_{timestamp}.log"

    # Open file in line-buffered mode (buffering=1):
    # Each log line is flushed to disk immediately after writing.
    # Default FileHandler would buffer ~8KB in memory before flushing —
    # meaning the last N lines before a crash or force-kill could be lost.
    log_file = open(log_path, "a", encoding="utf-8", buffering=1)
    file_handler = logging.StreamHandler(log_file)
    file_handler.setLevel(logging.DEBUG)

    # Store references for clean shutdown later
    file_handler._log_file = log_file
    file_handler._log_path = str(log_path)

    # Log format: timestamp [LEVEL ] [module] message
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-5s] [%(module)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Write session header as first log entry
    header = _build_session_header(log_path, mode)
    logger.info("\n" + header)

    # Run log retention cleanup and capture any deletion message for the caller
    cleanup_msg = _cleanup_old_logs(log_folder, logger)

    _initialized = True
    return log_path, cleanup_msg


def shutdown_logger(reason: str = "normal") -> None:
    """
    Flush and close log file cleanly before app exits.
    Call from on_closing() or any exit path.

    Args:
        reason: short label recorded as final log line.

    Reason labels:
        "user_closed" — normal window close
        "interrupted" — window closed during backup run
        "exception" — unhandled exception triggered shutdown
        "normal" — clean CLI exit
        "language_change" — app restart due to language switch

    Note: after shutdown_logger(), further log calls are silent.
    """
    logger = logging.getLogger(_LOGGER_NAME)
    logger.info(f"--- Session ended ({reason}) ---")

    # Flush and close all handlers, then remove them from the logger
    for handler in logger.handlers[:]:
        try:
            handler.flush()
            if hasattr(handler, "_log_file"):
                handler._log_file.close()
            handler.close()
        except Exception:
            pass
        logger.removeHandler(handler)


def get_logger() -> logging.Logger:
    """
    Return the shared logger instance.
    Call instead of logging.getLogger() for consistency.

    Note: setup_logger() must be called first.
    If called before setup, returns logger with no handlers (silent).
    """
    return logging.getLogger(_LOGGER_NAME)


def _get_existing_log_path(logger: logging.Logger) -> Path:
    """
    Retrieve log file path from already-initialised logger.
    Returns fallback Path if no handler with stored path found.
    """
    for handler in logger.handlers:
        if hasattr(handler, "_log_path"):
            return Path(handler._log_path)
    return Path("log/unknown.log")
