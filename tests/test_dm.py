"""DMs (modules/dm): sending on request (who, intent, friends-only recipients, daily cap) and answering DMs."""
import asyncio
import json
import types
import unittest

import discord

import common
import friends
from test_core import fake_msg

FRIEND = (10**17 + 1)          # on the friends list (FRIEND_IDS seed in tests/common.py)
OWNER = (10**17 + 2)


class DM(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from core.app import Lato
        cls.lato = Lato()
        asyncio.run(cls.lato.setup_modules())
        cls.mod = cls.lato.module("dm")
        friends.add(FRIEND, "alice")
        cls.lato.friends.refresh(force=True)

    def setUp(self):
        self.settings()
        self.dms = []
        self.mod.sent_file.unlink(missing_ok=True)

        def get_user(uid):
            async def send(text, **kw):
                self.dms.append((uid, text))
                return types.SimpleNamespace(channel=types.SimpleNamespace(id=900000 + uid % 1000))
            return types.SimpleNamespace(id=uid, send=send)
        self.lato.client.get_user = get_user

    def settings(self, **kw):
        base = {"sleep_mode": False, "mod_dm": True, "dm_who": "owner", "dm_reply": True, "dm_daily_cap": 5}
        common.TMP.joinpath("settings.json").write_text(json.dumps({**base, **kw}))
        self.lato.S.refresh(force=True)

    def ask(self, text, reply, author=OWNER):
        async def fake(messages, think=False, **k):
            return reply, ""
        self.lato.llm.chat = fake
        m, sent = fake_msg(text, author=author, cid=7070)
        asyncio.run(self.lato.pipeline.respond([m], "main" if author == OWNER else "pinged"))
        return sent, self.lato.history.recent(7070, 3)

    def test_owner_can_dm_a_friend(self):
        sent, _ = self.ask("tell alice the movie is at 8", "on it\n[[DM: @alice -> movie's at 8 :proud:]]")
        self.assertEqual(self.dms, [(FRIEND, "movie's at 8 :proud:")])
        self.assertEqual(sent[0][1], "on it")                     # the tag never shows in the channel

    def test_me_means_owner(self):
        self.ask("dm me the link later", "ok\n[[DM: me -> here u go]]")
        self.assertEqual(self.dms, [(OWNER, "here u go")])

    def test_strangers_and_unknown_names_never_get_dms(self):
        _, hist = self.ask("tell bob hi", "ok\n[[DM: @bob -> hi]]")
        self.assertEqual(self.dms, [])
        self.assertIn("dm failed", hist[-1]["content"])
        self.ask(f'message {10**17 + 4}', f'ok\n[[DM: {10**17 + 4} -> hi]]')
        self.assertEqual(self.dms, [])

    def test_needs_the_askers_own_words(self):
        _, hist = self.ask("lol nice", "[[DM: @alice -> hi]]")
        self.assertEqual(self.dms, [])
        self.assertIn("didn't ask for a message", hist[-1]["content"])

    def test_who_setting(self):
        self.ask("tell owner hi from me", "ok\n[[DM: me -> hi]]", author=FRIEND)
        self.assertEqual(self.dms, [])                            # default: owner only
        self.settings(dm_who="friends")
        self.ask("tell owner hi from me", "ok\n[[DM: owner -> alice says hi]]", author=FRIEND)
        self.assertEqual(self.dms, [(OWNER, "alice says hi")])

    def test_daily_cap(self):
        self.settings(dm_daily_cap=2)
        for _ in range(3):
            self.ask("tell alice hi", "ok\n[[DM: @alice -> hi]]")
        self.assertEqual(len(self.dms), 2)

    def test_module_off_sends_nothing(self):
        self.settings(mod_dm=False)
        self.ask("tell alice hi", "ok\n[[DM: @alice -> hi]]")
        self.assertEqual(self.dms, [])
        self.assertNotIn("[[DM", self.lato.system_prompt())

    def test_incoming_dms(self):
        def dm_msg(author):
            ch = discord.DMChannel.__new__(discord.DMChannel)
            ch.id = 5551
            return types.SimpleNamespace(id=2, content="hey lato", attachments=[], stickers=[], embeds=[], reactions=[],
                                         mentions=[], reference=None, guild=None, channel=ch,
                                         author=types.SimpleNamespace(id=author, bot=False, display_name="x", name="x"))
        started = []
        orig = self.lato.router.start
        self.lato.router.start = lambda key, msg, reason, is_owner: started.append((msg.author.id, reason))
        try:
            for who in (OWNER, FRIEND, 999):
                asyncio.run(self.lato.router.handle(dm_msg(who)))
            self.assertEqual(started, [(OWNER, "dm"), (FRIEND, "dm")])     # strangers ignored
            started.clear()
            self.settings(dm_reply=False)
            asyncio.run(self.lato.router.handle(dm_msg(OWNER)))
            self.settings(mod_dm=False)
            asyncio.run(self.lato.router.handle(dm_msg(OWNER)))
            self.assertEqual(started, [])
        finally:
            self.lato.router.start = orig


if __name__ == "__main__":
    unittest.main()
