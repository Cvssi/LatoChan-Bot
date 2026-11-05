"""Long-term memory (modules/recall): chunking, scope privacy, recall in the prompt, deletes/edits/forget,
nightly summaries, keyword fallback. Embeddings are faked (hashed bag of words) so this runs offline."""
import asyncio
import datetime
import hashlib
import json
import math
import time
import types
import unittest

import common
from test_core import fake_msg

OWNER, FRIEND, Alice, STRANGER = (10**17 + 2), (10**17 + 1), 777, 4242
MAIN, OTHER = 100, 555


def fake_vec(text):
    v = [0.0] * 768
    for w in text.lower().replace("search_document:", "").replace("search_query:", "").split():
        w = w.strip(".,!?:")
        if len(w) > 2:
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 768] += 1
    n = math.sqrt(sum(x * x for x in v)) or 1
    return [x / n for x in v]


class Recall(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from core.app import Lato
        cls.lato = Lato()
        asyncio.run(cls.lato.setup_modules())
        cls.mod = cls.lato.module("recall")
        cls.embed_ok = True

        async def embed(texts, timeout=120):
            return [fake_vec(t) for t in texts] if cls.embed_ok else None
        cls.mod.embed = embed

    def settings(self, **kw):
        common.TMP.joinpath("settings.json").write_text(json.dumps({"mod_recall": True, "sleep_mode": False, "mod_pings": False,
                                                                    "recall_max_distance": 90, **kw}))
        self.lato.S.refresh(force=True)

    def setUp(self):
        self.settings()
        type(self).embed_ok = True
        self.lato.history.clear()
        db = self.mod.db
        for (cid,) in db._q("SELECT id FROM chunks"):
            db.delete(cid)
        db._q("DELETE FROM state")
        self.lato.store._exec("DELETE FROM messages")
        self.mid = 1000

    def log(self, cid, author, name, text, ts):
        self.mid += 1
        self.lato.store.log_message(self.mid, cid, author, name, text, int(ts))
        return self.mid

    def old_convo(self, cid=MAIN, days=3):
        t = time.time() - days * 86400
        self.log(cid, Alice, "alice", "chess is at 8 on friday, bring snacks", t)
        self.log(cid, OWNER, "owner", "ok i'll bring the crisps for chess then", t + 60)
        self.log(cid, Alice, "alice", "and tell charlie the lobby password is in the pins", t + 120)

    def index(self):
        for c in (MAIN, OTHER):        # the loop only indexes main channels; OTHER must stay empty anyway
            if c in self.mod.channels():
                asyncio.run(self.mod.index_channel(c))

    def recall(self, text, author=OWNER, cid=MAIN):
        m, _ = fake_msg(text, author=author, cid=cid)
        from core.turn import Turn
        t = Turn(msgs=[m], reason="main", is_owner=author == OWNER, stranger=author not in (OWNER, FRIEND),
                 known=author in (OWNER, FRIEND), joined=text)
        return asyncio.run(self.mod.context(t))

    # ---- chunking
    def test_chunking_gaps_and_open_tail(self):
        from modules.recall.module import chunk_rows
        rows = [(1, 1, "a", "hi", 0), (2, 1, "a", "there", 60), (3, 2, "b", "later", 60 + 3600), (4, 2, "b", "x", 60 + 3660)]
        closed, tail = chunk_rows(rows, now=4000)
        self.assertEqual([[r[0] for r in c] for c in closed], [[1, 2]])
        self.assertEqual([r[0] for r in tail], [3, 4])            # still going: not indexed yet
        closed, tail = chunk_rows(rows, now=60 + 3660 + 1801)
        self.assertEqual(len(closed), 2)
        many = [(i, 1, "a", "word " * 10, i) for i in range(40)]
        self.assertTrue(all(len(c) <= 15 for c in chunk_rows(many, now=10**6)[0]))

    def test_only_main_channel_is_indexed_and_searched(self):
        self.old_convo(MAIN)
        self.old_convo(OTHER)
        self.log(OTHER, Alice, "alice", "secret: the other channel plans for chess", time.time() - 3 * 86400 + 200)
        asyncio.run(self.mod.index_channel(MAIN))
        st = self.mod.db.stats()["groups"]
        self.assertEqual({g["scope"] for g in st}, {"channel:100"})
        note = self.recall("lato when is chess again?")
        self.assertIn("chess is at 8 on friday", note)
        self.assertIn("3 days ago", note)
        self.assertNotIn("secret", note)
        self.assertEqual(self.recall("lato when is chess again?", author=FRIEND, cid=OTHER), "")

    def test_recall_keeps_each_main_channel_separate(self):
        from unittest.mock import patch

        from core import config

        self.old_convo()
        self.index()
        with (patch.object(config, "MAIN_CHANNELS", {MAIN, OTHER}),
              patch.object(config, "PRIVATE_CHANNELS", {MAIN, OTHER})):
            self.assertEqual(self.recall("lato when is chess again?", cid=OTHER), "")
            self.assertIn("chess is at 8 on friday", self.recall("lato when is chess again?"))

    def test_skips_strangers_short_chatter_and_recent(self):
        self.old_convo()
        self.index()
        self.assertEqual(self.recall("lato when is chess again?", author=STRANGER), "")
        self.assertEqual(self.recall("lol ok"), "")
        self.assertIn("chess", self.recall("lato when is chess again?", author=FRIEND))
        self.setUp()
        self.old_convo(days=0.5)                                     # 12 h ago...
        self.index()
        self.settings(recall_skip_recent_min=24 * 60)                # ...counts as "recent" with a 24 h window
        self.assertEqual(self.recall("lato when is chess again?"), "")

    def test_delete_and_edit_rebuild_the_chunk(self):
        self.old_convo()
        self.index()
        pw = self.mid                       # the lobby password message
        self.lato.store.delete_message(pw)
        asyncio.run(self.mod.ev_raw_message_delete(types.SimpleNamespace(message_id=pw)))
        note = self.recall("lato what's the chess lobby password thing")
        self.assertNotIn("password", note)
        self.assertIn("chess is at 8", note)
        first = self.mid - 2
        self.lato.store.update_message(first, "chess moved to saturday at 9")
        asyncio.run(self.mod.ev_raw_message_edit(types.SimpleNamespace(message_id=first, data={"content": "x"})))
        note = self.recall("lato when is chess again?")
        self.assertIn("saturday at 9", note)
        self.assertNotIn("friday", note)

    def test_forget_drops_only_the_askers_own_messages(self):
        self.old_convo()
        self.index()
        n = asyncio.run(self.mod.forget_own(OWNER, "crisps"))
        self.assertEqual(n, 1)
        note = self.recall("lato who is bringing snacks for chess")
        self.assertNotIn("crisps", note)
        self.assertIn("bring snacks", note)                 # alice's line stays
        self.assertEqual(asyncio.run(self.mod.forget_own(OWNER, "friday")), 0)   # that was alice's, not owner's

    def test_episode_written_once_and_recalled(self):
        from zoneinfo import ZoneInfo
        day = (datetime.datetime.now(ZoneInfo("UTC")) - datetime.timedelta(days=2)).date()
        t0 = datetime.datetime(day.year, day.month, day.day, 12, tzinfo=ZoneInfo("UTC")).timestamp()
        for i in range(10):
            self.log(MAIN, Alice if i % 2 else OWNER, "alice" if i % 2 else "owner", f"talking about the ebike battery {i}", t0 + i * 60)
        calls = []

        async def chat(messages, think=False, **kw):
            calls.append(messages)
            return "owner and alice talked about owner's ebike battery; lato picked cyan for her role.", ""
        self.lato.llm.chat = chat
        txt = asyncio.run(self.mod.write_episode(MAIN, day))
        self.assertIn("ebike battery", txt)
        self.assertIn("talking about the ebike battery 0", calls[0][1]["content"])
        self.assertIsNone(asyncio.run(self.mod.write_episode(MAIN, day)))          # not twice
        note = self.recall("lato what did we say about my ebike battery")
        self.assertIn("(summary, 2 days ago)", note)
        quiet = (day - datetime.timedelta(days=1))
        self.assertIsNone(asyncio.run(self.mod.write_episode(MAIN, quiet)))         # < 8 messages: skipped
        self.assertEqual(len(calls), 1)

    def test_keyword_fallback_when_embeddings_down(self):
        self.old_convo()
        type(self).embed_ok = False
        self.mod.embed_fail = 0
        asyncio.run(self.mod.index_channel(MAIN))
        self.assertEqual(self.mod.db.stats()["groups"], [])          # waits for the embedding endpoint rather than skipping
        self.mod.embed_fail = 10
        asyncio.run(self.mod.index_channel(MAIN))                    # gave up waiting: stored for keyword search
        self.assertIn("chess is at 8", self.recall("lato when is chess again?"))

    def test_works_without_sqlite_vec(self):
        self.old_convo()
        vec = self.mod.db.vec_ok
        self.mod.db.vec_ok = False                       # e.g. the package failed to load: keyword search only
        try:
            self.index()
            self.assertIn("chess is at 8", self.recall("lato when is chess again?"))
        finally:
            self.mod.db.vec_ok = vec

    def test_her_name_is_not_part_of_the_search(self):
        from modules.recall.module import clean_query
        self.assertEqual(clean_query("Lato, whats the capital of france <@123> @alice"), "whats the capital of france")
        self.assertEqual(clean_query("latochan lato"), "")

    def test_budget(self):
        t = time.time() - 5 * 86400
        for k in range(12):
            for j in range(3):
                self.log(MAIN, Alice, "alice", f"chess plan number {k} part {j} " + "blah " * 30, t + k * 4000 + j * 10)
        self.index()
        self.settings(recall_budget=600)
        self.assertLessEqual(len(self.recall("lato what was the chess plan")), 900)


if __name__ == "__main__":
    unittest.main()
