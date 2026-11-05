"""Her system prompt: persona + core rules + module snippets. Checks the split stays clean as modules are added:
  * the persona (and its seed) holds no tags or mechanics,
  * every [[TAG]] a module tells her about is one that module actually handles, and its handled tags are documented,
  * snippets vanish when their feature is off, stay small, and come out in a stable order (prompt cache).
"""
import asyncio
import json
import re
import unittest

import common  # noqa: F401  (env + paths)

from core import config as C
from core import prompt

TAG_RE = re.compile(r"\[\[\s*([A-Z_]+)")
CORE_TAGS = {"SKIP", "THIS"}                    # handled by the pipeline itself / the generic example in rules.md
UNDOCUMENTED_OK = {"THINKING"}                   # alias-style tag names the model invents; handled, not taught
SNIPPET_BUDGET = 800                             # chars per module snippet (server emotes come from data, not counted)
BUDGET_OVERRIDE = {"actions": 1000}               # 19 tags; keep it terse anyway
PERSONA_FILES = ("persona.md", "persona-limited.md", "persona-me.md")


class App(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from core.app import Lato
        cls.lato = Lato()
        asyncio.run(cls.lato.setup_modules())

    def settings(self, **kw):
        common.TMP.joinpath("settings.json").write_text(json.dumps({"sleep_mode": False, **kw}))
        self.lato.S.refresh(force=True)

    def names(self):
        return [n for n, _ in self.lato.prompt_parts()]

    # ---- the persona is character only ----
    def test_persona_seed_has_no_mechanics(self):
        for f in (C.ROOT / "persona.md", C.PERSONA_PATH):
            text = f.read_text(encoding="utf-8")
            self.assertNotIn("[[", text, f"{f}: tags belong in core/rules.md or a module's persona.md")

    def test_order_persona_rules_modules(self):
        self.settings()
        names = self.names()
        self.assertEqual(names[:2], ["persona", "rules"])
        self.assertTrue(all(n.startswith("module ") for n in names[2:]), names)
        self.assertEqual(self.lato.system_prompt(), self.lato.system_prompt(), "must be byte-identical between calls")

    def test_snippet_tags_are_handled_and_documented(self):
        for m in self.lato.modules:
            texts = [m.persona_file(f) for f in PERSONA_FILES]
            mentioned = {t for x in texts for t in TAG_RE.findall(x)}
            handled = set(m.TAGS) | {a for al in m.TAGS.values() for a in al}
            self.assertLessEqual(mentioned - CORE_TAGS, handled, f"{m.NAME}: persona mentions tags it doesn't handle")
            if any(texts):
                missing = set(m.TAGS) - mentioned - UNDOCUMENTED_OK
                self.assertFalse(missing, f"{m.NAME}: handles {missing} but its persona never mentions them")

    def test_rules_only_use_core_tags(self):
        self.assertLessEqual(set(TAG_RE.findall(C.RULES_PATH.read_text(encoding="utf-8"))), CORE_TAGS)

    def test_snippets_within_budget(self):
        for m in self.lato.modules:
            for f in PERSONA_FILES:
                n = len(m.persona_file(f))
                cap = BUDGET_OVERRIDE.get(m.NAME, SNIPPET_BUDGET)
                self.assertLessEqual(n, cap, f"modules/{m.NAME}/{f} is {n} chars (budget {cap})")

    def test_off_features_say_nothing(self):
        self.settings(memory_enabled=False, gifs_enabled=False)
        names = self.names()
        self.assertNotIn("module memory", names)
        self.assertNotIn("module gifs", names)
        self.assertNotIn("module sleep", names)         # sleep_mode False
        self.settings(memory_enabled=True, sleep_mode=True)
        names = self.names()
        self.assertIn("module memory", names)
        self.assertIn("module sleep", names)

    def test_server_powers_off_keeps_only_thinking_switch(self):
        self.settings(server_powers=False)
        text = self.lato.module("actions").persona()
        self.assertIn("[[TOGGLE_THINKING]]", text)
        self.assertNotIn("[[MUTE", text)
        self.settings(server_powers=True)
        self.assertIn("[[MUTE", self.lato.module("actions").persona())

    def write_emotes(self):
        C.EMOTES_PATH.write_text(f'<:proud:{10**17 + 3}> proud\n# comment\n<:Heart:{10**17 + 8}> love\n'
                                 f'<:kms:{10**17 + 9}> blocked one\n', encoding="utf-8")

    def test_emote_block_has_short_names_only(self):
        from core import emotes
        try:
            self.write_emotes()
            block = emotes.prompt_block()
            self.assertIn(":proud: proud", block)
            self.assertIn(":Heart: love", block)
            self.assertNotIn(f'{10**17 + 3}', block)      # no IDs in her prompt
            self.assertNotIn("kms", block)                      # blocked
            self.assertIn(block, self.lato.module("actions").persona())
        finally:
            C.EMOTES_PATH.unlink(missing_ok=True)
        self.assertEqual(emotes.prompt_block(), "")

    def test_expand_short_codes(self):
        from core import emotes
        try:
            self.write_emotes()
            self.assertEqual(emotes.expand("nice :proud:"), f'nice <:proud:{10**17 + 3}>')
            self.assertEqual(emotes.expand(":heart:"), f'<:Heart:{10**17 + 8}>')     # any case
            for keep in (f'<:proud:{10**17 + 3}>', "at 12:30:45", ":skull: lol", ":kms:", "a:proud:b"):
                self.assertEqual(emotes.expand(keep), keep)
            self.assertEqual(emotes.react_code(":proud:"), f'<:proud:{10**17 + 3}>')
            self.assertEqual(emotes.react_code("😂"), "😂")
        finally:
            C.EMOTES_PATH.unlink(missing_ok=True)

    def test_reply_sends_full_code_and_history_keeps_short(self):
        from test_core import fake_msg
        try:
            self.write_emotes()
            self.settings()

            async def fake(messages, think=False, **k):
                return "ok :proud:", ""
            self.lato.llm.chat = fake
            m, sent = fake_msg("did it", cid=777)
            asyncio.run(self.lato.pipeline.respond([m], "main"))
            self.assertIn(f'<:proud:{10**17 + 3}>', sent[0][1])
            hist = [h["content"] for h in self.lato.history.recent(777, 5) if h["role"] == "assistant"]
            self.assertEqual(hist[-1], "ok :proud:")
        finally:
            C.EMOTES_PATH.unlink(missing_ok=True)

    def test_gif_moods_only_with_links(self):
        g = self.lato.module("gifs")
        d = common.TMP / "gifs"
        (d / "smug").mkdir(parents=True, exist_ok=True)
        (d / "smug" / "links.txt").write_text("https://example.com/a.gif\n")
        (d / "sad").mkdir(parents=True, exist_ok=True)       # empty: not offered
        self.settings(gifs_enabled=True)
        text = g.persona()
        self.assertIn("smug", text)
        self.assertNotIn("sad", text)
        self.assertNotIn("{{MOODS}}", text)

    def test_promptinfo_matches_live_prompt(self):
        from core import promptinfo
        self.settings()
        self.assertEqual(prompt.join(promptinfo.current_parts()), self.lato.system_prompt())


if __name__ == "__main__":
    unittest.main()
