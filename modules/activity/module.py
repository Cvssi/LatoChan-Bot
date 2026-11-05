"""Activity watcher: when a friend starts a new game or a new artist on Spotify, she may ping them about it
(tease, roast their taste, ask how it's going). Cooldowns per person and overall, a chance roll, quiet hours.

Needs the privileged Presence Intent:
  1. Discord Developer Portal -> LatoChan -> Bot -> Privileged Gateway Intents -> Presence Intent ON
  2. PRESENCE_INTENT=1 in /etc/latochan.env, then restart her.
Doing step 2 without step 1 stops her logging in, so always do the portal first.
Play time per person per day is kept in mod/activity/played.json so she can say "you've been on this for 5 hours".
"""
import datetime
import json
import random
import re
import time
from zoneinfo import ZoneInfo

import discord

from core import clock
from core import config as C
from core.module import Module as Base
from core.text import lowercase_outside_code


def describe(acts) -> dict:
    """What someone is doing right now: {"game": name|None, "song": (title, artist)|None}"""
    out = {"game": None, "song": None}
    for a in acts or ():
        if isinstance(a, discord.Spotify):
            out["song"] = (a.title, a.artist)
        elif getattr(a, "type", None) == discord.ActivityType.playing and getattr(a, "name", None):
            out["game"] = a.name
    return out


ASK = re.compile(r"listen|playing|\bplay\b|song|music|spotify|track|\bgame\b|what am i (doing|on)", re.I)


class Module(Base):
    HELP = {"title": "what you're playing", "line": "i notice new games / spotify artists and might comment. not into it? ask to be left out", "who": "anyone"}
    ORDER = 95
    SWITCH = "mod_activity"
    NEEDS = ("PRESENCE_INTENT",)

    async def setup(self):
        self.last_user: dict[int, float] = {}
        self.last_any = -1e9   # "long ago": monotonic() is small right after a reboot
        self.last_artist: dict[int, str] = {}
        self.started: dict[tuple[int, str], float] = {}     # (user, game) -> when they started (wall clock)
        self.played_file = self.data_dir() / "played.json"
        if not C.PRESENCE_INTENT:
            self.log.info("activity: PRESENCE_INTENT is not set, so Discord won't send presence updates (module idle)")

    def status(self) -> str:
        if not self.enabled():
            return "off"
        return "on" if C.PRESENCE_INTENT else "on but idle: PRESENCE_INTENT=1 missing"

    # ---- play time ----
    def _played(self) -> dict:
        try:
            return json.loads(self.played_file.read_text())
        except (OSError, ValueError):
            return {}

    def add_played(self, uid: int, game: str, seconds: float) -> None:
        day = datetime.datetime.now(ZoneInfo(C.TIMEZONE)).date().isoformat()
        d = self._played()
        d = {k: v for k, v in d.items() if k >= (datetime.date.fromisoformat(day) - datetime.timedelta(days=7)).isoformat()}
        u = d.setdefault(day, {}).setdefault(str(uid), {})
        u[game] = u.get(game, 0) + int(seconds)
        self.played_file.write_text(json.dumps(d))

    def played_today(self, uid: int, game: str) -> float:
        day = datetime.datetime.now(ZoneInfo(C.TIMEZONE)).date().isoformat()
        secs = self._played().get(day, {}).get(str(uid), {}).get(game, 0)
        st = self.started.get((uid, game))
        return secs + (time.time() - st if st else 0)

    # ---- answering "what am I listening to / what's dana playing?" ----
    def describe_member(self, member) -> str:
        name = C.USER_NAME if member.id == C.OWNER_ID else (member.display_name or member.name).lower()
        now = describe(getattr(member, "activities", ()) or ())
        bits = []
        if now["song"]:
            bits.append(f"listening to \"{now['song'][0]}\" by {now['song'][1]} on spotify")
        if now["game"]:
            hrs = self.played_today(member.id, now["game"]) / 3600
            bits.append(f"playing {now['game']}" + (f" (about {hrs:.0f}h today)" if hrs >= 1 else ""))
        return f"{name} is " + " and ".join(bits) if bits else f"{name}: nothing showing on discord right now"

    async def context(self, turn) -> str:
        if turn.stranger or not C.PRESENCE_INTENT or not ASK.search(turn.joined):
            return ""
        msg = turn.msg
        guild = getattr(msg, "guild", None)
        people = [msg.author] + [u for u in msg.mentions if not getattr(u, "bot", False) and u.id != msg.author.id][:3]
        lines = []
        for p in people:
            m = guild.get_member(p.id) if guild else None
            lines.append(self.describe_member(m or p))
        return "(what they're doing on discord right now: " + "; ".join(lines) + ". use it if they're asking about it.)"

    # ---- the event ----
    def quiet_now(self) -> bool:
        S = self.lato.S
        if not (S("activity_quiet") and S("quiet_hours_enabled")):
            return False
        return clock.in_hours(S("quiet_from_hour"), S("quiet_to_hour"))

    def eligible(self, member) -> bool:
        if member.bot:
            return False
        known = self.lato.is_known(member.id)
        return known or bool(self.lato.S("activity_strangers"))

    async def ev_presence_update(self, before, after):
        if not self.eligible(after):
            return
        b, a = describe(before.activities), describe(after.activities)
        uid = after.id
        # bookkeeping for play time, whether or not she says anything
        if b["game"] and b["game"] != a["game"]:
            st = self.started.pop((uid, b["game"]), None)
            if st:
                self.add_played(uid, b["game"], time.time() - st)
        event = None
        if a["game"] and a["game"] != b["game"]:
            self.started[(uid, a["game"])] = time.time()
            if self.lato.S("activity_games"):
                event = ("game", a["game"])
        elif a["song"] and self.lato.S("activity_music"):
            artist = a["song"][1]
            if artist and artist != self.last_artist.get(uid):
                self.last_artist[uid] = artist
                event = ("song", a["song"])
        if event and self.should_comment(uid):
            await self.comment(after, event)

    def should_comment(self, uid: int) -> bool:
        S, now = self.lato.S, time.monotonic()
        if now - self.last_user.get(uid, -1e9) < S("activity_user_cooldown_min") * 60:
            return False
        if now - self.last_any < S("activity_global_cooldown_min") * 60:
            return False
        if self.quiet_now() or random.random() * 100 >= S("activity_chance"):
            return False
        self.last_user[uid] = now
        self.last_any = now
        return True

    def note_for(self, member, event) -> str:
        name = C.USER_NAME if member.id == C.OWNER_ID else (member.display_name or member.name).lower()
        if event[0] == "game":
            hrs = self.played_today(member.id, event[1]) / 3600
            extra = f" they've already played it for about {hrs:.0f} hours today." if hrs >= 1 else ""
            what = f"{name} just started playing {event[1]}.{extra}"
        else:
            title, artist = event[1]
            what = f"{name} is listening to \"{title}\" by {artist} on spotify."
        return (f"(system note: you just noticed on discord that {what} say ONE short thing to them about it: tease them, "
                "roast their taste or ask how it's going. the system pings them for you, so don't write their name with @. "
                "keep it short and playful.)")

    async def comment(self, member, event) -> None:
        lato = self.lato
        cid = C.primary_channel()
        try:
            ch = await lato.channel(cid)
        except Exception:
            self.log.exception("activity: can't reach the main channel")
            return
        note = self.note_for(member, event)
        async with lato.locks[cid]:
            messages = [{"role": "system", "content": lato.system_prompt()}, *lato.recent_history(cid),
                        {"role": "user", "content": note + lato.turn_context()}]
            try:
                reply, _ = await lato.llm.chat(messages, think=False)
            except Exception:
                self.log.exception("activity: model call failed")
                return
        reply, calls, _ = lato.tags.parse(reply)
        reply = lowercase_outside_code(re.sub(r"\[\[[^\]]*\]\]", "", reply)).strip()
        if not reply or any(c.name == "SKIP" for c in calls):
            self.log.info("activity: she let %s's %s slide", member, event[0])
            return
        mention = f"<@{member.id}>" if lato.S("activity_ping") else (member.display_name or member.name).lower()
        text = f"{mention} {reply}"
        self.log.info("ACTIVITY %s (%s: %s) -> %s", member, event[0], event[1], reply.replace("\n", " / "))
        await lato.say(ch, text)
        lato.history.append(cid, {"role": "assistant", "content": text})
        sl = lato.sleep
        if sl.on() and not sl.is_awake(cid):
            sl.wake(cid, lato.S("chime_wait_seconds"))   # give them a minute to answer, like a check-in
