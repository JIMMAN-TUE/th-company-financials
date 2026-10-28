from __future__ import annotations

import re

_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
_EMPTY = {"", "-", "–", "—", "n/a", "na", "null", "none"}


def parse_number(text: object) -> float | None:
    """Parse a statement cell: '1,234.50', '(1,234)' (negative), '-' (empty), Thai digits."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text).strip().translate(_THAI_DIGITS)
    if s.lower() in _EMPTY:
        return None
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1]
    s = s.replace(",", "").replace(" ", "").replace("฿", "").replace("บาท", "")
    if s.startswith("-"):
        negative, s = True, s[1:]
    if not re.fullmatch(r"\d+(\.\d+)?", s):
        return None
    value = float(s)
    return -value if negative else value
