"""/help (core/commands.py): built from switched-on modules, filtered by who's asking."""
import asyncio
import json
import unittest

import common
import friends

OWNER = (10**17 + 2)
FRIEND = (10**17 + 1)          # on the friends list (FRIEND_IDS seed in tests/common.py)


class Help(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from core.app import Lato
        cls.lato = Lato()
        asyncio.run(cls.lato.setup_modules())

    def settings(self, **kw):
        base = {"sleep_mode": False, "mod_lights": True, "mod_draw": True, "mod_dm": True, "memory_enabled": True,
                "lights_who": "trusted", "draw_friends": False, "dm_who": "owner"}
        common.TMP.joinpath("settings.json").write_text(json.dumps({**base, **kw}))
        self.lato.S.refresh(force=True)

    def help(self, uid):
        from core.commands import build_help
        return build_help(self.lato, uid)

    def setUp(self):
        self.settings()
        friends.trust(FRIEND, False)
        self.lato.friends.refresh(force=True)

    def test_every_module_declares_help(self):
        missing = [m.NAME for m in self.lato.modules if getattr(m, "HELP", None) is None]
        self.assertEqual(missing, [], "add HELP = {...} (or HELP = {} to hide it) to these modules")
        for m in self.lato.modules:
            if m.HELP:
                self.assertTrue({"title", "line", "who"} <= set(m.HELP), m.NAME)

    def test_owner_sees_everything_on(self):
        h = self.help(OWNER)
        for t in ("lights", "drawing", "dms", "server stuff", "notes", "go to sleep"):
            self.assertIn(t, h)
        self.assertLess(len(h), 2000)

    def test_friend_sees_only_what_they_may_use(self):
        h = self.help(FRIEND)
        self.assertIn("notes", h)
        for t in ("lights", "drawing", "dms", "server stuff", "go to sleep"):
            self.assertNotIn(t, h)
        friends.trust(FRIEND, True)
        self.lato.friends.refresh(force=True)
        try:
            self.assertIn("lights", self.help(FRIEND))      # lights_who trusted
        finally:
            friends.trust(FRIEND, False)
            self.lato.friends.refresh(force=True)

    def test_switched_off_module_is_not_listed(self):
        self.settings(mod_lights=False)
        h = self.help(OWNER)
        self.assertNotIn("lights", h)

    def test_stranger_gets_one_line(self):
        h = self.help(1234)
        self.assertEqual(len(h.splitlines()), 1)
        self.assertNotIn("lights", h)

    def test_tree_has_help_command(self):
        import discord
        from core import commands
        client = discord.Client(intents=discord.Intents.none())
        tree = commands.install(self.lato, client)
        self.assertEqual([c.name for c in tree.get_commands()], ["help"])

    def test_sync_only_when_changed(self):
        import discord
        from core import commands
        client = discord.Client(intents=discord.Intents.none())
        tree = commands.install(self.lato, client)
        calls = []

        async def fake_sync(guild=None):
            calls.append(guild.id)
            return tree.get_commands()
        tree.sync = fake_sync
        d = common.TMP / "slashtest"
        d.mkdir(exist_ok=True)
        (d / "slash-commands.sha").unlink(missing_ok=True)
        asyncio.run(commands.sync(tree, d))
        asyncio.run(commands.sync(tree, d))
        self.assertEqual(len(calls), 1)                # second start: unchanged, no API call
        (d / "slash-commands.sha").write_text("old")
        asyncio.run(commands.sync(tree, d))
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
