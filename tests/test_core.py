"""Core tests: tag parser, module isolation, history persistence, module settings, pings, golden snapshot.
Run all bot tests:  python -m unittest discover -s tests -v      (from the latochan folder)"""
import asyncio
import json
import subprocess
import sys
import types
import unittest

import common  # noqa: F401  (sets env first)
from core.tags import TagRegistry


class Tags(unittest.TestCase):
    def setUp(self):
        self.r = TagRegistry()
        self.r.register("GIF", "gifs")
        self.r.register("REACT", "actions", ("EMOJI",))
        self.r.register("REMEMBER", "memory")

    def test_known_tags_removed_in_order(self):
        text, calls, dropped = self.r.parse("hi [[REACT: 😂]] there\n[[GIF: happy]]")
        self.assertEqual(text, "hi  there")
        self.assertEqual([(c.name, c.arg, c.owner) for c in calls], [("REACT", "😂", "actions"), ("GIF", "happy", "gifs")])
        self.assertEqual(dropped, [])

    def test_alias_and_space_form(self):
        _, calls, _ = self.r.parse("[[emoji 💀]]")
        self.assertEqual((calls[0].name, calls[0].arg), ("REACT", "💀"))

    def test_unknown_short_dropped_long_kept(self):
        text, calls, dropped = self.r.parse("a [[DANCE]] b [[WHATEVER: " + "x" * 80 + "]]")
        self.assertEqual(calls, [])
        self.assertEqual(dropped, ["[[DANCE]]"])
        self.assertIn("[[WHATEVER:", text)

    def test_multiline_known_tag(self):
        text, calls, _ = self.r.parse("ok\n[[REMEMBER: likes\ncats]]")
        self.assertEqual(text, "ok")
        self.assertEqual(calls[0].arg, "likes\ncats")

    def test_two_modules_cannot_claim_one_tag(self):
        with self.assertRaises(ValueError):
            self.r.register("GIF", "other")


def fake_msg(text="hi", author=(10**17 + 2), guild=None, cid=100):
    sent = []

    async def reply(t, **kw):
        sent.append(("reply", t, kw.get("allowed_mentions")))

    class Ch:
        id, name = cid, "general"

        def typing(self):
            import contextlib

            @contextlib.asynccontextmanager
            async def cm():
                yield
            return cm()

        async def send(self, t, **kw):
            sent.append(("send", t, kw.get("allowed_mentions")))

    async def add_reaction(e):
        sent.append(("react", str(e), None))

    m = types.SimpleNamespace(id=1, content=text, attachments=[], stickers=[], embeds=[], reactions=[], mentions=[],
                              reference=None, guild=guild, channel=Ch(), reply=reply, add_reaction=add_reaction,
                              author=types.SimpleNamespace(id=author, bot=False, display_name="x", name="x", voice=None))
    return m, sent


class App(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from core.app import Lato
        cls.lato = Lato()
        asyncio.run(cls.lato.setup_modules())

    def settings(self, **kw):
        common.TMP.joinpath("settings.json").write_text(json.dumps({"sleep_mode": False, **kw}))
        self.lato.S.refresh(force=True)

    def ask(self, reply, text="hi", **kw):
        async def fake(messages, think=False, **k):
            self.prompt = messages
            return reply, ""
        self.lato.llm.chat = fake
        m, sent = fake_msg(text, **kw)
        asyncio.run(self.lato.pipeline.respond([m], "main"))
        return sent

    def test_existing_modules_loaded(self):
        names = {m.NAME for m in self.lato.modules}
        self.assertTrue({"actions", "gifs", "memory", "personality", "pings", "rag", "sleep"} <= names, names)

    def test_broken_module_is_isolated(self):
        from core.module import Module

        class Boom(Module):
            NAME, ORDER = "boom", 1

            async def context(self, turn):
                raise RuntimeError("kaboom")
        b = Boom(self.lato, common.ROOT)
        self.lato.modules.insert(0, b)
        try:
            self.settings()
            for _ in range(3):
                sent = self.ask("still here")
                self.assertEqual(sent[0][1], "still here")
            self.assertTrue(b.broken, "module should be disabled after 3 failures")
        finally:
            self.lato.modules.remove(b)

    def test_switched_off_module_tags_still_stripped(self):
        self.settings(memory_enabled=False)
        sent = self.ask("ok\n[[REMEMBER: " + "a very long note " * 6 + "]]")
        self.assertEqual(sent[0][1], "ok")

    def test_pings_resolve_unique_names_only(self):
        self.settings(mod_pings=True)
        mem = lambda i, n: types.SimpleNamespace(id=i, bot=False, display_name=n, name=n, global_name=None)
        guild = types.SimpleNamespace(members=[mem(1, "dana"), mem(2, "danny"), mem(3, "sam"), mem(4, "sam")])
        sent = self.ask("@dana look. @sam? @owner hi @everyone", guild=guild)
        text, am = sent[0][1], sent[0][2]
        self.assertIn("<@1>", text)
        self.assertIn("@sam", text)            # ambiguous: stays plain
        self.assertIn(f'<@{10**17 + 2}>', text)
        self.assertIn("@everyone", text)
        self.assertTrue(am.users and not am.everyone and not am.roles)

    def test_pings_off_means_no_user_pings(self):
        self.settings(mod_pings=False)
        sent = self.ask("@owner hi")
        self.assertEqual(sent[0][1], "@owner hi")
        self.assertFalse(sent[0][2].users)

    def test_module_persona_only_while_enabled(self):
        self.settings(mod_pings=True)
        self.assertIn("Pinging people", self.lato.system_prompt())
        self.settings(mod_pings=False)
        self.assertNotIn("Pinging people", self.lato.system_prompt())

    def test_sleepy_wake_toggle_and_chance(self):
        async def chat(messages, think=False, **k):
            self.prompt = messages
            return "hi", ""
        self.lato.llm.chat = chat

        def woke_prompt():
            m, _ = fake_msg("lato wake up")
            asyncio.run(self.lato.pipeline.respond([m], "woke"))
            return self.prompt[-1]["content"]
        self.settings(sleep_mode=True)
        self.assertIn("grumpy or sleepy bit", woke_prompt())
        self.settings(sleep_mode=True, wake_sleepy=False)
        self.assertIn("not grumpy about it this time", woke_prompt())
        self.settings(sleep_mode=True, wake_sleepy_chance=0)
        self.assertIn("not grumpy about it this time", woke_prompt())
        self.settings(sleep_mode=True, wake_sleepy_chance=50)
        n = sum("grumpy or sleepy bit" in woke_prompt() for _ in range(200))
        self.assertTrue(60 < n < 140, n)

    def test_history_persists_across_restart(self):
        from core.store import History, Store
        path = common.TMP / "hist-test.db"
        h = History(Store(path, 6), 6)
        for i in range(10):
            h.append(55, {"role": "user", "content": f"m{i}"})
        h2 = History(Store(path, 6), 6)
        self.assertEqual([e["content"] for e in h2.get(55)], [f"m{i}" for i in range(4, 10)])


class Settings(unittest.TestCase):
    def test_module_settings_in_schema_and_cli(self):
        out = subprocess.run([sys.executable, str(common.ROOT / "botsettings.py"), "json"], capture_output=True, text=True,
                             env={**__import__("os").environ})
        doc = json.loads(out.stdout)
        keys = {s["key"]: s for s in doc["schema"]}
        self.assertIn("mod_pings", keys)
        self.assertEqual(keys["mod_pings"]["group"], "modules")
        self.assertIn("modules", [g["id"] for g in doc["groups"]])
        self.assertIn("sass", keys)   # v1 settings untouched


class Golden(unittest.TestCase):
    def test_v1_behaviour_unchanged(self):
        r = subprocess.run([sys.executable, str(common.HERE / "golden.py")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])


if __name__ == "__main__":
    unittest.main()
