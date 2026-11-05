"""Chatter mix (core/mix.py): on read / react / gif / reply for chat that isn't aimed at her."""
import asyncio
import json
import types
import unittest

import common
from core import mix
from core.turn import Turn
from test_core import fake_msg

GIFS = common.TMP / "gifs"
OWNER = (10**17 + 2)


class Mix(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from core.app import Lato
        cls.lato = Lato()
        asyncio.run(cls.lato.setup_modules())
        (GIFS / "happy").mkdir(parents=True, exist_ok=True)
        (GIFS / "happy" / "links.txt").write_text("https://tenor.com/happy-1\n")

    def settings(self, **kw):
        base = {"sleep_mode": False, "mix_ignore": 0, "mix_react": 0, "mix_gif": 0, "mix_reply": 100, "gif_cooldown_seconds": 0}
        common.TMP.joinpath("settings.json").write_text(json.dumps({**base, **kw}))
        self.lato.S.refresh(force=True)

    def turn(self, text="lol nice", reason="main", stranger=False):
        m, _ = fake_msg(text)
        return Turn(msgs=[m], reason=reason, is_owner=True, stranger=stranger, known=not stranger, joined=text)

    def ask(self, text, reply="ok", **kw):
        self.calls = 0

        async def fake(messages, think=False, **k):
            self.calls += 1
            self.prompt = messages[-1]["content"]
            return reply, ""
        self.lato.llm.chat = fake
        m, sent = fake_msg(text, cid=4242)
        asyncio.run(self.lato.pipeline.respond([m], kw.get("reason", "main")))
        return sent

    # ---- decide ----
    def test_default_is_always_reply(self):
        self.settings()
        self.assertEqual({mix.decide(self.lato, self.turn(), r / 10) for r in range(10)}, {"reply"})

    def test_shares_split_the_pie(self):
        self.settings(mix_ignore=25, mix_react=25, mix_gif=25, mix_reply=25)
        got = [mix.decide(self.lato, self.turn(), r) for r in (0.1, 0.3, 0.6, 0.9)]
        self.assertEqual(got, ["ignore", "react", "gif", "reply"])

    def test_not_adding_up_is_scaled(self):
        self.settings(mix_ignore=10, mix_react=0, mix_gif=0, mix_reply=10)      # = 50 / 50
        self.assertEqual(mix.decide(self.lato, self.turn(), 0.49), "ignore")
        self.assertEqual(mix.decide(self.lato, self.turn(), 0.51), "reply")
        self.settings(mix_ignore=0, mix_react=0, mix_gif=0, mix_reply=0)        # nothing set = reply
        self.assertEqual(mix.decide(self.lato, self.turn(), 0.0), "reply")

    def test_addressed_always_reply(self):
        self.settings(mix_ignore=100, mix_reply=0)
        for text in ("lato you there", "what do you think?", "latochan pls"):
            self.assertEqual(mix.decide(self.lato, self.turn(text), 0.0), "reply", text)
        for reason in ("pinged", "name", "woke", "sleep_cmd"):
            self.assertEqual(mix.decide(self.lato, self.turn(reason=reason), 0.0), "reply", reason)
        self.assertEqual(mix.decide(self.lato, self.turn(reason="awake"), 0.0), "ignore")

    def test_ping_and_reply_to_her_always_reply(self):
        import discord
        self.settings(mix_ignore=100, mix_reply=0)
        bot = types.SimpleNamespace(id=(10**17 + 6))
        orig = type(self.lato.client).user
        type(self.lato.client).user = property(lambda c: bot)
        try:
            t = self.turn("hey look at this")                    # ping
            t.msgs[0].mentions = [bot]
            self.assertEqual(mix.decide(self.lato, t, 0.0), "reply")
            t = self.turn("lmao same")                           # a reply to one of her messages
            her = discord.Message.__new__(discord.Message)
            her.author = bot
            t.msgs[0].reference = types.SimpleNamespace(resolved=her)
            self.assertEqual(mix.decide(self.lato, t, 0.0), "reply")
            self.assertEqual(mix.decide(self.lato, self.turn("lmao same"), 0.0), "ignore")   # plain chatter
        finally:
            type(self.lato.client).user = orig

    def test_unavailable_shares_go_to_reply(self):
        self.settings(mix_react=50, mix_gif=50, mix_reply=0, server_powers=False, gifs_enabled=False)
        self.assertEqual(mix.decide(self.lato, self.turn(), 0.0), "reply")

    # ---- pipeline ----
    def test_ignore_skips_the_model(self):
        self.settings(mix_ignore=100, mix_reply=0)
        sent = self.ask("lol nice")
        self.assertEqual(self.calls, 0)
        self.assertEqual(sent, [])
        hist = self.lato.history.recent(4242, 1)
        self.assertEqual(hist[-1]["content"], "[[SKIP]]")

    def test_react_sends_only_a_reaction(self):
        self.settings(mix_react=100, mix_reply=0)
        sent = self.ask("lol nice", reply="haha that's great\n[[REACT: 😂]]\n[[GIF: happy]]")
        self.assertIn("single [[REACT", self.prompt)
        self.assertEqual([s[0] for s in sent], ["react"])
        self.assertEqual(sent[0][1], "😂")

    def test_react_without_reaction_stays_quiet(self):
        self.settings(mix_react=100, mix_reply=0)
        sent = self.ask("lol nice", reply="haha")
        self.assertEqual(sent, [])

    def test_gif_sends_only_a_gif(self):
        self.settings(mix_gif=100, mix_reply=0)
        sent = self.ask("lol nice", reply="so true\n[[GIF: happy]]")
        self.assertIn("answer with a gif only", self.prompt)
        self.assertEqual([s[1] for s in sent], ["https://tenor.com/happy-1"])

    def test_question_gets_a_normal_reply(self):
        self.settings(mix_ignore=100, mix_reply=0)
        sent = self.ask("is it raining?", reply="no")
        self.assertEqual(self.calls, 1)
        self.assertEqual(sent[0][1], "no")


if __name__ == "__main__":
    unittest.main()
