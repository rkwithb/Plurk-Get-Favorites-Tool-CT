# Copyright (c) 2026 rkwithb (https://github.com/rkwithb)
# Licensed under CC BY-NC 4.0 (Non-Commercial Use Only)
# Disclaimer: Use at your own risk. The author is not responsible for any damages.

"""
core/i18n.py

Lightweight i18n module for plurk-fav.
- Call load_language() with a language code to load translations.
- All modules use t(key, **kwargs) to get translated strings.
- Falls back to the key itself if a translation is missing (visible but non-crashing).
- Locale files are flat JSON stored in locales/ next to the binary (not bundled inside).
  LOCALES_DIR from paths.py resolves correctly in both script mode and frozen mode.

Config persistence (language code, port) is handled by core/config.py.
This module only reads the locale files — it never touches config.json.
"""

import json

from core.logger import get_logger
from core.paths import LOCALES_DIR

logger = get_logger()

# Currently loaded translations dict
_translations: dict = {}

# Currently active language code
_current_language: str = "zh_TW"

# Supported language codes mapped to display labels for the UI dropdown
SUPPORTED_LANGUAGES: dict[str, str] = {
    "zh_TW": "繁體中文",
    "en":    "English",
}


def load_language(lang: str) -> None:
    """
    Load translations for the given language code from its JSON file.
    Falls back to zh_TW if the requested locale file is not found.

    Args:
        lang: language code, e.g. "zh_TW" or "en"
    """
    global _translations, _current_language

    locales_folder = LOCALES_DIR
    locale_file = locales_folder / f"{lang}.json"

    if not locale_file.exists():
        logger.warning(f"i18n: locale file not found for '{lang}' — falling back to zh_TW")
        lang = "zh_TW"
        locale_file = locales_folder / "zh_TW.json"

    try:
        with open(locale_file, "r", encoding="utf-8") as f:
            _translations = json.load(f)
            _current_language = lang
            logger.debug(f"i18n: loaded '{lang}' ({len(_translations)} keys)")

    except Exception as e:
        logger.error(f"i18n: failed to load locale file '{locale_file}' — {type(e).__name__}: {e}")
        _translations = {}
        _current_language = lang


def t(key: str, **kwargs) -> str:
    """
    Return the translated string for key, with optional placeholder substitution.
    Falls back to the key itself if not found — missing translations are visible
    in the UI but will never raise an exception.

    Args:
        key:    translation key, e.g. "btn_start_backup"
        kwargs: placeholder values, e.g. t("log_backup_fetching", count=10)

    Returns:
        Translated and formatted string.
    """
    text = _translations.get(key, key)

    if kwargs:
        try:
            text = text.format(**kwargs)
        except KeyError as e:
            logger.warning(f"i18n: missing placeholder {e} in key '{key}'")

    return text


def get_language() -> str:
    """Return the currently active language code."""
    return _current_language
