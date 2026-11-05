"""Small text helpers shared by the pipeline and modules."""
from __future__ import annotations

import re

# Spans that must keep their exact case: code, links, discord tokens like <@id> / <:emoji:id>, [[TAGS]]
KEEP = re.compile(r"```.*?```|`[^`\n]*`|https?://\S+|<[@#:a!&][^>\s]*>|\[\[[A-Z_]+(?::[^\]]*)?\]\]", re.S)


def lowercase_outside_code(text: str) -> str:
    out, pos = [], 0
    for m in KEEP.finditer(text):
        out.append(text[pos:m.start()].lower())
        out.append(m.group(0))
        pos = m.end()
    out.append(text[pos:].lower())
    return "".join(out)
