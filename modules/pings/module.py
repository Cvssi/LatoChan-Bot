"""Pings: she can @mention people. When her reply contains @name and exactly one member of the server has that
name, it becomes a real ping. Never @everyone / @here / roles (Discord blocks those for her anyway).
Switch: mod_pings. Off = names stay plain text and nobody is notified (the v1 behaviour)."""
import re

from core import config as C
from core.module import Module as Base
from core.people import member_names

AT_NAME = re.compile(r"(?<![\w<@])@([\w][\w.\-]{1,31})")


class Module(Base):
    HELP = {}
    ORDER = 80
    SWITCH = "mod_pings"

    async def setup(self):
        self.lato.provide("mentions", self)

    def allow_users(self) -> bool:
        return self.enabled()

    def resolve(self, guild, name: str):
        n = name.lower().rstrip(".")
        if n in ("everyone", "here"):
            return None
        if n == C.USER_NAME.casefold():
            return C.OWNER_ID
        if not guild:
            return None
        hits = {m.id for m in guild.members if not m.bot and n in member_names(m)}
        return hits.pop() if len(hits) == 1 else None

    def clean_text(self, turn, text):
        guild = turn.msg.guild

        def sub(m):
            uid = self.resolve(guild, m.group(1))
            return f"<@{uid}>" if uid else m.group(0)
        return AT_NAME.sub(sub, text)
