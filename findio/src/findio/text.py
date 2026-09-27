"""Decode source files that are not UTF-8."""

from __future__ import annotations

import codecs
import re

_COOKIE = re.compile(rb"coding[:=]\s*([-\w.]+)")
_WIDE_ENCODINGS = ("utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be")


def decode_source(data: bytes) -> str:
    """Decode source bytes to text.

    A byte-order mark and a ``coding:`` cookie in the first two lines are
    honored, then strict UTF-8. UTF-16 and UTF-32 without a mark are recognized
    by their layout. Latin-1 is the last resort, so an undeclared 8-bit file
    still decodes; a cookie is what selects Windows-1252, Shift-JIS, and the
    other encodings Python knows.
    """
    if not data:
        return ""
    for encoding in _declared_encodings(data):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    # UTF-8 allows NUL, so a wide-text sample must be tried before it.
    wide = _decode_wide(data)
    if wide is not None:
        return wide
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    return data.decode("latin-1")


def is_binary(probe: bytes) -> bool:
    """True when a leading sample is binary rather than wide text.

    A NUL byte usually means a binary file. UTF-16 and UTF-32 text also
    contain NULs, so those samples are kept.
    """
    if b"\0" not in probe:
        return False
    if _bom_encoding(probe) is not None:
        return False
    return not _wide_text(probe)


def _declared_encodings(data: bytes) -> list[str]:
    encodings: list[str] = []
    bom = _bom_encoding(data)
    if bom is not None:
        encodings.append(bom)
    cookie = _coding_cookie(data)
    if cookie is not None and cookie not in encodings:
        encodings.append(cookie)
    return encodings


def _bom_encoding(data: bytes) -> str | None:
    if data.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return "utf-32"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    return None


def _coding_cookie(data: bytes) -> str | None:
    for line in data.split(b"\n", 2)[:2]:
        match = _COOKIE.search(line)
        if match is None:
            continue
        name = match.group(1).decode("ascii", errors="replace")
        try:
            codecs.lookup(name)
        except LookupError:
            return None
        return name
    return None


def _decode_wide(data: bytes) -> str | None:
    if not _wide_text(data[:8192]):
        return None
    best: str | None = None
    best_ascii = -1.0
    for encoding in _WIDE_ENCODINGS:
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if not text:
            continue
        ascii_ratio = sum(char.isascii() for char in text) / len(text)
        if ascii_ratio > best_ascii:
            best = text
            best_ascii = ascii_ratio
    if best is not None and best_ascii > 0.8:
        return best
    return None


def _wide_text(probe: bytes) -> bool:
    """UTF-16 or UTF-32 text without a byte-order mark."""
    if len(probe) < 8:
        return False
    half = len(probe) // 2
    even = sum(byte == 0 for byte in probe[0::2]) / half
    odd = sum(byte == 0 for byte in probe[1::2]) / half
    if (odd > 0.8 and even < 0.2) or (even > 0.8 and odd < 0.2):
        return True
    groups = len(probe) // 4
    wide = 0
    for start in range(0, groups * 4, 4):
        chunk = probe[start : start + 4]
        if chunk.count(0) >= 3 and any(chunk):
            wide += 1
    return groups > 0 and wide / groups > 0.8
