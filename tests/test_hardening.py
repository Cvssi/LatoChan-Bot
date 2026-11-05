"""Small guards added in the 2.7.1 code review: each pins down one bug that was found, so it can't come back."""
import asyncio
import datetime
import sqlite3
import tempfile
import unittest
from pathlib import Path

import common  # noqa: F401 - initializes test configuration before importing the bot
from core import access, clock, tasks
from core.store import Store


class Clock(unittest.TestCase):
    def test_quiet_window_wraps_midnight(self):
        self.assertTrue(clock.in_hours(22, 6, hour=23))
        self.assertTrue(clock.in_hours(22, 6, hour=5))
        self.assertFalse(clock.in_hours(22, 6, hour=6))
        self.assertTrue(clock.in_hours(0, 8, hour=3))
        self.assertFalse(clock.in_hours(0, 8, hour=8))
        self.assertFalse(clock.in_hours(8, 8, hour=8), "start == end means never")

    def test_primary_channel_is_stable(self):
        from core import config as C
        self.assertEqual(C.primary_channel(), int(C.latoids.MAIN_CHANNEL))


class Access(unittest.TestCase):
    def test_levels_and_who(self):
        lv = access.level_of
        self.assertEqual([lv(True, False, True), lv(False, True, True), lv(False, False, True), lv(False, False, False)],
                         ["owner", "trusted", "friends", "stranger"])
        for who, expect in (("owner", [True, False, False, False]), ("trusted", [True, True, False, False]),
                            ("friends", [True, True, True, False]), ("nonsense", [True, False, False, False])):
            got = [access.allows_level(x, who) for x in ("owner", "trusted", "friends", "stranger")]
            self.assertEqual(got, expect, who)


class Tasks(unittest.TestCase):
    def test_spawn_keeps_a_reference_and_logs_crashes(self):
        async def go():
            done = []

            async def ok():
                await asyncio.sleep(0)
                done.append(1)

            async def boom():
                raise RuntimeError("kaboom")
            t1, t2 = tasks.spawn(ok(), "ok"), tasks.spawn(boom(), "boom")
            self.assertIn(t1, tasks._live)
            await asyncio.gather(t1, t2, return_exceptions=True)
            await asyncio.sleep(0)
            self.assertEqual(done, [1])
            self.assertNotIn(t1, tasks._live)
            self.assertNotIn(t2, tasks._live)
        with self.assertLogs("latochan", "ERROR") as cm:
            asyncio.run(go())
        self.assertIn("boom", cm.output[0])


class StoreByAuthor(unittest.TestCase):
    def test_only_that_persons_messages(self):
        st = Store(Path(tempfile.mkdtemp()) / "c.db", 10)
        for mid, author, text in ((1, 7, "a"), (2, 8, "b"), (3, 7, "c")):
            st.log_message(mid, 100, author, "x", text, 1000 + mid)
        st.log_message(4, 200, 7, "x", "other channel", 1004)
        self.assertEqual([r[3] for r in st.messages_by_author(100, 7)], ["a", "c"])


class Lights(unittest.TestCase):
    def test_intent_needs_a_word_start(self):
        from modules.lights.module import INTENT
        for text in ("turn the lights off", "make the lamp dimmer", "it's getting dark", "set a colour", "brighter please", "dim it",
                     "make it darker", "lamp on"):
            self.assertTrue(INTENT.search(text), text)
        for text in ("that was a slight problem", "my flight is at 8", "the dimension is wrong", "highlight it", "sunlight is nice",
                     "going to Brighton", "lightning outside", "a lightweight laptop"):
            self.assertFalse(INTENT.search(text), text)


class Weather(unittest.TestCase):
    def test_reading_without_a_temperature_does_not_raise(self):
        from modules.weather.module import Module
        m = object.__new__(Module)
        m.latest = {"ts": 1_700_000_000, "temp": None, "feels": None, "humidity": 80, "wind": 3, "gust": 5, "dir": 90,
                    "rain_rate": 0, "rain_today": 0}
        m.fc = []
        m.between = lambda t0, t1: []
        out = m.summary(now=1_700_000_100)
        self.assertIn("temperature unavailable", out)
        self.assertIn("humidity 80%", out)


class RecallBits(unittest.TestCase):
    def test_dates_do_not_use_the_linux_only_percent_dash_d(self):
        from modules.recall.module import Module, ago
        self.assertEqual(Module.day_name(datetime.date(2026, 11, 4)), "Wednesday 4 November 2026")
        now = datetime.datetime(2026, 11, 4, 12, tzinfo=datetime.timezone.utc).timestamp()
        old = datetime.datetime(2026, 1, 3, 12, tzinfo=datetime.timezone.utc).timestamp()
        self.assertEqual(ago(old, now), "3 Jan 2026")

    def test_failed_insert_leaves_nothing_behind(self):
        from modules.recall.db import RecallDB
        db = RecallDB(Path(tempfile.mkdtemp()) / "r.db")
        if not db.vec_ok:
            self.skipTest("sqlite-vec not installed")
        with self.assertRaises(sqlite3.Error):
            db.add("chat", "channel:1", "some words here", 1, 2, channel_id=1, msg_ids=[1, 2], vec=[0.1] * 5)   # wrong size
        self.assertEqual(db._q("SELECT count(*) FROM chunks")[0][0], 0)
        self.assertEqual(db._q("SELECT count(*) FROM fts_chunks")[0][0], 0)
        self.assertEqual(db._q("SELECT count(*) FROM chunk_msgs")[0][0], 0)
        db.add("chat", "channel:1", "a good one", 1, 2, channel_id=1, msg_ids=[3], vec=[0.1] * 768)    # and it still works
        self.assertEqual(db._q("SELECT count(*) FROM chunks")[0][0], 1)


if __name__ == "__main__":
    unittest.main()
