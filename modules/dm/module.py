"""DMs: [[DM: @name -> message]] sends a private message, and friends who DM her get answers.

Who may ask = dm_who (owner / trusted / friends, core/access.py); the asker's own words must ask for a message
(DM_INTENT), so other people's text can't make her DM anyone. Recipients: the owner or someone on the friends list
only, resolved by friends.json names (never strangers, never the bot), at most dm_daily_cap per person per server-local day.
Incoming DMs: the router asks this module (service "dm") whether to answer; strangers are always ignored.
Switch: mod_dm."""
import datetime
import json
import re
from zoneinfo import ZoneInfo

import discord

from core import access
from core import config as C
from core import emotes
from core.module import Module as Base
from core.text import lowercase_outside_code

DM_INTENT = re.compile(r"\b(dm|dms|message|msg|text|tell|send|let\s+\w+\s+know|remind|ask|ping)\b", re.I)
MAX_PER_REPLY = 2
MAX_LEN = 1500


class Module(Base):
    HELP = {"title": "dms", "line": "i can message a friend for you, and you can dm me", "examples": ("lato tell alice the movie's at 8", "lato dm me the link"), "who": "dm_who"}
    ORDER = 85
    SWITCH = "mod_dm"
    TAGS = {"DM": ("MESSAGE", "PM")}

    async def setup(self):
        self.lato.provide("dm", self)
        self.sent_file = self.data_dir() / "sent.json"

    def persona(self):
        who = {"owner": "{{USER_NAME}}", "trusted": "{{USER_NAME}} or a (trusted) friend",
               "friends": "{{USER_NAME}} or one of his friends"}.get(self.lato.S("dm_who"), "{{USER_NAME}}")
        return super().persona().replace("{{WHO}}", who)

    # ---- incoming DMs (asked by core/router.py) ----
    def accept(self, msg) -> bool:
        """Answer this DM? Only while on, dm_reply is on, and the sender is the owner or a friend."""
        return self.enabled() and bool(self.lato.S("dm_reply")) and self.lato.is_known(msg.author.id)

    # ---- sending ----
    def recipient(self, ref: str) -> int | None:
        """Owner or exactly one friend by name (friends.json); None if unknown or ambiguous."""
        r = ref.strip().lstrip("@").strip().lower()
        if not r:
            return None
        if r in ("me", "myself", C.USER_NAME.lower()):
            return C.OWNER_ID
        if r.isdigit():
            uid = int(r)
            return uid if uid == C.OWNER_ID or self.lato.is_friend(uid) else None
        names = [(int(f["id"]), (f.get("name") or "").lower()) for f in self.lato.friends.all() if f.get("name")]
        for test in (lambda n: n == r, lambda n: n.startswith(r)):
            hits = {uid for uid, n in names if test(n)}
            if len(hits) == 1:
                return hits.pop()
            if len(hits) > 1:
                return None
        return None

    def _count(self, uid: int, add: bool = False) -> int:
        day = datetime.datetime.now(ZoneInfo(C.TIMEZONE)).strftime("%Y-%m-%d")
        try:
            data = json.loads(self.sent_file.read_text())
        except (OSError, ValueError):
            data = {}
        if data.get("day") != day:
            data = {"day": day, "sent": {}}
        n = int(data["sent"].get(str(uid), 0))
        if add:
            data["sent"][str(uid)] = n + 1
            try:
                self.sent_file.write_text(json.dumps(data))
            except OSError:
                self.log.exception("couldn't save the DM counter")
        return n

    async def on_reply(self, turn):
        if turn.tags("DM"):
            turn.has_output = True

    async def act(self, turn):
        calls = turn.tags("DM")
        if not calls:
            return
        if not access.allows(turn, self.lato.S("dm_who")):
            self.log.info("DM: %s isn't allowed to ask for DMs", self.lato.speaker(turn.msg))
            turn.done.append("dm skipped (they can't ask for DMs)")
            return
        if not DM_INTENT.search(turn.joined):
            turn.done.append("dm skipped (they didn't ask for a message)")
            return
        for c in calls[:MAX_PER_REPLY]:
            turn.done.append(await self.send(c.arg, turn))

    async def send(self, arg: str, turn) -> str:
        ref, sep, text = arg.partition("->")
        if not sep:
            ref, _, text = arg.strip().partition(" ")
        text = lowercase_outside_code(text.strip())[:MAX_LEN]
        if not text:
            return "dm failed: nothing to send"
        uid = self.recipient(ref)
        if uid is None:
            return f"dm failed: '{ref.strip()}' isn't {C.USER_NAME} or someone on the friends list (or the name matches more than one)"
        if uid == turn.msg.author.id and uid != C.OWNER_ID:
            return "dm skipped (they asked to be messaged themselves; just answer here)"
        if self._count(uid) >= int(self.lato.S("dm_daily_cap")):
            return f"dm skipped (already sent {self.lato.S('dm_daily_cap')} dms to them today)"
        try:
            user = self.lato.client.get_user(uid) or await self.lato.client.fetch_user(uid)
            dm = await user.send(emotes.expand(text), allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden:
            return "dm failed: they don't accept DMs from her (privacy settings)"
        except Exception as e:
            self.log.warning("DM to %s failed: %r", uid, e)
            return "dm failed: discord didn't take it"
        self._count(uid, add=True)
        name = C.USER_NAME if uid == C.OWNER_ID else next((f["name"] for f in self.lato.friends.all() if int(f["id"]) == uid), str(uid))
        # so she knows what she said if they answer in the DM
        self.lato.history.append(dm.channel.id, {"role": "assistant", "content": text})
        self.log.info("DM to %s (asked by %s): %s", name, self.lato.speaker(turn.msg), text)
        return f"dm sent to {name}"
