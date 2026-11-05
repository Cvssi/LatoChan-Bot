"""Reaction gifs: [[GIF: mood]] posts a random link from GIF_DIR/<mood>/links.txt (rules in ../../gifstore.py).
Settings: gifs_enabled (switch), gif_chance, wake_gif_chance, gif_only, gif_reply_chance, gif_only_chance,
gif_cooldown_seconds. Service "gifs": plan(), pick(), block_reason(), any_mood(), mark_sent() for other modules
(the sleep module's check-ins use it)."""
import os
import random
import re
import time
from pathlib import Path

from core.module import Module as Base
from gifstore import KNOWN_MOODS as KNOWN

GIF_DIR = Path(os.environ.get("GIF_DIR", "/var/lib/latochan/gifs"))
FALLBACK_SKIP = {"waking", "sleepy"}
NO_PLAN = {"force": False, "only": False, "note": ""}
ONLY_NOTE = "(gif mode: answer with a gif only this time. exactly one [[GIF: mood]] line, picked to fit the moment, and no other words.)"


class Module(Base):
    HELP = {}
    ORDER = 50
    TAGS = {"GIF": ()}

    def __init__(self, *a):
        super().__init__(*a)
        self.last_gif: dict[int, float] = {}

    async def setup(self):
        self.lato.provide("gifs", self)

    def persona(self):
        if not self.lato.S("gifs_enabled"):
            return ""
        moods = self.moods()
        return super().persona().replace("{{MOODS}}", ", ".join(moods)) if moods else ""

    def moods(self) -> list[str]:
        """Moods with at least one link, known moods first (stable order keeps the prompt cache warm)."""
        try:
            have = {d.name for d in GIF_DIR.iterdir() if d.is_dir() and self.pick(d.name)}
        except OSError:
            return []
        return [m for m in KNOWN if m in have] + sorted(have - set(KNOWN))

    # ---- service ----
    def pick(self, mood: str) -> str | None:
        """Random link from gifs/<mood>/links.txt (one URL per line, # comments ok). Re-read every time."""
        f = GIF_DIR / re.sub(r"[^a-z_]", "", mood.lower().replace(" ", "_")) / "links.txt"
        try:
            links = [ln.strip() for ln in f.read_text(encoding="utf-8").splitlines()
                     if ln.strip().startswith("https://") and not ln.strip().startswith("#")]
        except OSError:
            return None
        return random.choice(links) if links else None

    def block_reason(self, cid: int, bypass_cooldown: bool = False) -> str | None:
        """Why a gif can't go out right now (None = it can). Master switch + per-channel cooldown."""
        S = self.lato.S
        if not S("gifs_enabled"):
            return "gifs are switched off"
        gap = S("gif_cooldown_seconds")
        if gap and not bypass_cooldown and time.monotonic() - self.last_gif.get(cid, -1e9) < gap:
            return f"cooldown ({gap}s between gifs in a channel)"
        return None

    def any_mood(self) -> str | None:
        """A random mood that has at least one link (she was told to send a gif but didn't name a mood)."""
        try:
            have = [d.name for d in GIF_DIR.iterdir() if d.is_dir() and d.name not in FALLBACK_SKIP and self.pick(d.name)]
        except OSError:
            return None
        return random.choice(have) if have else None

    def plan(self) -> dict:
        """Rolled once per reply. force = she is told to include a gif; only = the gif goes alone (no words)."""
        S = self.lato.S
        if not S("gifs_enabled"):
            return dict(NO_PLAN)
        gif_mode = bool(S("gif_only"))
        force = gif_mode or random.random() * 100 < S("gif_reply_chance")
        only = gif_mode or random.random() * 100 < S("gif_only_chance")
        note = ""
        if force and only:
            note = ONLY_NOTE
        elif force:
            note = "(gif: put one [[GIF: mood]] line in this reply, picked to fit how you feel, alongside your normal short answer.)"
        return {"force": force, "only": only, "note": note}

    def mark_sent(self, cid: int) -> None:
        self.last_gif[cid] = time.monotonic()

    @staticmethod
    def mood_of(turn_or_calls) -> str | None:
        calls = turn_or_calls.tags("GIF") if hasattr(turn_or_calls, "tags") else [c for c in turn_or_calls if c.name == "GIF"]
        return calls[0].arg.strip().lower() or None if calls else None

    # ---- pipeline ----
    async def context(self, turn) -> str:
        if turn.mode == "gif":   # the chatter mix picked "just a gif" (core/mix.py)
            plan = {"force": True, "only": True, "note": ONLY_NOTE}
        else:
            plan = self.plan() if not turn.stranger else dict(NO_PLAN)
        turn.data["gifs"] = {"plan": plan}
        return plan["note"]

    async def on_reply(self, turn):
        S, st = self.lato.S, turn.data.setdefault("gifs", {"plan": dict(NO_PLAN)})
        plan, mood = st["plan"], self.mood_of(turn)
        if mood is None and turn.reason == "woke" and (plan["force"] or random.random() * 100 < S("wake_gif_chance")):
            mood = "waking"
        elif mood is None and plan["force"]:
            mood = self.any_mood()      # told to send a gif but named no mood: pick any that has links
            if mood:
                self.log.info("GIF she didn't name a mood; picked '%s'", mood)
        elif mood is not None and not plan["force"] and random.random() * 100 >= S("gif_chance"):
            self.log.info("skipping the '%s' gif (gif_chance)", mood)
            mood = None
        # you asking her directly, or gif mode, ignores the per-channel cooldown
        blocked = self.block_reason(turn.cid, bypass_cooldown=turn.is_owner or plan["only"]) if mood else None
        gif = self.pick(mood) if mood and not turn.stranger and not blocked else None
        if mood and not gif:
            self.log.info("no gif sent for mood '%s': %s", mood,
                          "stranger" if turn.stranger else blocked or f"no links in gifs/{mood}/links.txt")
        st.update(mood=mood, gif=gif)
        if gif:
            turn.has_output = True

    async def before_send(self, turn):
        st = turn.data.get("gifs") or {}
        gif = st.get("gif")
        if not gif:
            return
        if st["plan"]["only"]:
            turn.blank_text = True          # gif mode: just the gif, no words
            turn.data["history_fallback"] = f"[[GIF: {st['mood']}]]"
        if turn.reason == "woke":
            await turn.msg.channel.send(gif, allowed_mentions=self.lato.allowed_mentions())   # yawn first, then talk
            if not turn.is_owner:
                self.mark_sent(turn.cid)
            st["gif"] = None

    async def after_send(self, turn):
        st = turn.data.get("gifs") or {}
        if st.get("gif"):
            await turn.msg.channel.send(st["gif"], allowed_mentions=self.lato.allowed_mentions())
            self.mark_sent(turn.cid)
            turn.history_text = ((turn.history_text or "") + f" [[GIF: {st['mood']}]]").strip()
