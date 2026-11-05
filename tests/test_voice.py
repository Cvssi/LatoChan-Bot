"""Voice notes (modules/voice) against a fake voice worker: who gets transcribed, the transcript drives the normal
reply, her answer goes out as a real Discord voice message, and voice-only falls back to text if speech fails."""
import asyncio
import json
import os
import types
import unittest

from aiohttp import web

import common
import friends
from test_core import fake_msg

OWNER, FRIEND, STRANGER = (10**17 + 2), (10**17 + 1), 4242


class Worker:
    def __init__(self, text="hey lato turn the lamp warm", tts_ok=True):
        self.text, self.tts_ok, self.stt_calls, self.tts_calls = text, tts_ok, [], []

    async def stt(self, req):
        assert req.headers.get("X-Lato-Token") == "tok"
        self.stt_calls.append((await req.read(), dict(req.query)))
        return web.json_response({"text": self.text, "language": "en", "seconds": 2.0})

    async def tts(self, req):
        body = await req.json()
        self.tts_calls.append(body)
        if not self.tts_ok:
            return web.json_response({"error": "boom"}, status=500)
        return web.Response(body=b"OggS-fake", content_type="audio/ogg", headers={"X-Seconds": "1.5", "X-Waveform": "AAAA"})


def voice_msg(author=OWNER, text=""):
    m, sent = fake_msg(text, author=author)
    att = types.SimpleNamespace(filename="voice-message.ogg", content_type="audio/ogg", size=1000,
                                is_voice_message=lambda: True)

    async def read():
        return b"OggS-in"
    att.read = read
    m.attachments = [att]
    return m, sent


class Voice(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["VOICE_TOKEN"] = "tok"
        from core.app import Lato
        cls.lato = Lato()
        asyncio.run(cls.lato.setup_modules())
        cls.mod = cls.lato.module("voice")

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("VOICE_TOKEN", None)

    def settings(self, **kw):
        common.TMP.joinpath("settings.json").write_text(json.dumps(
            {"mod_voice": True, "voice_speak": True, "sleep_mode": False, "mod_pings": False, "mod_lights": False, **kw}))
        self.lato.S.refresh(force=True)

    def setUp(self):
        self.settings()
        self.lato.history.clear()
        self.mod.heard.clear()

    def run_with(self, worker, coro_fn):
        import modules.voice.module as vm

        async def go():
            app = web.Application()
            app.router.add_post("/stt", worker.stt)
            app.router.add_post("/tts", worker.tts)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "localhost", 0)
            await site.start()
            old = vm.VOICE_URL
            vm.VOICE_URL = f"http://localhost:{site._server.sockets[0].getsockname()[1]}"
            posted = []

            async def request(route, form=None, **kw):
                posted.append((route, form))
            self.lato.client.http.request = request
            try:
                return await coro_fn(), posted
            finally:
                vm.VOICE_URL = old
                await self.lato.close_http()
                await runner.cleanup()
        return asyncio.run(go())

    def converse(self, m, reply="ok, warm it is"):
        prompts = []

        async def chat(messages, think=False, **kw):
            prompts.append(messages[-1]["content"])
            return reply, ""
        self.lato.llm.chat = chat

        async def go():
            await self.lato.dispatch("message", m)
            await self.lato.pipeline.respond([m], "main")
            return prompts
        return go

    def test_owner_voice_note_becomes_text_and_gets_voice_reply(self):
        w = Worker()
        m, sent = voice_msg()
        prompts, posted = self.run_with(w, self.converse(m))
        self.assertEqual(m.content, "(voice note) hey lato turn the lamp warm")
        self.assertIn("Owner", w.stt_calls[0][1]["hint"])
        self.assertIn("voice note", prompts[0])
        self.assertEqual(sent[0][1], "ok, warm it is")                      # "both": text too
        self.assertEqual(w.tts_calls[0]["text"], "ok, warm it is")
        route, form = posted[0]
        payload = json.loads(form[0]["value"])
        self.assertEqual(payload["flags"], 8192)
        self.assertEqual(payload["attachments"][0]["duration_secs"], 1.5)
        self.assertEqual(payload["attachments"][0]["waveform"], "AAAA")
        self.assertEqual(payload["allowed_mentions"], {"parse": []})
        self.assertEqual(form[1]["value"], b"OggS-fake")

    def test_voice_speak_off_means_text_only(self):
        self.settings(voice_speak=False)
        w = Worker()
        m, sent = voice_msg()
        prompts, posted = self.run_with(w, self.converse(m))
        self.assertEqual(m.content, "(voice note) hey lato turn the lamp warm")   # still listens
        self.assertEqual(sent[0][1], "ok, warm it is")
        self.assertEqual(w.tts_calls, [])
        self.assertEqual(posted, [])

    def test_stranger_and_disallowed_never_transcribed(self):
        w = Worker()
        m, _ = voice_msg(author=STRANGER)
        self.run_with(w, lambda: self.lato.dispatch("message", m))
        self.assertEqual(w.stt_calls, [])
        self.assertEqual(m.content, "")
        self.settings(voice_who="owner")
        friends.trust(FRIEND, False)
        self.lato.friends.refresh(force=True)
        m, _ = voice_msg(author=FRIEND)
        self.run_with(w, lambda: self.lato.dispatch("message", m))
        self.assertEqual(w.stt_calls, [])

    def test_off_switch(self):
        self.settings(mod_voice=False)
        w = Worker()
        m, _ = voice_msg()
        self.run_with(w, lambda: self.lato.dispatch("message", m))
        self.assertEqual(w.stt_calls, [])

    def test_voice_only_and_fallback_to_text(self):
        self.settings(voice_reply="voice")
        m, sent = voice_msg()
        _, posted = self.run_with(Worker(), self.converse(m))
        self.assertEqual(sent, [])                    # no text message
        self.assertEqual(len(posted), 1)              # just the voice note
        hist = list(self.lato.history.get(m.channel.id))
        self.assertEqual(hist[-1]["content"], "ok, warm it is")
        self.lato.history.clear()
        self.mod.heard.clear()
        m, sent = voice_msg()
        _, posted = self.run_with(Worker(tts_ok=False), self.converse(m))
        self.assertEqual(posted, [])
        self.assertEqual(sent[0][1], "ok, warm it is")  # fell back to text

    def test_typed_request_for_a_voice_note(self):
        self.settings(voice_reply="text")
        m, sent = fake_msg("lato send me a voice note saying hi")
        w = Worker()
        _, posted = self.run_with(w, self.converse(m, reply="hiii"))
        self.assertEqual(sent[0][1], "hiii")
        self.assertEqual(w.tts_calls[0]["text"], "hiii")
        self.assertEqual(len(posted), 1)
        m, sent = fake_msg("lato what's up")
        w = Worker()
        _, posted = self.run_with(w, self.converse(m, reply="nm"))
        self.assertEqual(posted, [])

    def test_no_token_means_polite_note(self):
        os.environ.pop("VOICE_TOKEN")
        try:
            m, _ = voice_msg()
            w = Worker()
            self.run_with(w, lambda: self.lato.dispatch("message", m))
            self.assertIn("ears aren't set up", m.content)
            self.assertEqual(w.stt_calls, [])
        finally:
            os.environ["VOICE_TOKEN"] = "tok"


if __name__ == "__main__":
    unittest.main()
