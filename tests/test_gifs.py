"""Gif module behaviour (ported from v1 tests_gifmode.py): wake-gif cooldown bug, gif mode, percentage sliders."""
import asyncio
import json
import time
import unittest
from unittest.mock import patch

import common
import gifstore
from test_core import fake_msg

OWNER, FRIEND, STRANGER = (10**17 + 2), (10**17 + 1), 999
GIFS = common.TMP / "gifs"
for mood in ("annoyed", "happy", "waking", "sleepy"):
    (GIFS / mood).mkdir(parents=True, exist_ok=True)
    (GIFS / mood / "links.txt").write_text(f"https://tenor.com/{mood}-1\nhttps://tenor.com/{mood}-2\n")
(GIFS / "bored").mkdir(parents=True, exist_ok=True)
(GIFS / "bored" / "links.txt").write_text("# empty\n")


class Gifs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from core.app import Lato
        cls.lato = Lato()
        asyncio.run(cls.lato.setup_modules())
        cls.gifs, cls.sleep = cls.lato.module("gifs"), cls.lato.module("sleep")
        cls.canned, cls.prompts = {"r": ""}, []

        async def fake(messages, think=False, **k):
            cls.prompts.append(messages[-1]["content"])
            return cls.canned["r"], ""
        cls.lato.llm.chat = fake

    def settings(self, **kw):
        common.TMP.joinpath("settings.json").write_text(json.dumps({"mod_pings": False, **kw}))
        self.lato.S.refresh(force=True)

    def run_(self, author=OWNER, reason="main", awake=True):
        self.prompts.clear()
        if awake:
            self.sleep.awake_until[100] = time.monotonic() + 600
        m, sent = fake_msg("hi", author=author)
        asyncio.run(self.lato.pipeline.respond([m], reason))
        return [(k, t) for k, t, _ in sent]

    def test_cooldown_after_wake_gif(self):
        self.settings(gif_cooldown_seconds=225)
        self.gifs.last_gif.clear()
        self.canned["r"] = "-_- what\n\n[[GIF: waking]]\n\nits 1am"
        self.sleep.awake_until.pop(100, None)
        r = self.run_(reason="woke", awake=False)
        self.assertEqual([k for k, _ in r], ["send", "reply"])
        self.assertTrue(r[0][1].startswith("https://tenor.com/waking"))
        self.canned["r"] = "this is why\n[[GIF: annoyed]]"
        r = self.run_()
        self.assertTrue(any(t.startswith("https://tenor.com/annoyed") for _, t in r), "owner bypasses cooldown")
        r = self.run_(author=FRIEND, reason="pinged")
        self.assertEqual([k for k, _ in r], ["reply"], "friend hits the cooldown")
        self.gifs.last_gif.clear()
        r = self.run_(author=FRIEND, reason="pinged")
        self.assertTrue(any(t.startswith("https://tenor.com/annoyed") for _, t in r))

    def test_empty_folder_text_only(self):
        self.settings()
        self.canned["r"] = "meh\n[[GIF: bored]]"
        self.assertEqual([k for k, _ in self.run_()], ["reply"])

    def test_gif_mode(self):
        self.settings(gif_only=True)
        self.canned["r"] = "blah blah\n[[GIF: happy]]"
        r = self.run_()
        self.assertEqual([k for k, _ in r], ["send"])
        self.assertIn("gif mode", self.prompts[0])
        self.canned["r"] = "no mood given"
        r = self.run_()
        self.assertEqual([k for k, _ in r], ["send"])
        self.assertNotIn("bored", r[0][1])
        r = self.run_(author=STRANGER, reason="pinged")
        self.assertTrue(all(not t.startswith("https://") for _, t in r))
        self.assertNotIn("gif mode", self.prompts[0])

    def test_percentage_sliders(self):
        self.settings(gif_reply_chance=50)
        self.canned["r"] = "just text"
        n = sum(any(t.startswith("https://") for _, t in self.run_()) for _ in range(300))
        self.assertTrue(100 < n < 200, n)
        self.settings(gif_reply_chance=100, gif_only_chance=50)
        n = sum([k for k, _ in self.run_()] == ["send"] for _ in range(300))
        self.assertTrue(100 < n < 200, n)

    def test_master_switch_beats_gif_mode(self):
        self.settings(gifs_enabled=False, gif_only=True)
        self.canned["r"] = "hello\n[[GIF: happy]]"
        self.assertEqual([k for k, _ in self.run_()], ["reply"])


class GifStorage(unittest.TestCase):
    def test_validation_finishes_before_any_mood_is_written(self):
        with patch.object(gifstore, "MAX_PER_MOOD", 1), patch.object(gifstore, "_write") as write:
            with self.assertRaisesRegex(ValueError, "too many links"):
                gifstore.apply("replace", {"happy": ["https://example.com/1.gif"],
                                           "sad": ["https://example.com/1.gif", "https://example.com/2.gif"]})
            write.assert_not_called()

    def test_write_failure_reports_earlier_changes(self):
        with patch.object(gifstore, "_write", side_effect=[None, OSError("disk full")]):
            with self.assertRaisesRegex(gifstore.PartialUpdateError, "Saved happy.*sad"):
                gifstore.apply("replace", {"happy": ["https://example.com/1.gif"], "sad": []})


if __name__ == "__main__":
    unittest.main()
