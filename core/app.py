"""The Lato object: owns the Discord client, settings, history, modules and services, and wires Discord events
to the router, the pipeline and the modules."""
from __future__ import annotations

import asyncio
import importlib
import logging
import os
import time
from collections import defaultdict

import aiohttp
import discord

import botsettings
import friends

from . import config as C
from . import emotes, people, prompt
from .llm import LLM
from .module import Module
from .pipeline import Pipeline
from .router import Router
from .store import History, Store
from .tags import TagRegistry
from .tasks import spawn

log = logging.getLogger("latochan")
FAIL_LIMIT, FAIL_WINDOW_S = 3, 600     # this many errors in this window switches a module off until restart


class NullSleep:
    """Used when the sleep module isn't installed: always awake."""
    def on(self) -> bool: return False
    def is_awake(self, cid) -> bool: return True
    def wake(self, cid, seconds=None) -> None: ...
    def fall_asleep(self, cid) -> None: ...
    def take_chime_open(self, cid) -> bool: return False
    def any_awake(self) -> bool: return True
    def expire(self) -> None: ...


class LatoClient(discord.Client):
    def __init__(self, lato: Lato, **kw):
        super().__init__(**kw)
        self.lato = lato

    async def close(self) -> None:
        await self.lato.close_http()
        await super().close()

    async def setup_hook(self):
        await self.lato.setup_modules()
        from . import commands
        tree = commands.install(self.lato, self)
        self.lato.bg_sync = spawn(commands.sync(tree, self.lato.data_dir), "slash-sync")   # never delays logging in

    async def on_ready(self):
        await self.lato.on_ready()

    async def on_message(self, msg):
        await self.lato.on_message(msg)

    async def on_typing(self, channel, user, when):
        self.lato.router.on_typing(channel, user)

    async def on_raw_reaction_add(self, p):
        await self.lato.dispatch("raw_reaction_add", p)

    async def on_raw_message_delete(self, p):
        self.lato.store.delete_message(p.message_id)          # a deleted message leaves her log too
        await self.lato.dispatch("raw_message_delete", p)

    async def on_raw_message_edit(self, p):
        content = (getattr(p, "data", None) or {}).get("content")
        if content is not None:
            self.lato.store.update_message(p.message_id, content[:4000])
        await self.lato.dispatch("raw_message_edit", p)

    async def on_presence_update(self, before, after):
        await self.lato.dispatch("presence_update", before, after)

    async def on_voice_state_update(self, member, before, after):
        await self.lato.dispatch("voice_state_update", member, before, after)


class Lato:
    def __init__(self):
        self.S = botsettings.Live(str(C.SETTINGS_PATH))   # S("key") -> current validated value, re-read every ~2 s
        self.friends = friends.Live(str(C.FRIENDS_PATH))  # friends.json, re-read every ~2 s (lato-friends / LatoDash)
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = C.MEMBERS_INTENT
        intents.presences = C.PRESENCE_INTENT
        self.client = LatoClient(self, intents=intents)
        self.data_dir = C.DATA_DIR
        self._http: aiohttp.ClientSession | None = None
        self.store = Store(C.DATA_DIR / "chatlog.db", C.HISTORY_MAX_TURNS * 2)
        self.history = History(self.store, C.HISTORY_MAX_TURNS * 2, C.PRIVATE_CHANNELS)
        self.locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.last_seen: dict[int, float] = {}
        self.custom_activity = None          # the [[STATUS]] text, kept across sleep/wake
        self.tags = TagRegistry()
        self.tags.register("SKIP", "core")
        self.services: dict[str, object] = {}
        self.llm = LLM(self)
        self.modules: list[Module] = []
        self._loops_started = False
        self.load_modules()
        self.router = Router(self)
        self.pipeline = Pipeline(self)

    # ---------------------------------------------------------------- modules
    def load_modules(self) -> None:
        skip = {x.strip() for x in os.environ.get("LATO_SKIP_MODULES", "").split(",") if x.strip()}
        for folder in sorted(p for p in C.MODULES_DIR.iterdir() if (p / "module.py").exists()):
            name = folder.name
            if name in skip:
                log.info("module %s skipped (LATO_SKIP_MODULES)", name)
                continue
            try:
                mod = importlib.import_module(f"modules.{name}.module")
                cls = mod.Module
                cls.NAME = name
                inst = cls(self, folder)
                for tag, aliases in cls.TAGS.items():
                    self.tags.register(tag, name, aliases)
                self.modules.append(inst)
            except Exception:
                log.exception("module %s failed to load; carrying on without it", name)
        self.modules.sort(key=lambda m: (m.ORDER, m.NAME))
        log.info("modules: %s", ", ".join(m.NAME for m in self.modules) or "none")

    def module(self, name: str) -> Module | None:
        return next((m for m in self.modules if name == m.NAME), None)

    def active(self) -> list[Module]:
        return [m for m in self.modules if m.enabled()]

    def is_friend(self, uid: int) -> bool:
        return uid in self.friends.ids()

    def is_trusted(self, uid: int) -> bool:
        """A friend marked trusted (the owner is handled separately: he's always allowed everything)."""
        return uid in self.friends.trusted_ids()

    def is_known(self, uid: int) -> bool:
        """Owner or on the friends list (everyone else is a stranger)."""
        return uid == C.OWNER_ID or self.is_friend(uid)

    def provide(self, name: str, obj) -> None:
        self.services[name] = obj

    def service(self, name: str, default=None):
        return self.services.get(name, default)

    @property
    def sleep(self):
        return self.services.get("sleep") or NullSleep()

    def _failed(self, m: Module, hook: str) -> None:
        now = time.monotonic()
        m.fail_times = [t for t in m.fail_times if now - t < FAIL_WINDOW_S] + [now]
        log.exception("module %s: %s failed (%d errors in the last 10 min)", m.NAME, hook, len(m.fail_times))
        if len(m.fail_times) >= FAIL_LIMIT:
            m.broken = True
            log.error("module %s disabled until restart after repeated errors", m.NAME)

    async def run_hook(self, hook: str, *args):
        for m in self.active():
            try:
                await getattr(m, hook)(*args)
            except Exception:
                self._failed(m, hook)

    def run_sync_hook(self, hook: str, value, *args):
        """For hooks that transform a value (clean_text): value = hook(*args, value)."""
        for m in self.active():
            try:
                value = getattr(m, hook)(*args, value)
            except Exception:
                self._failed(m, hook)
        return value

    async def contexts(self, turn) -> list[str]:
        out = []
        for m in self.active():
            try:
                out.append(await m.context(turn) or "")
            except Exception:
                self._failed(m, "context")
        return out

    def ignored(self, msg) -> bool:
        for m in self.active():
            try:
                if m.ignore(msg):
                    return True
            except Exception:
                self._failed(m, "ignore")
        return False

    async def dispatch(self, event: str, *args) -> None:
        for m in self.active():
            fn = getattr(m, f"ev_{event}", None)
            if fn:
                try:
                    await fn(*args)
                except Exception:
                    self._failed(m, f"ev_{event}")

    # ---------------------------------------------------------------- shared services for modules
    def setting(self, key: str, default=None):
        try:
            return self.S(key)
        except KeyError:
            return default

    def set_setting(self, key: str, value) -> None:
        try:
            self.S.set(key, value)
        except Exception:
            log.exception("couldn't save setting %s", key)

    def http(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        return self._http

    async def close_http(self) -> None:
        if self._http is not None and not self._http.closed:
            await self._http.close()

    def allowed_mentions(self) -> discord.AllowedMentions:
        svc = self.services.get("mentions")
        users = bool(svc and svc.allow_users())
        return discord.AllowedMentions(everyone=False, roles=False, users=users, replied_user=False)

    def module_snippets(self) -> list[tuple[str, str]]:
        snippets = []
        for m in self.active():
            try:
                snippets.append((m.NAME, m.persona()))
            except Exception:
                self._failed(m, "persona")
        return snippets

    def prompt_parts(self, extra: str = "") -> list[tuple[str, str]]:
        """[(name, text)] that make up her system prompt right now (for lato-prompt / LatoDash)."""
        return prompt.parts(self.module_snippets(), extra)

    def system_prompt(self, extra: str = "") -> str:
        return prompt.join(self.prompt_parts(extra))

    turn_context = staticmethod(prompt.turn_context)

    def recent_history(self, cid: int) -> list:
        return self.history.recent(cid, int(self.S("history_turns")))

    def speaker(self, msg) -> str:
        return people.speaker(msg)

    def who(self, m) -> str:
        return people.who(self.client, m)

    def clean_content(self, msg) -> str:
        return people.clean_content(self.client, msg)

    find_member = staticmethod(people.find_member)

    def presence_status(self):
        sl = self.sleep
        if not sl.on():
            return discord.Status.online
        return discord.Status.online if sl.any_awake() else discord.Status.idle

    async def channel(self, cid: int):
        return self.client.get_channel(cid) or await self.client.fetch_channel(cid)

    async def say(self, channel, text: str):
        """Post in a channel with her mention rules."""
        return await channel.send(emotes.expand(text)[:1900], allowed_mentions=self.allowed_mentions())

    # ---------------------------------------------------------------- events
    async def on_message(self, msg) -> None:
        if msg.guild and msg.channel.id in C.PRIVATE_CHANNELS and msg.content:
            self.store.log_message(msg.id, msg.channel.id, msg.author.id, self.speaker(msg), msg.content[:4000],
                                   int(msg.created_at.timestamp()))
        await self.dispatch("message", msg)
        await self.router.handle(msg)

    async def on_ready(self) -> None:
        log.info("LatoChan v%s logged in as %s, model=%s, modules on: %s", C.VERSION, self.client.user, C.MODEL,
                 ", ".join(m.NAME for m in self.active()))
        if not self._loops_started:
            self._loops_started = True
            spawn(self.presence_loop(), "presence")
            spawn(self.friend_names_loop(), "friend-names")
            for m in self.modules:
                if type(m).loop is not Module.loop:
                    spawn(self._run_loop(m), f"loop-{m.NAME}")
        await self.dispatch("ready")
        # Catch up: answer the owner's latest main-channel message if nobody answered it in the last 30 min
        for cid in C.MAIN_CHANNELS:
            try:
                ch = await self.channel(cid)
                async for m in ch.history(limit=10):
                    if m.author.id == self.client.user.id:
                        break  # bot already spoke after it
                    if m.author.id == C.OWNER_ID:
                        age = (discord.utils.utcnow() - m.created_at).total_seconds()
                        if age < 1800:
                            log.info("catching up on message %s", m.id)
                            await self.router.handle(m)
                        break
            except Exception:
                log.exception("catch-up failed for channel %s", cid)

    async def _run_loop(self, m: Module) -> None:
        while True:
            try:
                await m.loop()
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("module %s: loop crashed; restarting it in 60 s", m.NAME)
                await asyncio.sleep(60)

    async def friend_names_loop(self) -> None:
        """Friends added by ID only get their Discord display name filled in (for lato-friends / LatoDash)."""
        while True:
            try:
                missing = [f["id"] for f in self.friends.all() if not f["name"]]
                names = {}
                for fid in missing:
                    for g in self.client.guilds:
                        m = g.get_member(int(fid))
                        if m:
                            names[fid] = (m.display_name or m.name)
                            break
                if names:
                    await asyncio.to_thread(friends.fill_names, names, str(C.FRIENDS_PATH))
                    log.info("friends: filled in names for %s", ", ".join(names.values()))
            except Exception:
                log.exception("friend name lookup failed")
            await asyncio.sleep(600)

    async def presence_loop(self) -> None:
        """Shows her as online while awake somewhere, idle (moon) while asleep; logs each change."""
        shown = None
        while True:
            st = self.presence_status()
            if st != shown:
                try:
                    await self.client.change_presence(status=st, activity=self.custom_activity)
                    log.info("SLEEP %s", "woke up" if st == discord.Status.online else "fell asleep")
                    shown = st
                except Exception:
                    log.exception("presence update failed")
            self.sleep.expire()
            await asyncio.sleep(5)

    def run(self) -> None:
        if not C.OWNER_ID or not C.MAIN_CHANNELS or not os.environ.get("DISCORD_BOT_TOKEN"):
            raise SystemExit("Set OWNER_ID, CHANNEL_IDS and DISCORD_BOT_TOKEN before starting the bot.")
        self.client.run(os.environ["DISCORD_BOT_TOKEN"], log_handler=None)

    async def setup_modules(self) -> None:
        for m in self.modules:
            try:
                await m.setup()
            except Exception:
                log.exception("module %s setup failed; disabling it", m.NAME)
                m.broken = True
