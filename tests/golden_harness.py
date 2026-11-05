"""Golden behaviour test: runs fixed scenarios through LatoChan's reply pipeline with a fake model and fake
Discord objects, and records everything she does (messages, reactions, notes, hand-offs, history, prompt).

The same scenarios run against any implementation through a small adapter (see adapter_*.py), so a refactor
can be checked against the snapshot taken from the old code:  python tests/golden.py [--update]
"""
import asyncio
import contextlib
import re
import types

OWNER, FRIEND, STRANGER = (10**17 + 2), (10**17 + 1), 999
MAIN, OTHER = 100, 200

events: list = []          # everything observable, in order


class Chan:
    def __init__(self, cid):
        self.id = cid
        self.name = "general" if cid == MAIN else "other"

    @contextlib.asynccontextmanager
    async def typing(self):
        yield

    async def send(self, text, **kw):
        events.append(["send", self.id, text, _mentions(kw)])

    async def history(self, limit=10, before=None):   # pragma: no cover - async generator stub
        if False:
            yield None


def _mentions(kw):
    am = kw.get("allowed_mentions")
    return None if am is None else [am.everyone, am.roles, am.users if isinstance(am.users, bool) else "list"]


_next_id = [1000]


def mk(author=OWNER, text="hi", chan=MAIN, bot=False, name="x"):
    _next_id[0] += 1
    ch = Chan(chan)
    mid = _next_id[0]

    async def reply(text, **kw):
        events.append(["reply", mid, text, _mentions(kw)])

    async def add_reaction(e):
        events.append(["react", mid, str(e)])

    return types.SimpleNamespace(
        id=mid, content=text, attachments=[], stickers=[], embeds=[], reactions=[], mentions=[], reference=None,
        guild=None, author=types.SimpleNamespace(id=author, bot=bot, display_name=name, name=name, voice=None),
        channel=ch, reply=reply, add_reaction=add_reaction)


NOW = re.compile(r"right now it is [^\n.]+")


def norm_prompt(p: str) -> str:
    return NOW.sub("<NOW>", p)


# ---- scenarios: (name, settings, author, reason, text, canned model reply, extra) ----
BASE = {"gif_chance": 100, "wake_gif_chance": 0, "gif_reply_chance": 0, "gif_only_chance": 0, "sleep_mode": True,
        "chatter_reply_chance": 100, "memory_enabled": True}
RESPOND = [
    ("plain", {}, OWNER, "main", "hey", "Hello THERE owner", {}),
    ("label strip", {}, OWNER, "main", "hey", "latochan: owner: hi", {}),
    ("code kept", {}, OWNER, "main", "code?", "Use `MyFunc()` NOW https://Example.com/AbC", {}),
    ("react only", {}, OWNER, "main", "lol", "[[REACT: 😂]]", {}),
    ("react alias", {}, OWNER, "main", "lol", "ok [[EMOJI: 💀]]", {}),
    ("bracket react", {}, OWNER, "main", "lol", "haha [reacted 😭]", {}),
    ("skip", {}, OWNER, "main", "ok", "[[SKIP]]", {}),
    ("skip+react", {}, OWNER, "main", "ok", "[[SKIP]] [[REACT: 👍]]", {}),
    ("unknown tag", {}, OWNER, "main", "hi", "hi [[DANCE: wildly]] there", {}),
    ("remember self", {}, FRIEND, "pinged", "i love cats", "cute\n[[REMEMBER: loves cats]]", {}),
    ("remember long", {}, FRIEND, "pinged", "about me", "noted\n[[REMEMBER: prefers long books with short chapters and likes to read them in the evening]]", {}),
    ("forget", {}, FRIEND, "pinged", "forget cats", "fine\n[[FORGET: cats]]", {}),
    ("remember stranger", {}, STRANGER, "pinged", "i like dogs", "who are you\n[[REMEMBER: likes dogs]]", {}),
    ("memory off", {"memory_enabled": False}, FRIEND, "pinged", "i like tea", "ok\n[[REMEMBER: likes tea]]", {}),
    ("think toggle", {"thinking": False}, OWNER, "main", "toggle your thinking", "fine [[TOGGLE_THINKING]]", {}),
    ("think no intent", {"thinking": False}, OWNER, "main", "hello", "nah [[TOGGLE_THINKING]]", {}),
    ("mute by friend", {}, FRIEND, "pinged", "mute dana", "ok [[MUTE: @dana]]", {}),
    ("mute no guild", {}, OWNER, "main", "mute dana", "ok [[MUTE: @dana]]", {}),
    ("powers off", {"server_powers": False}, OWNER, "main", "lol", "[[REACT: 😂]] ha", {}),
    ("sleep cmd", {}, OWNER, "sleep_cmd", "lato go to sleep", "night\n[[GIF: sleepy]]", {}),
    ("name note", {}, OWNER, "name", "lato is weird", "rude", {}),
    ("awake note", {}, FRIEND, "awake", "anyone up", "me", {}),
    ("woke note", {}, OWNER, "woke", "lato wake up", "-_- what", {}),
    ("gif after text", {}, OWNER, "main", "yay", "nice\n[[GIF: happy]]", {}),
    ("wake gif first", {"wake_gif_chance": 100}, OWNER, "woke", "lato", "what", {}),
    ("gif empty mood", {}, OWNER, "main", "meh", "meh\n[[GIF: bored]]", {}),
    ("gif stranger", {}, STRANGER, "pinged", "hi", "who\n[[GIF: happy]]", {}),
    ("gif mode", {"gif_only": True}, OWNER, "main", "hi", "words\n[[GIF: happy]]", {}),
    ("gifs off", {"gifs_enabled": False}, OWNER, "main", "hi", "words\n[[GIF: happy]]", {}),
    ("stranger prompt", {}, STRANGER, "pinged", "help me", "no", {}),
    ("lowercase", {}, OWNER, "main", "x", f'WHY Are You SHOUTING <:proud:{10**17 + 3}>', {}),
]

ROUTE = [
    # (name, settings, awake?, author, text, chan)
    ("asleep owner chatter", {}, False, OWNER, "hey", MAIN),
    ("asleep owner named", {}, False, OWNER, "lato wake up", MAIN),
    ("asleep owner sleepcmd", {}, False, OWNER, "lato go to sleep", MAIN),
    ("asleep friend named", {}, False, FRIEND, "latochan hi", MAIN),
    ("asleep friend named, friends can't wake", {"friends_can_wake": False}, False, FRIEND, "lato hi", MAIN),
    ("asleep stranger named", {}, False, STRANGER, "lato hi", MAIN),
    ("awake owner main", {}, True, OWNER, "hey", MAIN),
    ("awake owner other chan", {}, True, OWNER, "hey", OTHER),
    ("awake owner sleepcmd", {}, True, OWNER, "lato go to sleep", MAIN),
    ("awake friend chatter", {}, True, FRIEND, "anyone up", MAIN),
    ("awake friend chatter 0%", {"chatter_reply_chance": 0}, True, FRIEND, "anyone up", MAIN),
    ("awake friend named", {}, True, FRIEND, "lato is cool", MAIN),
    ("awake stranger chatter", {}, True, STRANGER, "yo", MAIN),
    ("sleep off owner main", {"sleep_mode": False}, False, OWNER, "hey", MAIN),
    ("sleep off friend chatter", {"sleep_mode": False}, False, FRIEND, "yo", MAIN),
    ("bare link", {}, True, OWNER, "https://x.com/a/b", MAIN),
    ("bot author", {}, True, OWNER, "hey", MAIN, True),
    ("empty", {}, True, OWNER, "   ", MAIN),
]


async def run_all(api) -> dict:
    """api: adapter with reset(), settings(dict), set_awake(cid, bool), respond(msgs, reason),
    route(msg) -> reason|None, history(cid), memory_dump(), prompts (list), is_awake(cid), thinking()"""
    out = {"respond": {}, "route": {}}
    for name, st, author, reason, text, reply, extra in RESPOND:
        api.reset()
        api.settings({**BASE, **st})
        events.clear()
        api.prompts.clear()
        chan = extra.get("chan", MAIN)
        api.set_awake(chan, reason != "woke")
        api.canned = reply
        await api.respond([mk(author, text, chan)], reason)
        for _ in range(5):
            await asyncio.sleep(0)
        out["respond"][name] = {
            "events": [e[:1] + e[2:] for e in events],   # drop message ids
            "history": api.history(chan),
            "memory": re.sub(r"#snapshot [0-9a-f]{64}", "#snapshot HASH", api.memory_dump()),
            "prompt": norm_prompt(api.prompts[-1]) if api.prompts else None,
            "awake_after": api.is_awake(chan),
            "thinking": api.thinking(),
        }
    for row in ROUTE:
        name, st, awake, author, text, chan = row[:6]
        bot = row[6] if len(row) > 6 else False
        api.reset()
        api.settings({**BASE, **st})
        api.set_awake(chan, awake)
        r = await api.route(mk(author, text, chan, bot=bot))
        out["route"][name] = {"reason": r, "awake_after": api.is_awake(chan)}
    return out
