#!/usr/bin/env python3

# Copyright (c) 2026 rkwithb (https://github.com/rkwithb)
# Licensed under Apache License 2.0 (Non-Commercial Use Only)
# Disclaimer: Use at your own risk. The author is not responsible for any damages.

"""
main.py

Unified bootstrap entry point for CustomTkinter GUI and CLI modes.

This module provides:
1. Platform-specific initialization (Windows stdout robustness, frozen binary setup)
2. Mode detection (GUI by default, CLI if YYYYMM date argument provided)
3. Entry point dispatch to ui/app.py (GUI) or CLI handler

Usage:
    python main.py                                    # Launch GUI (default)
    python main.py 202604                            # CLI: date mode, plurks from 2026-04
    python main.py 202604 --mode incremental         # CLI: incremental mode
    python main.py 202604 --mode full                # CLI: full backup
    python main.py 202604 --export-only              # CLI: re-export DB to JS
    python main.py --version                         # Print version and exit

CLI Modes:
    date        — backup plurks posted on/after the given month (default)
    incremental — backup plurks posted after the last saved plurk_id
    full        — backup all plurks regardless of DB state
    export-only — re-export all months from DB to JS without API call

Exit codes:
    0 — Success
    1 — Error (invalid args, import failure, runtime exception)
"""

import sys
import io
import os
import traceback
from pathlib import Path

# ==========================================
# Windows stdout robustness initialization
# Prevents encoding crashes in Windows terminal/console environments
# Critical for frozen binaries where stdout might be None
# ==========================================
if sys.platform == "win32":
    if sys.stdout is not None and hasattr(sys.stdout, 'buffer'):
        try:
            # Force UTF-8 with line buffering to prevent encoding crashes
            sys.stdout = io.TextIOWrapper(
                sys.stdout.buffer,
                encoding='utf-8',
                line_buffering=True
            )
        except Exception:
            pass
    elif sys.stdout is None:
        # Prevent print crashes in --windowed mode or no-console frozen binaries
        sys.stdout = open(os.devnull, 'w')


# ==========================================
# Ensure project root is in sys.path
# ==========================================
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _is_frozen() -> bool:
    """Check if running as a frozen binary (PyInstaller, cx_Freeze, etc)."""
    return getattr(sys, 'frozen', False)


def _is_valid_yyyymm(arg: str) -> bool:
    """Validate if argument is YYYYMM format (e.g., 202604)."""
    if not isinstance(arg, str) or len(arg) != 6:
        return False
    try:
        year = int(arg[:4])
        month = int(arg[4:6])
        # Basic validation: year >= 2000, month 01-12
        return year >= 2000 and 1 <= month <= 12
    except ValueError:
        return False


def _parse_cli_args() -> tuple:
    """
    Parse CLI arguments: YYYYMM [--mode MODE] [--export-only]

    Returns:
        (yyyymm, mode, export_only) tuple, or (None, None, False) if not CLI

    Examples:
        ['prog', '202604']                           → ('202604', 'date', False)
        ['prog', '202604', '--mode', 'full']         → ('202604', 'full', False)
        ['prog', '202604', '--export-only']          → ('202604', 'export-only', False)
        ['prog', '202604', '--mode', 'incremental']  → ('202604', 'incremental', False)
    """
    if len(sys.argv) < 2:
        return (None, None, False)

    first_arg = sys.argv[1]
    if not _is_valid_yyyymm(first_arg):
        return (None, None, False)

    yyyymm = first_arg
    mode = 'date'  # default mode
    export_only = False

    # Parse remaining optional arguments
    i = 2
    while i < len(sys.argv):
        arg = sys.argv[i]

        if arg == '--export-only':
            export_only = True
            i += 1

        elif arg == '--mode':
            if i + 1 < len(sys.argv):
                mode = sys.argv[i + 1]
                i += 2
            else:
                print("Error: --mode requires a value (incremental, date, full)", file=sys.stderr)
                sys.exit(1)

        else:
            print(f"Error: Unknown argument '{arg}'", file=sys.stderr)
            sys.exit(1)

    # Validate mode
    valid_modes = {'incremental', 'date', 'full', 'export-only'}
    if mode not in valid_modes:
        error_msg = f"Error: Invalid mode '{mode}'. "
        error_msg += f"Must be one of: {', '.join(valid_modes)}"
        print(error_msg, file=sys.stderr)
        sys.exit(1)

    # If --export-only is set, force mode to 'export-only'
    if export_only:
        mode = 'export-only'

    return (yyyymm, mode, export_only)


def _detect_mode() -> str:
    """
    Detect execution mode from command-line arguments.

    Returns 'cli' if first arg is YYYYMM format (after --version check).
    Otherwise returns 'gui' (default).
    """
    # --version is handled by ui/app.py, so don't check here
    yyyymm, _, _ = _parse_cli_args()
    return 'cli' if yyyymm else 'gui'


def main_gui():
    """Launch GUI mode — delegates to ui/app.py:main()."""
    try:
        from ui.app import main as gui_main
        gui_main()
    except ImportError as e:
        print(f"Error: Failed to import ui.app module — {e}", file=sys.stderr)
        msg = "Make sure you are running from the project root directory."
        print(msg, file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: GUI application failed — {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


def main_cli(yyyymm: str, mode: str):
    """
    Launch CLI mode — executes backup or export based on mode.

    Modes:
        'incremental' — backup plurks posted after last saved id
        'date'        — backup plurks posted on/after YYYYMM
        'full'        — backup all plurks
        'export-only' — re-export DB to JS without API call

    Args:
        yyyymm: date in format YYYYMM (e.g., '202604')
        mode:   'incremental', 'date', 'full', or 'export-only'
    """
    from datetime import datetime
    import time

    try:
        from core.config import load_config
        from core.auth import get_keys, build_plurk_client
        from core.db import init_db, get_total_count
        from core.paths import BACKUP_DIR, DB_PATH
        from core.cli_utils import (
            resolve_backup_criteria,
            format_yyyymm_error,
            run_cli_backup,
            run_export_only,
        )
    except ImportError as import_err:
        err_msg = f"Error: Failed to import core modules — {import_err}"
        print(err_msg, file=sys.stderr)
        sys.exit(1)

    # ========================================================================
    # Helper closures for console logging
    # ========================================================================

    def console_log(msg: str):
        """Print log message with timestamp to stdout."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {msg}")

    def console_progress(this_run: int, total: int):
        """Print progress update."""
        print(f"  Progress: +{this_run} this run, {total} total", end='\r')

    console_log(f"CLI mode: {mode} with date {yyyymm}")

    # ========================================================================
    # Phase 1: Load configuration and keys
    # ========================================================================

    try:
        cfg = load_config()
        console_log(f"Config loaded: language={cfg.language}, port={cfg.port}")
    except Exception as e:
        console_log(f"Error loading config: {e}")
        sys.exit(1)

    ck, cs, at, ats = get_keys()

    if mode == 'export-only':
        # Export-only doesn't need API keys
        console_log("Export-only mode: skipping API key validation")
    else:
        # Other modes require all 4 keys
        if not all([ck, cs, at, ats]):
            missing = []
            if not ck:
                missing.append("PLURK_CONSUMER_KEY")
            if not cs:
                missing.append("PLURK_CONSUMER_SECRET")
            if not at:
                missing.append("PLURK_ACCESS_TOKEN")
            if not ats:
                missing.append("PLURK_ACCESS_TOKEN_SECRET")
            console_log(f"Error: Missing API keys: {', '.join(missing)}")
            console_log("Please set keys in tool.env and try again.")
            sys.exit(1)
        console_log("API keys loaded and validated")

    # ========================================================================
    # Phase 2: Initialize database
    # ========================================================================

    try:
        conn = init_db(str(DB_PATH), console_log)
        console_log(f"Database ready at {DB_PATH}")
    except Exception as e:
        console_log(f"Error initializing database: {e}")
        sys.exit(1)

    # ========================================================================
    # Phase 3: Handle export-only mode (no API call)
    # ========================================================================

    if mode == 'export-only':
        console_log("Starting export-only re-export...")
        success = run_export_only(conn, str(BACKUP_DIR), console_log)
        conn.close()
        if success:
            console_log("Export complete!")
            sys.exit(0)
        else:
            console_log("Export failed.")
            sys.exit(1)

    # ========================================================================
    # Phase 4: Build PlurkAPI client (for incremental/date/full modes)
    # ========================================================================

    try:
        client = build_plurk_client(ck, cs, at, ats)
        console_log("PlurkAPI client authorized")
    except Exception as e:
        console_log(f"Error building API client: {e}")
        console_log("Check that your API keys are valid.")
        sys.exit(1)

    # ========================================================================
    # Phase 5: Resolve backup mode and criteria
    # ========================================================================

    try:
        resolved_mode, criteria = resolve_backup_criteria(mode, yyyymm)

        if resolved_mode == 'incremental':
            console_log("Mode: incremental — backup new plurks from last saved")
        elif resolved_mode == 'date':
            criteria_str = criteria.strftime("%Y-%m-%d") if criteria else "?"
            console_log(f"Mode: date — backup plurks posted on/after {criteria_str}")
        elif resolved_mode == 'full':
            console_log("Mode: full — backup all plurks (may take a while)")
    except ValueError:
        console_log(f"Error: {format_yyyymm_error(yyyymm)}")
        sys.exit(1)

    # ========================================================================
    # Phase 6: Run backup
    # ========================================================================

    console_log("Starting backup...")
    start_time = time.time()

    success = run_cli_backup(
        client=client,
        conn=conn,
        mode=resolved_mode,
        criteria=criteria,
        backup_dir=str(BACKUP_DIR),
        console_log=console_log,
        console_progress=console_progress,
    )

    elapsed = time.time() - start_time

    if success:
        total = get_total_count(conn)
        console_log(f"\n✓ Backup complete in {elapsed:.1f}s. Total saved: {total}")
        conn.close()
        sys.exit(0)
    else:
        console_log(f"\n✗ Backup failed after {elapsed:.1f}s")
        conn.close()
        sys.exit(1)


def main():
    """Unified entry point — detects and dispatches to appropriate mode."""
    mode = _detect_mode()

    if mode == 'gui':
        main_gui()
    elif mode == 'cli':
        yyyymm, cli_mode, _ = _parse_cli_args()
        main_cli(yyyymm, cli_mode)
    else:
        print(f"Error: Unknown mode — {mode}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
