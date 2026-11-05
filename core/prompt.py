"""System prompt and per-turn context. The system prompt stays byte-identical between messages (Ollama reuses
its cache); everything that changes per message goes at the end of the newest message.

The system prompt is built from parts, always in the same order:
  1. persona   PERSONA_PATH (persona.md): who she is. Owner-edited, no tags or mechanics.
  2. rules     core/rules.md: how the chat works (name labels, [[SKIP]], honesty, hard limits).
  3. modules   each enabled module's persona text, in module ORDER (only while that module is on).
"""
from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

from . import config as C

def fill(text: str) -> str:
    return text.replace("{{BOT_NAME}}", C.BOT_NAME).replace("{{USER_NAME}}", C.USER_NAME).strip()


def base_parts() -> list[tuple[str, str]]:
    """[(name, text)] for the persona and the core rules."""
    parts = [("rules", fill(C.RULES_PATH.read_text(encoding="utf-8")))]
    if C.PERSONA_PATH.is_file():
        parts.insert(0, ("persona", fill(C.PERSONA_PATH.read_text(encoding="utf-8"))))
    return parts


def parts(module_snippets: list[tuple[str, str]], extra: str = "") -> list[tuple[str, str]]:
    out = base_parts() + [(f"module {n}", fill(s)) for n, s in module_snippets if s and s.strip()]
    if extra:
        out.append(("extra", extra))
    return out


def join(parts_: list[tuple[str, str]]) -> str:
    return "\n\n".join(t for _, t in parts_)


def turn_context(*parts_: str) -> str:
    now = datetime.datetime.now(ZoneInfo(C.TIMEZONE))
    body = "\n".join([f"right now it is {now:%A %d %B %Y, %H:%M} {now.tzname()}."] + [p for p in parts_ if p])
    return "\n\n(system context, not part of their message:\n" + body + ")"
