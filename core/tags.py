"""The one tag parser. The model asks for things with [[NAME]] or [[NAME: args]] in its reply.

Modules register the tag names they own (plus misspellings the model invents). parse() pulls every known tag
out of the text and returns them as TagCalls in order. Unknown short tags are dropped so they never show in
chat; anything that only looks like a tag (long, multi-line) is left alone, exactly like v1.

Tags of every *installed* module are always stripped, even while that module is switched off, so a switched-off
feature can never leak raw tags (e.g. a long [[REMEMBER: ...]]) into chat.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

TAG_RE = re.compile(r"\[\[\s*([A-Za-z_]+)(?:(?:\s*:\s*|\s+)(.*?))?\s*\]\]", re.S)
SHORT_UNKNOWN = re.compile(r"\[\[[^\]\n]{0,60}\]\]")


@dataclass
class TagCall:
    name: str          # canonical upper-case name
    arg: str           # stripped argument text ("" if none)
    raw: str           # exactly what the model wrote
    owner: str         # module that owns the tag ("core" for SKIP)


class TagRegistry:
    def __init__(self):
        self._canon: dict[str, tuple[str, str]] = {}   # NAME/ALIAS -> (canonical, owner)

    def register(self, name: str, owner: str, aliases: tuple[str, ...] = ()) -> None:
        name = name.upper()
        for n in (name, *[a.upper() for a in aliases]):
            prev = self._canon.get(n)
            if prev and prev[1] != owner:
                raise ValueError(f"tag [[{n}]] is claimed by both '{prev[1]}' and '{owner}'")
            self._canon[n] = (name, owner)

    def lookup(self, name: str) -> tuple[str, str] | None:
        return self._canon.get(name.upper())

    def names(self) -> set[str]:
        return set(self._canon)

    def parse(self, text: str) -> tuple[str, list[TagCall], list[str]]:
        """-> (text without tags, known calls in order, dropped unknown tags)"""
        calls: list[TagCall] = []
        dropped: list[str] = []
        out, pos = [], 0
        for m in TAG_RE.finditer(text):
            raw = m.group(0)
            hit = self.lookup(m.group(1))
            if hit:
                calls.append(TagCall(hit[0], (m.group(2) or "").strip(), raw, hit[1]))
            elif SHORT_UNKNOWN.fullmatch(raw):
                dropped.append(raw)
            else:
                continue                       # not a tag we understand and too odd to touch: leave it
            out.append(text[pos:m.start()])
            pos = m.end()
        out.append(text[pos:])
        clean = re.sub(r"[ \t]+\n", "\n", "".join(out)).strip()
        return clean, calls, dropped
