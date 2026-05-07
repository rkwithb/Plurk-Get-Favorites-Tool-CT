# Copyright (c) 2026 rkwithb
# Licensed under Apache License 2.0 (Non-Commercial Use Only)
# Disclaimer: Use at your own risk.

"""
core/cli_utils.py

Testable CLI utility functions — extracted from ui/app.py to enable
independent testing and CLI reuse.

Functions:
    resolve_backup_criteria()     — map CLI mode → (mode, criteria) tuple
    format_yyyymm_error()         — format validation error for user display
    run_cli_backup()              — orchestrate backup with console callbacks
    run_export_only()             — re-export DB to JS without API call
"""

from datetime import datetime
from typing import Callable, Tuple

from plurk_oauth import PlurkAPI
import sqlite3

from core.backup import run_backup_task
from core.export import reexport_from_db
from core.logger import get_logger

logger = get_logger()


# ============================================================================
# Public API — Testable Functions
# ============================================================================

def resolve_backup_criteria(mode: str, yyyymm: str) -> Tuple[str, any]:
    """
    Map CLI mode + YYYYMM argument to (resolved_mode, criteria).

    Modes:
        'incremental' — backup plurks after the last saved id
        'date'        — backup plurks on/after YYYYMM
        'full'        — backup all plurks (ignores YYYYMM)
        'export-only' — re-export DB to JS

    Args:
        mode:   'incremental', 'date', 'full', or 'export-only'
        yyyymm: date string in YYYYMM format (e.g., '202604')

    Returns:
        Tuple (resolved_mode, criteria) where:
            - resolved_mode: one of 'incremental', 'date', 'full'
            - criteria: int or datetime depending on mode
                - 'incremental': last plurk_id (int, from DB)
                - 'date': datetime(year, month, 1, 0, 0)
                - 'full': 0 (unused by backup task)
            Special: 'export-only' returns ('export-only', None)

    Raises:
        ValueError: if mode or YYYYMM parsing fails

    Examples:
        - resolve_backup_criteria('incremental', '202604')
        - resolve_backup_criteria('date', '202604')
        - resolve_backup_criteria('full', '202604')
        - resolve_backup_criteria('export-only', '202604')
    """
    if mode == 'incremental':
        # Incremental mode: criteria is 0 (last_plurk_id fetched from DB)
        return ('incremental', 0)

    elif mode == 'date':
        # Date mode: parse YYYYMM → datetime(year, month, 1)
        try:
            year = int(yyyymm[:4])
            month = int(yyyymm[4:6])
            criteria = datetime(year, month, 1, 0, 0)
            return ('date', criteria)
        except (ValueError, IndexError) as e:
            raise ValueError(f"Invalid YYYYMM format '{yyyymm}': {e}")

    elif mode == 'full':
        # Full mode: criteria is 0
        return ('full', 0)

    elif mode == 'export-only':
        # Export-only mode: doesn't use backup_task; recycle pattern anyway
        return ('export-only', None)

    else:
        raise ValueError(
            f"Invalid mode '{mode}': must be incremental, date, "
            f"full, or export-only"
        )


def format_yyyymm_error(yyyymm: str) -> str:
    """Format a helpful error message for invalid YYYYMM input."""
    return (
        f"Invalid date format '{yyyymm}': expected YYYYMM (e.g., 202604). "
        f"Year >= 2000, month 01-12."
    )


def run_cli_backup(
    client: PlurkAPI,
    conn: sqlite3.Connection,
    mode: str,
    criteria: any,
    backup_dir: str,
    console_log: Callable[[str], None],
    console_progress: Callable[[int, int], None],
) -> bool:  # noqa: E501
    """
    Orchestrate backup with console logging.

    Args:
        client:           authorised PlurkAPI instance
        conn:             open database connection
        mode:             'incremental', 'date', or 'full'
        criteria:         int or datetime (result from resolve_backup_criteria)
        backup_dir:       path to backup_js/ folder
        console_log:      callback taking one str (log line)
        console_progress: callback taking (this_run: int, total: int)

    Returns:
        bool: True on success, False on error
    """
    import threading

    # Create a stop event (dummy in CLI mode; used to
    # satisfy run_backup_task API)
    stop_event = threading.Event()

    try:
        run_backup_task(
            client=client,
            conn=conn,
            mode=mode,
            criteria=criteria,
            backup_dir=backup_dir,
            stop_event=stop_event,
            on_log=console_log,
            on_stats=console_progress,
        )
        return True
    except Exception as e:
        console_log(f"Error: backup failed — {e}")
        logger.exception("cli_backup: backup_task raised exception")
        return False


def run_export_only(
    conn: sqlite3.Connection,
    backup_dir: str,
    console_log: Callable[[str], None],
) -> bool:
    """
    Re-export all months from DB to JS files without any API calls.

    Args:
        conn:        open database connection
        backup_dir:  path to backup_js/ folder
        console_log: callback taking one str (log line)

    Returns:
        bool: True on success, False on error
    """
    try:
        reexport_from_db(conn, backup_dir, console_log)
        return True
    except Exception as e:
        console_log(f"Error: export failed — {e}")
        logger.exception("cli_export_only: reexport_from_db raised exception")
        return False
