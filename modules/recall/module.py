"""Long-term memory (memory v2, roadmap #7): she looks back over older conversations and daily summaries.

* Indexer (background, every 2 min): new messages from chatlog.db in the recall channels (her main channels) are cut
  into conversation chunks (new chunk after a 30-min gap, 15 messages or ~1,200 characters; a chunk is only indexed
  once the conversation has gone quiet), embedded by the configured local model (nomic-embed-text) and stored in recall.db.
* Nightly summaries ("episodes"): at recall_episode_hour she summarises yesterday in each recall channel in 3-6 lines.
  Kept forever; raw chat chunks expire with the 180-day message log.
* Each reply: if the message is worth it (not a stranger, >= recall_min_words), search recall.db (vector + keyword)
  inside the allowed scopes only and add the best hits, dated, to the turn (recall_budget characters).
* Privacy: only the recall channels are indexed; search filters by scope in SQL; deleted/edited Discord messages are
  removed/fixed in the log and their chunks rebuilt; [[FORGET: ...]] also drops the asker's own matching messages.
"""
from __future__ import annotations

import asyncio
import datetime
import os
import re
import time
from zoneinfo import ZoneInfo

import aiohttp

from core import config as C
from core.module import Module as Base

from .db import RecallDB

TZ = ZoneInfo(C.TIMEZONE)
GAP_S, MAX_MSGS, MAX_CHARS = 30 * 60, 15, 1200
KEEP_CHAT_DAYS = 180
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")
EMBED_URL = os.environ.get("EMBED_URL", C.OLLAMA_URL)
STOP = set("the and but for are was were you your what when where who how why did does this that with have has had "
           "lato latochan just like its it's dont can will would about from they them there then than".split())
SUMMARY_PROMPT = ("You write the memory notes of a Discord bot called {bot}. Summarise this chat log from one day in a "
                  "channel in 3-6 short lines, third person, everyone's names in lowercase. Keep facts, decisions, plans, "
                  "opinions, promises, running jokes and what {bot} said or decided. Skip greetings and filler. No "
                  "guessing, nothing that isn't in the log.")


def chunk_rows(rows: list[tuple], now: int) -> tuple[list[list[tuple]], list[tuple]]:
    """Split (id, author_id, author, content, ts) rows into closed chunks + the still-open tail."""
    chunks, cur, chars = [], [], 0
    for r in rows:
        if cur and (r[4] - cur[-1][4] > GAP_S or len(cur) >= MAX_MSGS or chars + len(r[3] or "") > MAX_CHARS):
            chunks.append(cur)
            cur, chars = [], 0
        cur.append(r)
        chars += len(r[3] or "") + len(r[2] or "") + 2
    if cur and now - cur[-1][4] > GAP_S:
        chunks.append(cur)
        cur = []
    return chunks, cur


def chunk_text(rows: list[tuple]) -> str:
    return "\n".join(f"{(r[2] or 'someone').lower()}: {r[3]}" for r in rows if (r[3] or "").strip())


NAME_RE = re.compile(r"<@!?\d+>|@\w+|\b(lato|latochan|lato-chan|" + re.escape(C.BOT_NAME) + r")\b[,:!?]*", re.I)


def clean_query(text: str) -> str:
    """Her own name is in almost every chunk, so it drags unrelated chat closer (measured: ~0.12 cosine distance).
    Search on what they're asking about, without her name or @mentions."""
    return re.sub(r"\s+", " ", NAME_RE.sub(" ", text or "")).strip()


def _date_long(ts: float) -> str:
    """'4 Nov 2026'. (strftime's %-d only exists on Linux; this also works on the Windows PC.)"""
    d = datetime.datetime.fromtimestamp(ts, TZ)
    return f"{d.day} {d:%b %Y}"


def ago(ts: int, now: float) -> str:
    d = (datetime.datetime.fromtimestamp(now, TZ).date() - datetime.datetime.fromtimestamp(ts, TZ).date()).days
    if d <= 0:
        return "earlier today"
    if d == 1:
        return "yesterday"
    if d < 14:
        return f"{d} days ago"
    return _date_long(ts)


class Module(Base):
    SWITCH = "mod_recall"
    ORDER = 22             # after notes (10) and rag (20)
    HELP = {"title": "long-term memory", "line": "i remember older conversations in the main channel (with a nightly summary of each day), so you can ask what was said; say 'forget that' and it goes",
            "examples": ("lato what did alice say about chess last week?", "lato what did we decide about the lamp?"),
            "who": "friends"}

    async def setup(self):
        self.db = RecallDB(self.lato.data_dir / "recall.db")
        self.last_error = ""
        self.embed_fail = 0
        self.lato.provide("recall", self)

    def channels(self) -> list[int]:
        return sorted(C.MAIN_CHANNELS & C.PRIVATE_CHANNELS)

    def status(self) -> str:
        if not self.enabled():
            return "off"
        n = sum(g["n"] for g in self.db.stats()["groups"])
        return f"on, {n} chunks" + ("" if self.db.vec_ok else " (sqlite-vec missing: keyword search only)") + \
               (f", last error: {self.last_error}" if self.last_error else "")

    # ---- embeddings
    async def embed(self, texts: list[str], timeout: float = 120) -> list[list[float]] | None:
        for url in dict.fromkeys((EMBED_URL, C.OLLAMA_URL)):
            try:
                async with self.lato.http().post(f"{url}/api/embed", json={"model": EMBED_MODEL, "input": texts, "keep_alive": "24h"},
                                                 timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                    r.raise_for_status()
                    return (await r.json())["embeddings"]
            except Exception as e:
                self.last_error = f"embedding failed ({type(e).__name__})"
        return None

    # ---- indexing
    async def index_channel(self, cid: int, now: int | None = None) -> int:
        now = int(now or time.time())
        pos = self.db.get(f"chan:{cid}", {"ts": 0, "id": 0})
        rows = self.lato.store.messages_after(cid, pos["ts"], pos["id"], limit=1500)
        closed, _open = chunk_rows(rows, now)
        done = 0
        for ch in closed:
            text = chunk_text(ch)
            if len(text) >= 40:
                vecs = await self.embed(["search_document: " + text])
                if vecs is None:
                    self.embed_fail += 1
                    if self.embed_fail < 10:
                        return done           # try again next round, nothing skipped
                vec = vecs[0] if vecs else None
                self.embed_fail = 0 if vec else self.embed_fail
                self.db.add("chat", f"channel:{cid}", text, ch[0][4], ch[-1][4], channel_id=cid,
                            people=[r[1] for r in ch], msg_ids=[r[0] for r in ch], vec=vec, model=EMBED_MODEL)
                done += 1
            self.db.put(f"chan:{cid}", {"ts": ch[-1][4], "id": ch[-1][0]})
        return done

    async def rebuild_for(self, msg_id: int) -> None:
        """A message was deleted or edited: rebuild the chunks that held it from what's left in the log."""
        for ch in self.db.chunks_with_msg(msg_id):
            if ch["kind"] != "chat":
                continue
            rows = self.lato.store.messages_by_ids(ch["msg_ids"])
            self.db.delete(ch["id"])
            text = chunk_text(rows)
            if len(text) >= 40:
                vecs = await self.embed(["search_document: " + text])
                self.db.add("chat", ch["scope"], text, rows[0][4], rows[-1][4], channel_id=ch["channel_id"],
                            people=[r[1] for r in rows], msg_ids=[r[0] for r in rows],
                            vec=vecs[0] if vecs else None, model=EMBED_MODEL)

    async def ev_raw_message_delete(self, p):
        await self.rebuild_for(p.message_id)

    async def ev_raw_message_edit(self, p):
        await self.rebuild_for(p.message_id)

    # ---- nightly summaries
    async def write_episode(self, cid: int, day: datetime.date) -> str | None:
        key = f"episode:{cid}:{day.isoformat()}"
        if self.db.get(key):
            return None
        t0 = int(datetime.datetime(day.year, day.month, day.day, tzinfo=TZ).timestamp())
        rows = [r for r in self.lato.store.messages_between(cid, t0, t0 + 86400) if (r[3] or "").strip()]
        if len(rows) < 8:
            self.db.put(key, "skipped")
            return None
        log_text = chunk_text(rows)[-14000:]
        msgs = [{"role": "system", "content": SUMMARY_PROMPT.format(bot=C.BOT_NAME)},
                {"role": "user", "content": f"chat log, {self.day_name(day)}:\n{log_text}"}]
        reply, _ = await self.lato.llm.chat(msgs, think=False)
        summary = re.sub(r"\[\[[^\]]*\]\]", "", reply or "").strip()
        if not summary:
            return None
        text = f"{self.day_name(day)} in #{self.channel_name(cid)}:\n{summary}"
        vecs = await self.embed(["search_document: " + text])
        self.db.add("episode", f"channel:{cid}", text, t0, t0 + 86399, channel_id=cid,
                    people=sorted({r[1] for r in rows}), vec=vecs[0] if vecs else None, model=EMBED_MODEL)
        self.db.put(key, "done")
        self.log.info("RECALL episode for %s: %d messages -> %d chars", day, len(rows), len(summary))
        return text

    @staticmethod
    def day_name(day: datetime.date) -> str:
        """'Wednesday 4 November 2026' (no %-d, so it also runs on Windows)."""
        return f"{day:%A} {day.day} {day:%B %Y}"

    def channel_name(self, cid: int) -> str:
        ch = self.lato.client.get_channel(cid) if self.lato.client else None
        return getattr(ch, "name", None) or str(cid)

    async def loop(self):
        await asyncio.sleep(30)
        last_prune = 0.0
        while True:
            if self.enabled():
                try:
                    for cid in self.channels():
                        await self.index_channel(cid)
                    now = datetime.datetime.now(TZ)
                    if self.lato.S("recall_episodes") and now.hour >= self.lato.S("recall_episode_hour"):
                        for back in (3, 2, 1):            # catch up a couple of missed nights too
                            for cid in self.channels():
                                await self.write_episode(cid, now.date() - datetime.timedelta(days=back))
                    if time.time() - last_prune > 86400:
                        self.db.prune("chat", int(time.time()) - KEEP_CHAT_DAYS * 86400)
                        last_prune = time.time()
                    self.last_error = "" if self.embed_fail == 0 else self.last_error
                except Exception as e:
                    self.last_error = f"{type(e).__name__}: {str(e)[:80]}"
                    self.log.exception("recall loop")
            await asyncio.sleep(120)

    # ---- recalling
    def worth_it(self, turn) -> bool:
        words = [w for w in re.findall(r"[a-z0-9']+", (turn.joined or "").lower()) if w not in STOP]
        return (not turn.stranger) and turn.mode == "reply" and len(words) >= self.lato.S("recall_min_words")

    async def context(self, turn) -> str:
        cid = turn.msg.channel.id
        if cid not in self.channels() or not self.worth_it(turn):
            return ""
        S, now = self.lato.S, time.time()
        q = clean_query(turn.joined[-600:])
        if not q:
            return ""
        vecs = await self.embed(["search_query: " + q], timeout=4)
        qvec = vecs[0] if vecs else None
        hits = await asyncio.to_thread(self.db.search, [f"channel:{cid}"], q, qvec, k=8,
                                       before=int(now - S("recall_skip_recent_min") * 60),
                                       max_distance=S("recall_max_distance") / 100)
        if qvec is not None and self.db.vec_ok:   # keyword-only hits are too loose when a vector can judge them
            hits = [h for h in hits if h["distance"] is not None]
        if not hits:
            return ""
        hits.sort(key=lambda h: (h["kind"] != "episode", -h["score"]))
        out, used = [], 0
        for h in hits:
            body = h["text"] if h["kind"] == "episode" else h["text"][-700:]
            line = f"- ({'summary, ' if h['kind'] == 'episode' else ''}{ago(h['t_end'], now)}) " + body.replace("\n", " / ")
            if used + len(line) > S("recall_budget"):
                break
            out.append(line)
            used += len(line)
        if not out:
            return ""
        self.log.info("RECALL %d hits for %s (%d chars)", len(out), self.lato.speaker(turn.msg), used)
        return ("(from older conversations, may help. use only if relevant, don't quote it word for word or say you "
                "looked it up:\n" + "\n".join(out) + ")")

    # ---- forgetting
    async def on_reply(self, turn):
        for call in turn.tags("FORGET")[:2]:
            n = await self.forget_own(turn.msg.author.id, call.arg)
            if n:
                self.log.info("RECALL forgot %d of %s's messages matching '%s'", n, self.lato.speaker(turn.msg), call.arg)

    async def forget_own(self, author_id: int, text: str) -> int:
        """Drop this person's own logged messages that contain all the key words, then rebuild their chunks."""
        words = [w for w in re.findall(r"[a-z0-9']{3,}", (text or "").lower()) if w not in STOP]
        if not words:
            return 0
        n = 0
        for cid in self.channels():
            for r in self.lato.store.messages_by_author(cid, author_id):      # SQL does the filtering, not Python
                if all(w in (r[3] or "").lower() for w in words):
                    self.lato.store.delete_message(r[0])
                    await self.rebuild_for(r[0])
                    n += 1
        return n
