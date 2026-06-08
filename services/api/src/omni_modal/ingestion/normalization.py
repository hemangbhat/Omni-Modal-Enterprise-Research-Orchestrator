from __future__ import annotations

import re
import unicodedata


_WHITESPACE = re.compile(r"[ \t\f\v]+")
_LINE_BREAKS = re.compile(r"\n{3,}")
_BROKEN_HYPHEN = re.compile(r"(\w)-\n(\w)")


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _BROKEN_HYPHEN.sub(r"\1\2", normalized)
    normalized = _WHITESPACE.sub(" ", normalized)
    normalized = "\n".join(line.strip() for line in normalized.split("\n"))
    normalized = _LINE_BREAKS.sub("\n\n", normalized)
    return normalized.strip()
