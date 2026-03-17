# Copyright (c) 2026 rkwithb (https://github.com/rkwithb)
# Licensed under CC BY-NC 4.0 (Non-Commercial Use Only)
# Disclaimer: Use at your own risk. The author is not responsible for any damages.

"""
core/logger.py

Centralised singleton logger for Plurk-Get-Favorites-Tool-CT.
- Call setup_logger() once at app launch (GUI or CLI).
- All other modules use get_logger() to obtain the shared logger instance.
- Call shutdown_logger() before exiting to flush and close the file cleanly.
- Log files are written to <BASE_DIR>/log/session_YYYYMMDD_HHMMSS.log
- Works in both script mode and PyInstaller frozen binary mode.

Buffering strategy:
  The file is opened in line-buffered mode (buffering=1).
  Every log line is flushed to disk immediately after being written,
  rather than accumulating in an 8KB memory buffer first.
  Trade-off: slightly more disk write operations, but each line is guaranteed
  on disk before the next one is written — critical for crash/kill scenarios
  where a full-buffer flush would never happen.
  Performance impact is negligible since the bottleneck is network I/O, not disk.

Log retention:
  setup_logger() keeps the most recent MAX_SESSION_LOGS session files.
  Older files are deleted at launch before the new session file is created.
  A retention summary is always written to the new session log.
  The cleanup message is returned to the caller for display in the UI or CLI.
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
    Build a structured session header block written at the top of each log file.
    Captures environment snapshot for easy debugging.
    """
    now     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    os_info = f"{platform.system()} {platform.release()}"
    py_ver  = platform.python_version()

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


def _cleanup_old_logs(log_folder: Path, logger: logging.Logger) -> str | None:
    """
    Delete the oldest session log files if the total count exceeds MAX_SESSION_LOGS.
    Called from setup_logger() after the new session file is created.

    Writes a retention summary to the log file:
      - Always: one line with the total count of existing files found.
      - Only if files were deleted: one line listing the deleted filenames.

    Returns a human-readable cleanup message string if files were deleted,
    or None if no deletion was necessary. The caller (GUI or CLI) uses this
    to surface the message to the user.
    """
    session_files = sorted(log_folder.glob("session_*.log"))
    total = len(session_files)

    logger.info(f"Log retention: {total} session file(s) found (max {MAX_SESSION_LOGS})")

    if total <= MAX_SESSION_LOGS:
        return None

    to_delete    = session_files[:total - MAX_SESSION_LOGS]
    deleted_names = []

    for f in to_delete:
        try:
            f.unlink()
            deleted_names.append(f.name)
        except OSError as e:
            logger.warning(f"Log retention: failed to delete {f.name} — {e}")

    if deleted_names:
        names_str = ", ".join(deleted_names)
        logger.info(f"Log retention: deleted {len(deleted_names)} old session file(s): {names_str}")
        return f"[i] Deleted {len(deleted_names)} old session log file(s): {names_str}"

    return None


def setup_logger(mode: str = "GUI") -> tuple[Path, str | None]:
    """
    Initialise the singleton file logger. Should be called exactly once at app launch.

    Args:
        mode: "GUI" or "CLI" — recorded in the session header for context.

    Returns:
        (log_path, cleanup_msg) where:
          log_path:    Path to the log file created for this session.
          cleanup_msg: Human-readable string if old log files were deleted,
                       or None if no cleanup was necessary. The caller should
                       display this to the user (UI log window / CLI print).

    Behaviour:
        - Creates <BASE_DIR>/log/ if it does not exist.
        - Names the file session_YYYYMMDD_HHMMSS.log.
        - Opens the file in line-buffered mode (buffering=1) so every line
          is written to disk immediately — safe against crashes and force-kills.
        - Writes a session header block as the first entry.
        - Runs log retention cleanup (keeps MAX_SESSION_LOGS most recent files).
        - Subsequent calls are no-ops (returns the existing log path and None).
    """
    global _initialized

    logger = logging.getLogger(_LOGGER_NAME)

    # Guard: do not re-initialise if already set up
    if _initialized:
        return _get_existing_log_path(logger), None

    logger.setLevel(logging.DEBUG)

    # Log folder sits under BASE_DIR — same location regardless of script/frozen mode
    log_folder = BASE_DIR / "log"
    log_folder.mkdir(parents=True, exist_ok=True)

    # Timestamped session filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path  = log_folder / f"session_{timestamp}.log"

    # Open file in line-buffered mode (buffering=1):
    # Each log line is flushed to disk immediately after writing.
    # Default FileHandler would buffer ~8KB in memory before flushing —
    # meaning the last N lines before a crash or force-kill could be lost.
    log_file     = open(log_path, "a", encoding="utf-8", buffering=1)
    file_handler = logging.StreamHandler(log_file)
    file_handler.setLevel(logging.DEBUG)

    # Store references for clean shutdown later
    file_handler._log_file = log_file
    file_handler._log_path = str(log_path)

    # Log format: timestamp [LEVEL ] [module] message
    formatter = logging.Formatter(
        fmt     = "%(asctime)s [%(levelname)-5s] [%(module)s] %(message)s",
        datefmt = "%Y-%m-%d %H:%M:%S",
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
    Flush and close the log file cleanly before the app exits.
    Should be called from on_closing() or any exit path.

    Args:
        reason: short label recorded as the final log line.

    Reason labels:
        "user_closed"  — normal window close, no active run
        "interrupted"  — window closed during an active backup run
        "exception"    — unhandled exception triggered shutdown
        "normal"       — clean CLI exit
        "language_change" — app restarting due to language switch

    Note: after shutdown_logger() is called, further log calls will be silent
    since all handlers are removed. This is intentional — it is the last thing
    called before the process exits.
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
    All modules should call this instead of logging.getLogger() directly
    so the logger name stays consistent across the codebase.

    Note: setup_logger() must be called before this is useful.
    If called before setup, returns the logger with no handlers (silent).
    """
    return logging.getLogger(_LOGGER_NAME)


def _get_existing_log_path(logger: logging.Logger) -> Path:
    """
    Retrieve the log file path from an already-initialised logger's StreamHandler.
    Returns a fallback Path if no handler with a stored path is found.
    """
    for handler in logger.handlers:
        if hasattr(handler, "_log_path"):
            return Path(handler._log_path)
    return Path("log/unknown.log")
