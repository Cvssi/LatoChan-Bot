"""Doodle mode: SVG sanitiser (no scripts/links/entities), truncated-SVG repair, rendering, the posting flow."""
import asyncio
import json
import unittest

import common
from test_core import fake_msg
from modules.draw import svgsafe

CAT = ('<svg viewBox="0 0 256 256"><rect width="256" height="256" fill="#fde"/>'
       '<circle cx="128" cy="140" r="70" fill="#f90" stroke="#222" stroke-width="6"/>'
       '<polygon points="70,90 90,30 115,80" fill="#f90"/><path d="M100 150 Q128 170 156 150" stroke="#222" fill="none"/></svg>')


class Sanitiser(unittest.TestCase):
    def test_strips_dangerous_things(self):
        evil = ('```svg\n<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" onload="x()">'
                '<script>alert(1)</script><image href="https://untrusted.example.com/a.png"/><use xlink:href="http://e/x.svg#a"/>'
                '<rect width="10" height="10" style="fill:url(http://e/x)" onclick="y()"/>'
                '<rect width="5" height="5" fill="url(#grad)"/><foreignObject><p>hi</p></foreignObject></svg>\n```')
        out = svgsafe.clean(evil).decode()
        for bad in ("script", "image", "use", "onload", "onclick", "http://e", "192.168", "foreignObject"):
            self.assertNotIn(bad, out)
        self.assertIn("url(#grad)", out)              # internal references are fine
        self.assertIn('width="512"', out)

    def test_entities_refused(self):
        with self.assertRaises(svgsafe.Unsafe):
            svgsafe.clean('<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]><svg>&x;</svg>')

    def test_no_svg_refused(self):
        with self.assertRaises(svgsafe.Unsafe):
            svgsafe.clean("i drew a cat (trust me)")

    def test_truncated_svg_repaired(self):
        cut = '<svg viewBox="0 0 256 256"><g><rect width="256" height="256" fill="#abc"/><circle cx="1" cy="1" r="9"/><path d="M1 1 L'
        out = svgsafe.clean(cut).decode()
        self.assertIn("<circle", out)
        self.assertNotIn("<path", out)

    def test_renders_png(self):
        png = svgsafe.render_png(svgsafe.clean(CAT))
        self.assertTrue(png.startswith(b"\x89PNG"))


class Flow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from core.app import Lato
        cls.lato = Lato()
        asyncio.run(cls.lato.setup_modules())
        cls.d = cls.lato.module("draw")

    def setUp(self):
        common.TMP.joinpath("settings.json").write_text(json.dumps({"mod_draw": True, "sleep_mode": False, "mod_pings": False}))
        self.lato.S.refresh(force=True)
        self.d.count_file.unlink(missing_ok=True)

    def ask(self, model_svg, author=(10**17 + 2), reply="ok gimme a sec\n[[DRAW: a fat orange cat]]"):
        async def chat(messages, think=False, **kw):
            return (model_svg, "") if "draw this" in messages[-1]["content"] else (reply, "")
        self.lato.llm.chat = chat
        m, sent = fake_msg("lato draw me a cat", author=author)
        files = []

        async def reply_fn(text=None, file=None, **kw):
            sent.append(("reply", text, None))
            if file:
                files.append(file)
        m.reply = reply_fn

        async def go():
            await self.lato.pipeline.respond([m], "main")
            for t in [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]:
                await t
        asyncio.run(go())
        return sent, files

    def test_owner_gets_a_drawing(self):
        sent, files = self.ask(CAT)
        self.assertEqual(sent[0][1], "ok gimme a sec")
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].filename, "doodle.png")
        self.assertEqual(self.d.used_today(), 1)

    def test_broken_svg_gets_a_skull_not_a_crash(self):
        sent, files = self.ask("lol no")
        self.assertEqual(files, [])
        self.assertIn(("react", "💀", None), sent)

    def test_stranger_cannot(self):
        sent, files = self.ask(CAT, author=999)
        self.assertEqual(files, [])


if __name__ == "__main__":
    unittest.main()
