"""Per-person notes: [[REMEMBER: note]], [[REMEMBER: @name -> note]] (owner only), [[FORGET: text]].
Storage and the lato-memory CLI live in ../../memory.py. Switch: memory_enabled.
Service "memory": other modules can save notes with lato.service("memory").add(user_id, name, note)."""
import asyncio

from core import config as C
from core.module import Module as Base

try:
    import memory as store
except Exception:   # pragma: no cover
    store = None

MAX_MEM_OPS = 2


class Module(Base):
    HELP = {"title": "notes", "line": "i remember things about you if you ask, and i stick to my own choices (fav colour, nicknames...) once i've picked them", "examples": ("lato remember i hate mushrooms", "lato forget that"), "who": "friends"}
    ORDER = 10
    TAGS = {"REMEMBER": (), "FORGET": (), "ME": ("MYSELF", "SELF")}

    def on(self, turn=None) -> bool:
        return bool(store and self.lato.S("memory_enabled") and (
            turn is None or turn.is_owner and turn.msg.channel.id in C.PRIVATE_CHANNELS
        ))

    async def setup(self):
        self.lato.provide("memory", self)

    def persona(self):
        if not self.on():
            return ""
        return "\n\n".join(x for x in (super().persona(), self.persona_file("persona-me.md")) if x)

    def add(self, user_id: int, name: str, note: str, by_id: int | None = None) -> str:
        return store.add(user_id, name, note, by_id) if store else "off"

    async def context(self, turn) -> str:
        if not self.on(turn):
            return ""
        msg = turn.msg
        people = [(msg.author.id, self.lato.speaker(msg), store.SHOW_SELF)]
        if turn.is_owner:  # only Owner's messages pull in notes about the people he mentions
            people += [(u.id, (getattr(u, "display_name", None) or u.name).lower(), store.SHOW_OTHER)
                       for u in msg.mentions if not u.bot and u.id != C.OWNER_ID][:2]
        try:
            notes = await asyncio.to_thread(store.block, people, turn.joined)
            me = await asyncio.to_thread(store.me_block, turn.joined)
            return "\n\n".join(x for x in (me, notes) if x)
        except Exception:
            self.log.exception("memory lookup failed; continuing without it")
            return ""

    async def on_reply(self, turn):
        if self.on(turn):
            for call in turn.tags("ME")[:MAX_MEM_OPS]:      # her own decisions: only ever from her own reply
                parsed = store.parse_me(call.arg)
                if not parsed:
                    self.log.info("ME skipped: invalid argument")
                    continue
                try:
                    res = await asyncio.to_thread(store.me_set, parsed[0], parsed[1], turn.msg.author.id)
                    self.log.info("ME %s", res)
                except Exception:
                    self.log.exception("ME save failed")
        ops = turn.tags("REMEMBER", "FORGET")
        if not ops or not self.on(turn):
            return
        msg = turn.msg
        for call in ops[:MAX_MEM_OPS]:
            op, arg = call.name, call.arg
            if not arg:
                continue
            uid, name, text = msg.author.id, self.lato.speaker(msg), arg
            ref, sep, rest = arg.partition("->")
            if sep and len(ref.strip()) <= 40:
                if not turn.is_owner:
                    continue  # friends can only add notes about themselves
                m = self.lato.find_member(msg, ref)
                if not m or m.bot:
                    self.log.info("MEMORY skipped: couldn't tell who '%s' is", ref.strip())
                    continue
                uid, text = m.id, rest
                name = C.USER_NAME if m.id == C.OWNER_ID else (m.display_name or m.name).lower()
            try:
                if op == "REMEMBER":
                    res = await asyncio.to_thread(store.add, uid, name, text, msg.author.id)
                    self.log.info("MEMORY %s", res)
                else:
                    gone = await asyncio.to_thread(store.forget, uid, text)
                    self.log.info("MEMORY forget %s", "removed" if gone else "no match")
            except Exception:
                self.log.exception("memory %s failed", op)
