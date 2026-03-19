# Copyright (c) 2026 rkwithb (https://github.com/rkwithb)
# Licensed under CC BY-NC 4.0 (Non-Commercial Use Only)
# Disclaimer: Use at your own risk. The author is not responsible for any damages.

"""
core/config.py

Single owner of config.json for plurk-fav.
- load_config() : read all persisted settings, return as AppConfig
- save_config() : write all settings back to config.json (read-modify-write)

config.json schema:
    {
        "language": "zh_TW",
        "port":     5123
    }

AppConfig is a simple dataclass — callers unpack what they need:
    cfg = load_config()
    cfg.language   → "zh_TW"
    cfg.port       → 5123

All other modules that need config values import from here.
i18n.py calls load_config() to get the language code at startup —
it no longer owns any file I/O of its own.
"""

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from core.logger import get_logger

logger = get_logger()

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_LANGUAGE: str = "zh_TW"
DEFAULT_PORT:     int = 5123


# ---------------------------------------------------------------------------
# AppConfig dataclass
# ---------------------------------------------------------------------------

@dataclass
class AppConfig:
    language: str = DEFAULT_LANGUAGE
    port:     int = DEFAULT_PORT


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def _resolve_config_path() -> Path:
    """
    Resolve config.json path.
    - Frozen binary: stored next to the binary on disk (user-writable, persistent)
    - Script mode:   stored at the project root
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "config.json"
    else:
        return Path(__file__).resolve().parent.parent / "config.json"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_config() -> AppConfig:
    """
    Read config.json and return an AppConfig with validated values.
    Falls back to defaults for any missing or invalid field.
    Returns a fully default AppConfig if the file is missing or unreadable.

    Returns:
        AppConfig(language, port)
    """
    from core.i18n import SUPPORTED_LANGUAGES

    config_path = _resolve_config_path()

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        # -- language ----------------------------------------------------
        lang = raw.get("language", DEFAULT_LANGUAGE)
        if lang not in SUPPORTED_LANGUAGES:
            logger.warning("config: unknown language '%s' — falling back to %s", lang, DEFAULT_LANGUAGE)
            lang = DEFAULT_LANGUAGE

        # -- port --------------------------------------------------------
        port = raw.get("port", DEFAULT_PORT)
        if not isinstance(port, int) or not (1024 <= port <= 65535):
            logger.warning("config: invalid port '%s' — falling back to %d", port, DEFAULT_PORT)
            port = DEFAULT_PORT

        logger.debug("config: loaded — language='%s' port=%d", lang, port)
        return AppConfig(language=lang, port=port)

    except FileNotFoundError:
        # First launch — config does not exist yet, use defaults silently
        logger.debug("config: config.json not found — using defaults")
        return AppConfig()

    except Exception as e:
        logger.warning("config: failed to read config.json — %s: %s — using defaults", type(e).__name__, e)
        return AppConfig()


def save_config(cfg: AppConfig) -> None:
    """
    Persist all settings to config.json (read-modify-write).
    Preserves any unrecognised keys that may have been added manually.

    Args:
        cfg: AppConfig instance with the values to save
    """
    config_path = _resolve_config_path()

    # Read existing file to preserve unrecognised keys
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
    except Exception:
        existing = {}

    existing["language"] = cfg.language
    existing["port"]     = cfg.port

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        logger.debug("config: saved — language='%s' port=%d", cfg.language, cfg.port)

    except Exception as e:
        logger.error("config: failed to write config.json — %s: %s", type(e).__name__, e)
