"""The contract every feature module follows. See ../MODULES.md for the how-to.

A module is a folder modules/<name>/ with:
  module.py    class Module(core.module.Module)   - behaviour (this file's hooks)
  settings.py  GROUP + SETTINGS                    - optional; stdlib only, read by botsettings.py / lato-config / LatoDash
  persona.md   optional text added to her system prompt while the module is on
  (docs)       a section in ../MODULES.md: what it does, how to use it, its settings

Rules:
  * Modules never import each other. Shared things go through `self.lato` (core services) or named services
    (self.lato.service("gifs")), which always have a do-nothing fallback, so any module can be removed.
  * SWITCH names the on/off setting. None = always on (the v1 features, which keep their own old switches).
    A switched-off module gets no hooks, no events, no persona text; its tags are still stripped from replies.
  * Any exception in a hook is logged and that hook is skipped for this message. Three errors within ten
    minutes switch the module off until the next restart. The bot itself never goes down because of a module.
"""
from __future__ import annotations

import logging
from pathlib import Path


class Module:
    NAME = ""              # set by the loader from the folder name
    ORDER = 50             # hook order: lower runs first (also the order of prompt notes)
    SWITCH: str | None = None
    TAGS: dict[str, tuple[str, ...]] = {}   # {"GIF": ("GIFS",)}: tag name -> extra spellings
    NEEDS: tuple[str, ...] = ()             # env flags it needs, e.g. ("PRESENCE_INTENT",): informational only
    HELP: dict | None = None                # /help entry (see core/commands.py); {} = not listed. Every module sets it.

    def __init__(self, lato, folder: Path):
        self.lato = lato
        self.folder = folder
        self.log = logging.getLogger(f"latochan.{self.NAME}")
        self.fail_times: list[float] = []
        self.broken = False

    # ---- state ----
    def enabled(self) -> bool:
        if self.broken:
            return False
        return self.SWITCH is None or bool(self.lato.setting(self.SWITCH, False))

    def data_dir(self) -> Path:
        d = self.lato.data_dir / "mod" / self.NAME
        d.mkdir(parents=True, exist_ok=True)
        return d

    def status(self) -> str:
        """One line for `lato-modules` / the dashboard."""
        return "on" if self.enabled() else "off"

    # ---- lifecycle ----
    async def setup(self) -> None:
        """Once, before the bot connects. Register services here: self.lato.provide("name", obj)."""

    async def loop(self) -> None:
        """Optional background task, started once when the bot is ready. Must check self.enabled() itself."""

    def persona(self) -> str:
        """Extra system-prompt text while enabled. Default: persona.md next to module.py, if present.
        Override it when the text depends on a setting (and return "" when the feature is off, so she never
        hears about things she can't do). Keep it short: it's sent with every message. See MODULES.md."""
        return self.persona_file("persona.md")

    def persona_file(self, name: str) -> str:
        p = self.folder / name
        try:
            return p.read_text(encoding="utf-8").strip() if p.exists() else ""
        except OSError:
            return ""

    # ---- reply pipeline hooks (turn = core.turn.Turn), in the order they run ----
    def ignore(self, msg) -> bool:
        """True = she doesn't look at this message at all."""
        return False

    async def context(self, turn) -> str:
        """A note added to the end of the newest message for the model (memory, game state...)."""
        return ""

    async def on_reply(self, turn) -> None:
        """Model has answered; turn.calls holds the parsed tags. Decide what to do (set turn.has_output if you
        will post or do something, so a [[SKIP]] doesn't swallow it)."""

    def clean_text(self, turn, text: str) -> str:
        """Last chance to edit the reply text before it's lower-cased and sent."""
        return text

    async def act(self, turn) -> None:
        """Do things (after the skip check, before sending). Put results in turn.done."""

    async def before_send(self, turn) -> None: ...

    async def after_send(self, turn) -> None: ...

    async def finish(self, turn) -> None:
        """After the reply and history are saved."""
