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
3. Entry point dispatch to ui/app.py (GUI) or CLI handler (future)

Usage:
    python main.py              # Launch GUI (default)
    python main.py 202604       # CLI mode: backup from 2026-04 (not yet implemented)
    python main.py --version    # Print version and exit

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


def _detect_mode() -> str:
    """
    Detect execution mode from command-line arguments.
    
    Returns 'cli' if first arg is YYYYMM format (after --version check).
    Otherwise returns 'gui' (default).
    """
    # --version is handled by ui/app.py, so don't check here
    if len(sys.argv) > 1:
        first_arg = sys.argv[1]
        if _is_valid_yyyymm(first_arg):
            return 'cli'
    return 'gui'


def main_gui():
    """Launch GUI mode — delegates to ui/app.py:main()."""
    try:
        from ui.app import main as gui_main
        gui_main()
    except ImportError as e:
        print(f"Error: Failed to import ui.app module — {e}", file=sys.stderr)
        print("Make sure you are running this script from the project root directory.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: GUI application failed — {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


def main_cli(date_str: str):
    """
    Launch CLI mode — date input YYYYMM format.
    
    Not yet implemented — placeholder for future CLI backup functionality.
    """
    print(f"CLI mode selected: {date_str}", file=sys.stderr)
    print("CLI functionality is not yet implemented.", file=sys.stderr)
    print("Use 'python main.py' to launch the GUI.", file=sys.stderr)
    sys.exit(1)


def main():
    """Unified entry point — detects and dispatches to appropriate mode."""
    mode = _detect_mode()
    
    if mode == 'gui':
        main_gui()
    elif mode == 'cli':
        # Extract the YYYYMM argument
        date_str = sys.argv[1]
        main_cli(date_str)
    else:
        print(f"Error: Unknown mode — {mode}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
