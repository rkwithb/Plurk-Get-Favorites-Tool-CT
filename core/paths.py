# Copyright (c) 2026 rkwithb (https://github.com/rkwithb)
# Licensed under CC BY-NC 4.0 (Non-Commercial Use Only)
# Disclaimer: Use at your own risk. The author is not responsible for any damages.

"""
core/paths.py

Centralised path resolution for Plurk-Get-Favorites-Tool-CT.

All runtime paths are derived from BASE_DIR, which resolves correctly in both
normal script execution and PyInstaller frozen binary mode.

Import pattern — all other modules use:
    from core.paths import BASE_DIR, DB_PATH, BACKUP_DIR, ...

Never redefine these paths in main.py, gui.py, or any other module.
"""

import sys
from pathlib import Path


def _resolve_base_dir() -> Path:
    """
    Resolve the folder containing the running program.
    - Frozen binary (PyInstaller): directory of the .exe / binary
    - Script mode: project root (two levels up from this file)
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


# ── Root ────────────────────────────────────────────────────────────────────

# Program root — all other paths are derived from this
BASE_DIR: Path = _resolve_base_dir()

# ── Viewer files ─────────────────────────────────────────────────────────────

# Archive browser entry point — opened by [Open index.html] or served by Flask
INDEX_PATH: Path = BASE_DIR / "index.html"

# Archive browser stylesheet — checked alongside INDEX_PATH on launch
STYLE_PATH: Path = BASE_DIR / "style.css"

# ── Backup data ──────────────────────────────────────────────────────────────

# Root folder for all generated backup files
BACKUP_DIR: Path = BASE_DIR / "backup_js"

# SQLite database — source of truth for all fetched favorites
DB_PATH: Path = BACKUP_DIR / "plurk_favorites.db"


# ── Config and credentials ───────────────────────────────────────────────────

# API credentials — gitignored, created on first run
ENV_PATH: Path = BASE_DIR / "tool.env"

# Persisted user preferences (language, etc.)
CONFIG_PATH: Path = BASE_DIR / "config.json"

# ── Locale ───────────────────────────────────────────────────────────────────

# Locale files folder — shipped next to the binary, not bundled inside.
# Resolves correctly in both script mode and frozen mode via BASE_DIR.
# Note: "locales" (plural) consistent with image tool convention.
LOCALES_DIR: Path = BASE_DIR / "locales"


# ── Helpers ──────────────────────────────────────────────────────────────────

def ensure_backup_dir() -> None:
    """
    Create BACKUP_DIR if it does not exist.
    Called once at startup from main.py and gui.py before any DB or export operations.
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def check_viewer_files() -> list[str]:
    """
    Check whether index.html and style.css exist in BASE_DIR.
    Returns a list of missing filenames (empty list if both present).

    Called at GUI launch — result shown as [!!] warnings in the log area
    if either file is missing.

    Example:
        missing = check_viewer_files()
        if missing:
            for name in missing:
                on_log(f"[!!] Missing viewer file: {name}")
    """
    missing = []
    if not INDEX_PATH.exists():
        missing.append(INDEX_PATH.name)
    if not STYLE_PATH.exists():
        missing.append(STYLE_PATH.name)
    return missing