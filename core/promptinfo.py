"""Show what goes into her system prompt right now, without touching the running bot or its data.

    python -m core.promptinfo            sizes per part (chars and a rough token count)
    python -m core.promptinfo --raw      the full system prompt, exactly as the model gets it
    python -m core.promptinfo --json     sizes + parts as JSON (LatoDash)

On the server: `lato-prompt`, `lato-prompt --raw`. It reads settings and module files only (no Discord, no databases),
so module on/off states are the live ones."""
from __future__ import annotations

import importlib
import json
import logging
import sys

import botsettings

from . import config as C
from . import prompt


class _Lite:
    """Just enough of core.app.Lato for Module.enabled() and Module.persona()."""

    def __init__(self):
        self.S = botsettings.Live(str(C.SETTINGS_PATH))
        self.data_dir = C.DATA_DIR
        self.services: dict = {}

    def setting(self, key, default=None):
        try:
            return self.S(key)
        except KeyError:
            return default

    def service(self, name, default=None):
        return self.services.get(name, default)

    def provide(self, name, obj):
        self.services[name] = obj


def load_modules(lato) -> list:
    mods = []
    for folder in sorted(p for p in C.MODULES_DIR.iterdir() if (p / "module.py").exists()):
        try:
            cls = importlib.import_module(f"modules.{folder.name}.module").Module
            cls.NAME = folder.name
            mods.append(cls(lato, folder))
        except Exception as e:      # same as the bot: a broken module is left out
            print(f"(module {folder.name} failed to load: {e!r})", file=sys.stderr)
    mods.sort(key=lambda m: (m.ORDER, m.NAME))
    return mods


def current_parts() -> list[tuple[str, str]]:
    lato = _Lite()
    snippets = [(m.NAME, m.persona()) for m in load_modules(lato) if m.enabled()]
    return prompt.parts(snippets)


def tokens(text: str) -> int:
    return round(len(text) / 3.6)        # rough for English chat text; good enough to compare parts


def main(argv: list[str]) -> int:
    logging.disable(logging.WARNING)
    parts = current_parts()
    if "--raw" in argv:
        print(prompt.join(parts))
        return 0
    if "--json" in argv:
        print(json.dumps({"parts": [{"name": n, "chars": len(t), "tokens": tokens(t), "text": t} for n, t in parts],
                          "total_chars": len(prompt.join(parts)), "total_tokens": tokens(prompt.join(parts))}))
        return 0
    total = prompt.join(parts)
    w = max(len(n) for n, _ in parts)
    for n, t in parts:
        print(f"{n:<{w}}  {len(t):>6} chars  ~{tokens(t):>5} tokens")
    print(f"{'total':<{w}}  {len(total):>6} chars  ~{tokens(total):>5} tokens")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
