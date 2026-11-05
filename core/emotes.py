"""The server's custom emotes, from EMOTES_PATH (one per line: "<:name:id> meaning", # comments ok).

She's shown short names only and writes :name:; expand() turns that into the real <:name:id> just before a message
is sent (and react_code() does the same for [[REACT: :name:]]). That keeps 19-digit IDs out of her prompt and means
she can't get an ID wrong. Blocked emotes are never listed and never expanded. Re-reads the file when it changes."""
from __future__ import annotations

import re

from . import config as C

BLOCKED = {"emoji_kys", "kms", "arab", "nig"}
LINE = re.compile(r"^<(a?):([A-Za-z0-9_]{2,32}):(\d{5,25})>\s*(.*)$")
SHORT = re.compile(r"(?<![<\w:]):([A-Za-z0-9_]{2,32}):(?![\w>])")
_cache: tuple[float, list[tuple[str, str, str]]] | None = None


def load() -> list[tuple[str, str, str]]:
    """[(name, full code, meaning)] in file order, blocked ones left out."""
    global _cache
    try:
        mtime = C.EMOTES_PATH.stat().st_mtime
    except OSError:
        return []
    if _cache and _cache[0] == mtime:
        return _cache[1]
    out = []
    for ln in C.EMOTES_PATH.read_text(encoding="utf-8").splitlines():
        m = LINE.match(ln.strip())
        if m and m.group(2).lower() not in BLOCKED:
            out.append((m.group(2), f"<{m.group(1)}:{m.group(2)}:{m.group(3)}>", m.group(4).strip()))
    _cache = (mtime, out)
    return out


def codes() -> dict[str, str]:
    return {name.lower(): code for name, code, _ in load()}


def expand(text: str) -> str:
    """:name: -> <:name:id> for the server's emotes (any case); anything else (e.g. :skull:) is left alone."""
    if ":" not in text:
        return text
    table = codes()
    if not table:
        return text
    return SHORT.sub(lambda m: table.get(m.group(1).lower(), m.group(0)), text)


def react_code(arg: str) -> str:
    """[[REACT: :proud:]] -> the real code; unicode emoji and full codes pass through."""
    a = arg.strip()
    m = re.fullmatch(r":([A-Za-z0-9_]{2,32}):", a)
    return codes().get(m.group(1).lower(), a) if m else a


def prompt_block() -> str:
    items = load()
    if not items:
        return ""
    return ("# Server emotes\nWrite them as :name: (the system turns that into the real emote), in text or in "
            "[[REACT: :name:]], at most one per message, by mood. Only these names; the words after them are just what "
            "they mean, never write those.\n" + " · ".join(f":{n}: {mean}".strip() for n, _, mean in items))
