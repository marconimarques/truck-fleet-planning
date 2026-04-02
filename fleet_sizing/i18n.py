"""Lightweight i18n module — JSON locale files + t() translation function.

Usage:
    from .i18n import t, set_language, load_saved_language, current_language, AVAILABLE_LANGS

    set_language("pt_BR")          # switch at runtime
    t("cli.welcome.body")          # look up a key, falls back to the key itself
    t("cli.summary.demand", total=12345.0)  # with format kwargs
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_LOCALES_DIR = Path(__file__).parent.parent / "locales"
_CONFIG_PATH = Path(__file__).parent.parent / "outputs" / "config.json"

_strings: dict[str, str] = {}
_lang: str = "en"

AVAILABLE_LANGS: dict[str, str] = {
    "en":    "English",
    "pt_BR": "Português (Brasil)",
}


def set_language(lang: str) -> None:
    """Load the locale file for *lang* and persist the choice to outputs/config.json."""
    global _strings, _lang
    _lang = lang
    path = _LOCALES_DIR / f"{lang}.json"
    fallback = _LOCALES_DIR / "en.json"
    target = path if path.exists() else fallback
    with open(target, encoding="utf-8") as f:
        _strings = json.load(f)

    # Persist choice so the next session starts in the same language
    config: dict = {}
    if _CONFIG_PATH.exists():
        try:
            with open(_CONFIG_PATH, encoding="utf-8") as f:
                config = json.load(f)
        except Exception:
            pass
    config["lang"] = lang
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def load_saved_language(default: str = "en") -> str:
    """Return the persisted language (or FLEET_LANG env var, or *default*)."""
    if _CONFIG_PATH.exists():
        try:
            with open(_CONFIG_PATH, encoding="utf-8") as f:
                saved = json.load(f).get("lang", "")
            if saved in AVAILABLE_LANGS:
                return saved
        except Exception:
            pass
    env = os.environ.get("FLEET_LANG", "")
    return env if env in AVAILABLE_LANGS else default


def current_language() -> str:
    """Return the active language code (e.g. 'en', 'pt_BR')."""
    return _lang


def t(key: str, **kwargs) -> str:
    """Return the translated string for *key*.

    Falls back to the key itself if not found (safe for partial migrations).
    Supports Python format-string kwargs: t("cli.summary.demand", total=12345.0)
    """
    text = _strings.get(key, key)
    return text.format(**kwargs) if kwargs else text
