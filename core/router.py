"""Who she answers and when: strangers/friends/owner, sleep and wake, and bursts (she waits until someone has
finished double/triple texting, then answers it all at once)."""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time

import discord

from . import config as C
from .media import has_media
from .people import addressed_to_bot
from .tasks import spawn

log = logging.getLogger("latochan")
TYPING_GRACE_S = 9                                        # Discord's typing indicator lasts ~10 s
NAME_RE = re.compile(r"\blato(?:[\s-]?chan)?\b", re.I)
SLEEP_CMD = re.compile(r"\b(go (back )?to sleep|go to bed|go nap|nap time|sleep now|night night|goodnight|good night|gn)\b", re.I)


class Burst:
    def __init__(self, reason: str, debounce: float):
        self.msgs: list = []
        self.reason = reason            # "main" | "pinged" | "name" | "woke" | "awake" | "sleep_cmd" | "dm"
        self.started = time.monotonic()
        self.deadline = self.started + debounce


class Router:
    def __init__(self, lato):
        self.lato = lato
        self.bursts: dict[tuple[int, int], Burst] = {}

    def on_typing(self, channel, user) -> None:
        b = self.bursts.get((channel.id, user.id))
        if b:
            b.deadline = max(b.deadline, time.monotonic() + TYPING_GRACE_S)

    async def handle(self, msg) -> None:
        lato, S = self.lato, self.lato.S
        if msg.author.bot:
            return
        if not msg.content.strip() and not has_media(msg):
            return
        is_owner = msg.author.id == C.OWNER_ID
        in_main = msg.channel.id in C.MAIN_CHANNELS
        key = (msg.channel.id, msg.author.id)

        if lato.ignored(msg):
            return

        b = self.bursts.get(key)
        if b:  # they're mid-burst: everything they send joins it and resets the wait
            b.msgs.append(msg)
            b.deadline = max(b.deadline, time.monotonic() + S("debounce_seconds"))
            return

        if re.fullmatch(r"\s*(https?://\S+\s*)+", msg.content) and not msg.attachments:
            return  # bare links (tweets, clips): don't comment on every one
        if isinstance(msg.channel, discord.DMChannel):   # a DM: only the dm module decides (off / stranger = ignored)
            svc = lato.service("dm")
            if not (svc and svc.accept(msg)):
                return
            self.start(key, msg, "dm", is_owner)
            return
        pinged = addressed_to_bot(lato.client, msg)
        named = bool(NAME_RE.search(msg.content))
        known = lato.is_known(msg.author.id)
        cid = msg.channel.id
        sl = lato.sleep
        if sl.on() and not sl.is_awake(cid):
            if not (pinged or named):
                return                      # asleep: chatter doesn't reach her
            if is_owner and SLEEP_CMD.search(msg.content):
                return                      # "go to sleep" while she's already asleep
            if not known or not (is_owner or S("friends_can_wake")):
                if not pinged:
                    return                  # strangers can't wake her by name-dropping
                reason = "pinged"           # a stranger pinging still gets brushed off, but she stays asleep
            else:
                reason = "woke"
                sl.wake(cid)
                log.info("SLEEP woken by %s in #%s", lato.speaker(msg), getattr(msg.channel, "name", cid))
        else:
            if sl.on() and known and sl.take_chime_open(cid):
                sl.wake(cid)                # someone answered her check-in: stay up properly
            if sl.on() and known and (pinged or named):
                sl.wake(cid)                # talking to her keeps her up
            if is_owner and named and SLEEP_CMD.search(msg.content):
                reason = "sleep_cmd"
            elif is_owner and in_main:
                reason = "main"
            elif pinged:
                reason = "pinged"
            elif named:
                reason = "name"   # said her name without pinging: she may answer if she wants
            elif sl.on() and known:
                reason = "awake"  # she's up and hanging around: may join in
            else:
                return

        if reason == "awake" and random.random() * 100 >= S("chatter_reply_chance"):
            return   # not in the mood to join in

        self.start(key, msg, reason, is_owner)

    def start(self, key, msg, reason: str, is_owner: bool) -> None:
        lato, S = self.lato, self.lato.S
        if not is_owner:
            now = time.monotonic()
            if now - lato.last_seen.get(msg.author.id, 0) < S("other_cooldown_seconds"):
                return
            lato.last_seen[msg.author.id] = now

        b = self.bursts[key] = Burst(reason, S("debounce_seconds"))
        b.msgs.append(msg)
        spawn(self.run_burst(key, b), "burst")

    async def run_burst(self, key, b: Burst) -> None:
        try:
            while True:
                now = time.monotonic()
                wait = min(b.deadline, b.started + self.lato.S("max_wait_seconds")) - now
                if wait <= 0:
                    break
                await asyncio.sleep(wait)
        finally:
            if self.bursts.get(key) is b:
                del self.bursts[key]
        try:
            await self.lato.pipeline.respond(b.msgs, b.reason)
        except Exception:
            log.exception("respond failed")
