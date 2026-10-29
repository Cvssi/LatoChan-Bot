"""Runs the real respond() with a fake model + fake Discord objects. Checks cooldown, gif mode, percentages."""
import asyncio, os, sys, tempfile, time, types, contextlib, json, random
from pathlib import Path
os.environ.update(DISCORD_BOT_TOKEN="x", OWNER_ID="1", SLEEP_MODE="1", FRIEND_IDS="200", CHANNEL_IDS="100")
_scratch = tempfile.TemporaryDirectory()
SET = str(Path(_scratch.name) / "settings.json"); os.environ["SETTINGS_PATH"] = SET
os.environ["RAG_ENABLED"] = "0"
os.environ["PRIVATE_CHANNEL_IDS"] = ""
random.seed(20261029)
gifdir = Path(_scratch.name) / "gifs"; gifdir.mkdir(); os.environ["GIF_DIR"] = str(gifdir)
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else ".")
import gateway as g
for mood in ("annoyed", "happy", "waking", "sleepy"):
    (gifdir / mood).mkdir(); (gifdir / mood / "links.txt").write_text(f"https://tenor.com/{mood}-1\nhttps://tenor.com/{mood}-2\n")
(gifdir / "bored").mkdir(); (gifdir / "bored" / "links.txt").write_text("# empty\n")

OWNER, FRIEND, STRANGER = 1, 200, 999
sent, prompts, canned = [], [], {"r": "ok sure"}

class Chan:
    def __init__(s, cid=100): s.id = cid; s.name = "general"
    @contextlib.asynccontextmanager
    async def typing(s): yield
    async def send(s, text, **kw): sent.append(("send", text))
def mk(author=OWNER, text="hi", chan=100):
    ch = Chan(chan)
    async def reply(text, **kw): sent.append(("reply", text))
    return types.SimpleNamespace(id=1, content=text, attachments=[], stickers=[], embeds=[], reactions=[], mentions=[], reference=None,
        author=types.SimpleNamespace(id=author, bot=False, display_name="x", name="x"), channel=ch, reply=reply)
async def fake_llm(messages, think=False):
    prompts.append(messages[-1]["content"]); return canned["r"], ""
g.ask_llm = fake_llm

def settings(**kw):
    Path(SET).write_text(json.dumps(kw)); g.S.refresh(force=True)

async def run(author=OWNER, reason="main", chan=100, text="hi"):
    sent.clear(); prompts.clear()
    g.awake_until[chan] = time.monotonic() + 600
    await g.respond([mk(author, text, chan)], reason)
    return list(sent)

async def main():
    ok = 0
    def check(name, got, want=True):
        nonlocal ok
        assert got == want, f"{name}: got {got!r}, want {want!r}"; ok += 1; print("  ok -", name)

    # --- the bug: cooldown after the wake gif blocked every later gif
    settings(gif_cooldown_seconds=225)
    canned["r"] = "-_- what\n\n[[GIF: waking]]\n\nits 1am"
    g.awake_until.pop(100, None)
    r = await run(reason="woke")
    check("wake: gif then text", [k for k, _ in r] == ["send", "reply"] and r[0][1].startswith("https://tenor.com/waking"))
    canned["r"] = "this is why\n[[GIF: annoyed]]"
    r = await run()
    check("owner asks right after wake gif: gif still goes out (cooldown bypassed for owner)", any(t.startswith("https://tenor.com/annoyed") for _, t in r))
    r = await run(author=FRIEND, reason="pinged")
    check("friend right after: cooldown applies, text only", [k for k, _ in r] == ["reply"])
    g.last_gif.clear()
    r = await run(author=FRIEND, reason="pinged")
    check("friend, cooldown over: gif posted", any(t.startswith("https://tenor.com/annoyed") for _, t in r))

    # --- empty mood folder: honest log, text still sent
    settings()
    canned["r"] = "meh\n[[GIF: bored]]"
    r = await run()
    check("empty folder: text only", [k for k, _ in r] == ["reply"])

    # --- gif mode: only gifs
    settings(gif_only=True)
    canned["r"] = "blah blah\n[[GIF: happy]]"
    r = await run()
    check("gif mode: only the gif, no text", [k for k, _ in r] == ["send"] and "happy" in r[0][1])
    check("gif mode: model was told to answer with a gif only", "gif only" in prompts[0] or "gif mode" in prompts[0])
    canned["r"] = "no mood given"
    r = await run()
    check("gif mode: model forgot mood -> random mood, still gif only", [k for k, _ in r] == ["send"] and "bored" not in r[0][1] and "waking" not in r[0][1])
    settings(gif_only=True, gif_cooldown_seconds=225)
    r = await run(author=FRIEND, reason="pinged")
    r2 = await run(author=FRIEND, reason="pinged")
    check("gif mode ignores cooldown for friends too", [k for k, _ in r] == ["send"] and [k for k, _ in r2] == ["send"])
    canned["r"] = "-_- up\n[[GIF: waking]]"
    g.awake_until.pop(100, None)
    r = await run(reason="woke")
    check("gif mode + wake: just the waking gif", [k for k, _ in r] == ["send"] and "waking" in r[0][1])
    r = await run(author=STRANGER, reason="pinged")
    check("gif mode: strangers still get text only, no gif", all(not t.startswith("https://") for _, t in r))
    check("stranger got no gif nudge", "gif mode" not in prompts[0])

    # --- percentage slider
    settings(gif_reply_chance=100)
    canned["r"] = "just text"
    r = await run()
    check("100%: nudged + gif added even when she named no mood", any(t.startswith("https://") for _, t in r) and any(k == "reply" for k, _ in r))
    check("100%: model nudged", "[[GIF:" in prompts[0])
    settings(gif_reply_chance=0)
    r = await run()
    check("0%: plain text, no nudge", [k for k, _ in r] == ["reply"] and "[[GIF:" not in prompts[0])
    settings(gif_reply_chance=50)
    n = 0
    for _ in range(400):
        r = await run(); n += any(t.startswith("https://") for _, t in r)
    check(f"50% slider gives ~50% ({n}/400)", 140 < n < 260)
    settings(gif_reply_chance=100, gif_only_chance=50)
    n = 0
    for _ in range(400):
        r = await run(); n += ([k for k, _ in r] == ["send"])
    check(f"gif-only share ~50% ({n}/400)", 140 < n < 260)

    # --- master switch
    settings(gifs_enabled=False, gif_only=True)
    canned["r"] = "hello\n[[GIF: happy]]"
    r = await run()
    check("gifs disabled beats gif mode: text only", [k for k, _ in r] == ["reply"])
    print(f"ALL {ok} PASSED")
try:
    asyncio.run(main())
finally:
    _scratch.cleanup()
