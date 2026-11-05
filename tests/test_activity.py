"""Activity watcher: what counts as a new game/artist, cooldowns, chance, strangers, play time, ping text."""
import asyncio
import json
import types
import unittest

import discord

import common
from test_core import fake_msg

FRIEND, STRANGER = (10**17 + 1), 999


def spotify(title, artist):
    return discord.Spotify(name="Spotify", details=title, state=artist, sync_id="x", session_id="s",
                           timestamps={}, assets={}, party={}, type=2)


def member(uid, *acts):
    return types.SimpleNamespace(id=uid, bot=False, display_name="Dana", name="dana", activities=list(acts))


class Activity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from core.app import Lato
        cls.lato = Lato()
        asyncio.run(cls.lato.setup_modules())
        cls.a = cls.lato.module("activity")
        cls.sent = []
        m, cls.sent = fake_msg("x")
        ch = m.channel

        async def channel(cid):
            return ch
        cls.lato.channel = channel

    def setUp(self):
        self.settings()
        self.a.last_user.clear()
        self.a.last_artist.clear()
        self.a.last_any = -1e9   # "long ago" (monotonic can be small on a fresh machine)
        self.sent.clear()
        self.prompts = []

        async def chat(messages, think=False, **kw):
            self.prompts.append(messages[-1]["content"])
            return "League again?? touch grass", ""
        self.lato.llm.chat = chat

    def settings(self, **kw):
        base = {"mod_activity": True, "activity_chance": 100, "sleep_mode": False, "mod_pings": True}
        common.TMP.joinpath("settings.json").write_text(json.dumps({**base, **kw}))
        self.lato.S.refresh(force=True)

    def update(self, before, after):
        asyncio.run(self.a.ev_presence_update(before, after))

    def test_new_game_pings_friend(self):
        self.update(member(FRIEND), member(FRIEND, discord.Game(name="League of Legends")))
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.sent[0][1].startswith(f"<@{FRIEND}> league again??"))
        self.assertIn("started playing League of Legends", self.prompts[0])

    def test_cooldown_and_same_game(self):
        self.update(member(FRIEND), member(FRIEND, discord.Game(name="League of Legends")))
        self.update(member(FRIEND, discord.Game(name="League of Legends")), member(FRIEND, discord.Game(name="League of Legends")))
        self.update(member(FRIEND), member(FRIEND, discord.Game(name="Minecraft")))
        self.assertEqual(len(self.sent), 1, "same game again = nothing; new game inside cooldown = nothing")

    def test_spotify_new_artist_only(self):
        self.settings(activity_user_cooldown_min=10, activity_global_cooldown_min=1)
        self.update(member(FRIEND), member(FRIEND, spotify("Bad Apple", "Alstroemeria")))
        self.a.last_user.clear(); self.a.last_any = 0
        self.update(member(FRIEND, spotify("Bad Apple", "Alstroemeria")), member(FRIEND, spotify("Other", "Alstroemeria")))
        self.assertEqual(len(self.sent), 1, "a new song by the same artist is not news")
        self.assertIn('"Bad Apple" by Alstroemeria', self.prompts[0])

    def test_strangers_and_chance_and_switches(self):
        self.update(member(STRANGER), member(STRANGER, discord.Game(name="X")))
        self.settings(activity_chance=0)
        self.update(member(FRIEND), member(FRIEND, discord.Game(name="X")))
        self.settings(activity_games=False)
        self.a.last_user.clear()
        self.update(member(FRIEND), member(FRIEND, discord.Game(name="Y")))
        self.assertEqual(self.sent, [])

    def test_no_ping_setting(self):
        self.settings(activity_ping=False)
        self.update(member(FRIEND), member(FRIEND, discord.Game(name="Osu")))
        self.assertTrue(self.sent[0][1].startswith("dana "))

    def test_play_time_counted(self):
        self.a.add_played(FRIEND, "Osu", 7300)
        self.assertGreaterEqual(self.a.played_today(FRIEND, "Osu"), 7300)
        self.update(member(FRIEND), member(FRIEND, discord.Game(name="Osu")))
        self.assertIn("about 2 hours today", self.prompts[0])



class AskWhatImDoing(unittest.TestCase):
    def test_context_describes_asker_and_mentioned_friend(self):
        import core.config as C
        from core.app import Lato
        from core.turn import Turn
        C.PRESENCE_INTENT = True
        lato = Lato()
        asyncio.run(lato.setup_modules())
        a = lato.module("activity")
        me = types.SimpleNamespace(id=(10**17 + 2), bot=False, display_name="Owner", name="owner",
                                   activities=[spotify("Bad Apple", "Alstroemeria")])
        dana = types.SimpleNamespace(id=FRIEND, bot=False, display_name="Dana", name="dana", activities=[discord.Game(name="Osu")])
        guild = types.SimpleNamespace(get_member=lambda uid: {me.id: me, dana.id: dana}.get(uid))
        m, _ = fake_msg("lato what am i listening to? and what is @dana playing", guild=guild)
        m.author, m.mentions = me, [dana]
        t = Turn(msgs=[m], reason="main", is_owner=True, stranger=False, known=True, joined=m.content)
        note = asyncio.run(a.context(t))
        self.assertIn('owner is listening to "Bad Apple" by Alstroemeria', note)
        self.assertIn("dana is playing Osu", note)
        t2 = Turn(msgs=[m], reason="main", is_owner=True, stranger=False, known=True, joined="lato hi")
        self.assertEqual(asyncio.run(a.context(t2)), "", "only when they ask")
        t3 = Turn(msgs=[m], reason="pinged", is_owner=False, stranger=True, known=False, joined=m.content)
        self.assertEqual(asyncio.run(a.context(t3)), "", "strangers get nothing")
        C.PRESENCE_INTENT = False


if __name__ == "__main__":
    unittest.main()
