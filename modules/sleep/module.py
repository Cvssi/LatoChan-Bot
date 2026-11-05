"""Sleep mode: asleep (Discord idle) until someone she knows pings her, replies to her or says her name; awake
for a few minutes; dozes off again. Every few hours she wakes on her own and peeks at the main channel (a
"check-in"). "lato go to sleep" from the owner puts her to sleep at once.
Settings: sleep_mode (switch), awake_*_minutes, friends_can_wake, chimes_enabled, chime_*_hours,
chime_wait_seconds, quiet_hours_*. SLEEP_MODE=0 in the environment forces it off.
Service "sleep" is what core/router.py asks (on, is_awake, wake, fall_asleep...)."""
import asyncio
import os
import random
import re
import time
from zoneinfo import ZoneInfo

from core import clock
from core import config as C
from core.module import Module as Base
from core.text import lowercase_outside_code

SLEEP_ENV = os.environ.get("SLEEP_MODE", "1") == "1"


class Module(Base):
    HELP = {}
    ORDER = 70

    def __init__(self, *a):
        super().__init__(*a)
        self.awake_until: dict[int, float] = {}   # channel id -> monotonic time she falls asleep again
        self.chime_open: dict[int, bool] = {}     # channel id -> she just checked in and is waiting for an answer

    async def setup(self):
        self.lato.provide("sleep", self)

    def persona(self):
        return super().persona() if self.on() else ""

    # ---- service used by the router ----
    def on(self) -> bool:
        return SLEEP_ENV and bool(self.lato.S("sleep_mode"))

    def is_awake(self, cid: int) -> bool:
        return not self.on() or time.monotonic() < self.awake_until.get(cid, 0)

    def any_awake(self) -> bool:
        return any(self.is_awake(c) for c in list(self.awake_until))

    def wake(self, cid: int, seconds: float | None = None) -> None:
        S = self.lato.S
        lo, hi = sorted((S("awake_min_minutes"), S("awake_max_minutes")))
        secs = seconds if seconds is not None else random.uniform(lo, hi) * 60
        self.awake_until[cid] = max(self.awake_until.get(cid, 0), time.monotonic() + secs)
        if seconds is None:
            self.chime_open.pop(cid, None)   # a real wake-up, not just the one-minute check-in window

    def fall_asleep(self, cid: int) -> None:
        self.awake_until.pop(cid, None)
        self.chime_open.pop(cid, None)

    def take_chime_open(self, cid: int) -> bool:
        return bool(self.chime_open.pop(cid, None))

    def expire(self) -> None:
        for cid in [c for c, t in self.awake_until.items() if time.monotonic() >= t]:
            self.fall_asleep(cid)

    def status(self) -> str:
        if not self.on():
            return "off (always awake)"
        up = [c for c in self.awake_until if self.is_awake(c)]
        return f"on, awake in {len(up)} channel(s)" if up else "on, asleep"

    # ---- pipeline ----
    async def after_send(self, turn):
        if turn.reason == "sleep_cmd":
            self.fall_asleep(turn.cid)
            self.log.info("SLEEP told to sleep by %s", self.lato.speaker(turn.msg))

    # ---- check-ins ----
    def in_quiet_hours(self) -> bool:
        S = self.lato.S
        return bool(S("quiet_hours_enabled")) and clock.in_hours(S("quiet_from_hour"), S("quiet_to_hour"))

    async def loop(self):
        """Every few hours (a live setting) she wakes on her own, peeks at the main channel and says something."""
        S = self.lato.S
        while True:
            start = time.monotonic()
            lo, hi = sorted((S("chime_min_hours"), S("chime_max_hours")))
            deadline = start + random.uniform(lo, hi) * 3600
            while True:
                await asyncio.sleep(30)    # short naps so a settings change takes effect within a minute
                lo, hi = sorted((S("chime_min_hours"), S("chime_max_hours")))
                deadline = min(deadline, start + hi * 3600)
                if time.monotonic() >= deadline:
                    break
            if not (self.on() and S("chimes_enabled")) or self.in_quiet_hours():
                continue
            try:
                await self.chime()
            except Exception:
                self.log.exception("chime failed")

    async def chime(self):
        lato = self.lato
        cid = C.primary_channel()
        if self.is_awake(cid):
            return  # already up and chatting
        ch = await lato.channel(cid)
        recent = []
        async for m in ch.history(limit=25):
            if m.content.strip():
                when = m.created_at.astimezone(ZoneInfo(C.TIMEZONE)).strftime("%a %H:%M")
                recent.append(f"[{when}] {lato.who(m)}: {lato.clean_content(m)[:300]}")
        recent.reverse()
        note = ("(system note: you've been asleep and just woke up on your own. you peek at the chat. "
                "recent messages, oldest first:\n" + ("\n".join(recent) or "(nothing)") + "\n\n"
                "say ONE short thing: comment on what they were talking about if it's interesting or recent, "
                "or if the chat's dead or old, say you're bored and ask if anyone's on. don't ping anyone, "
                "don't recap the whole chat, don't greet everyone formally.)")
        gifs = lato.service("gifs")
        plan = gifs.plan() if gifs else {"force": False, "only": False, "note": ""}
        mood_mod = lato.module("personality")
        mood = await mood_mod.context(None) if mood_mod and mood_mod.enabled() else ""
        async with lato.locks[cid]:
            messages = [{"role": "system", "content": lato.system_prompt()}, *lato.recent_history(cid),
                        {"role": "user", "content": note + lato.turn_context(mood, plan["note"])}]
            reply, _ = await lato.llm.chat(messages, think=False)
        reply, calls, _ = lato.tags.parse(reply)
        reply = lowercase_outside_code(re.sub(r"\[\[[^\]]*\]\]", "", reply)).strip()
        mood_tag = next((c.arg.strip().lower() for c in calls if c.name == "GIF" and c.arg.strip()), None)
        gif = None
        if gifs:
            if mood_tag is None and plan["force"]:
                mood_tag = gifs.any_mood()
            elif mood_tag and not plan["force"] and random.random() * 100 >= lato.S("gif_chance"):
                mood_tag = None
            blocked = gifs.block_reason(cid, bypass_cooldown=plan["only"]) if mood_tag else None
            gif = gifs.pick(mood_tag) if mood_tag and not blocked else None
            if mood_tag and not gif:
                self.log.info("no gif sent for mood '%s': %s", mood_tag, blocked or "no links")
        if gif and plan["only"]:
            reply = ""
        if not reply and not gif:
            return
        self.log.info("CHIME #%s %s%s", getattr(ch, "name", cid), reply.replace("\n", " / "), f" +gif({mood_tag})" if gif else "")
        if reply:
            await lato.say(ch, reply)
        if gif:
            await lato.say(ch, gif)
            gifs.mark_sent(cid)
        lato.history.append(cid, {"role": "assistant", "content": reply or f"[[GIF: {mood_tag}]]"})
        self.wake(cid, lato.S("chime_wait_seconds"))   # give them a minute to answer before she dozes off again
        self.chime_open[cid] = True
