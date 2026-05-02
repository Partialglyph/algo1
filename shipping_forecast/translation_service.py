from __future__ import annotations

"""
translation_service.py

Translation helper using DeepL API Free as the primary path.
Fallback chain:
  1. Title is already English -> return as-is.
  2. DeepL API Free available  -> translate via official deepl Python client.
  3. ASCII strip fallback      -> strip non-ASCII, append hint.

DeepL API Free limit: 500,000 chars/month.
The client library auto-detects Free vs Pro keys based on the ':fx' suffix.
"""

import asyncio
import logging
from typing import Optional

from . import settings

log = logging.getLogger(__name__)

_NON_ASCII_THRESHOLD = 0.15


def _looks_english(text: str) -> bool:
    if not text:
        return True
    non_ascii = sum(1 for c in text if ord(c) > 127)
    return (non_ascii / len(text)) < _NON_ASCII_THRESHOLD


def _ascii_fallback(title: str) -> str:
    cleaned = "".join(c if ord(c) < 128 else "" for c in title).strip()
    if not cleaned:
        return "[Title in non-English script — see original URL]"
    return cleaned + " [translated]"


async def _deepl_translate(text: str, target_lang: str = "EN") -> Optional[str]:
    """
    Translate text via official DeepL Python client.
    Runs synchronous client in a thread pool to keep FastAPI non-blocking.
    Returns translated string, or None on any failure.
    """
    key = settings.DEEPL_API_KEY
    if not key:
        return None
    try:
        import deepl as deepl_lib  # official 'deepl' package
        loop = asyncio.get_event_loop()

        def _sync_translate() -> str:
            translator = deepl_lib.Translator(key)
            result = translator.translate_text(text, target_lang=target_lang)
            if isinstance(result, list):
                return result[0].text if result else ""
            return result.text

        translated = await loop.run_in_executor(None, _sync_translate)
        if translated and translated.strip().lower() != text.strip().lower():
            return translated.strip()
        return None
    except Exception as exc:
        log.warning("DeepL translation failed: %s", exc)
        return None


async def ensure_english_title(title: str) -> tuple[str, str]:
    """
    Return (title_english, language_tag).
    language_tag is 'en' if already English, 'non-en' otherwise.
    """
    if not title:
        return title, "en"

    if _looks_english(title):
        return title, "en"

    translated = await _deepl_translate(title, target_lang="EN")
    if translated:
        return translated, "non-en"

    return _ascii_fallback(title), "non-en"
