"""Voice notes: she hears Discord voice messages (speech-to-text) and can answer with her own (text-to-speech).

How: a voice note from someone allowed (voice_who) in her channels or DMs is sent to the local voice worker
(workers/voice, systemd latovoice.service, loopback + token), and the transcript becomes the message text before
the router sees it, so everything else (name/wake, intent checks, history) works as if they'd typed it. Her reply
then also goes out as a real Discord voice message (voice_reply). Asking for one ("send me a voice note", "say it
out loud") works for typed messages too.
Privacy: audio is never written to disk (bot or worker); strangers' voice notes are never transcribed.
"""
from __future__ import annotations

import json
import os
import re

import aiohttp
import discord
from discord.http import Route

from core import access
from core import config as C
from core.module import Module as Base

VOICE_URL = os.environ.get("VOICE_URL", "http://localhost:8770")
MAX_BYTES = 10 * 1024 * 1024
ASK = re.compile(r"\b(voice ?(note|message|msg|memo)s?|say (it|that|this) (out loud|aloud)|"
                 r"(let me|i wanna|i want to) hear (you|your voice))\b", re.I)
AUDIO_EXT = (".ogg", ".oga", ".opus", ".mp3", ".m4a", ".wav", ".webm", ".flac", ".aac")


def is_audio(a) -> bool:
    try:
        if a.is_voice_message():
            return True
    except Exception:
        pass
    return (a.content_type or "").startswith("audio/") or a.filename.lower().endswith(AUDIO_EXT)


class Module(Base):
    SWITCH = "mod_voice"
    ORDER = 15            # before most modules, so the transcript is in place early
    HELP = {"title": "voice notes", "line": "send me a voice note and i'll listen (i answer in text for now)",
            "examples": ("(hold the mic button in discord)",), "who": "voice_who"}

    async def setup(self):
        self.heard: dict[int, str] = {}       # message id -> transcript (voice notes in this session)

    def token(self) -> str:
        return os.environ.get("VOICE_TOKEN", "")

    def status(self) -> str:
        if not self.enabled():
            return "off"
        return "on" if self.token() else "on, but VOICE_TOKEN missing (run workers/voice/install.sh)"

    def level_allowed(self, uid: int) -> bool:
        lv = access.level_of(uid == C.OWNER_ID, self.lato.is_trusted(uid), self.lato.is_known(uid))
        return access.allows_level(lv, self.lato.S("voice_who"))

    def hint(self) -> str:
        names = [C.BOT_NAME, "Lato", C.USER_NAME.capitalize()]
        try:
            names += [f.get("name", "") for f in self.lato.friends.all()]
        except Exception:
            pass
        return ", ".join(dict.fromkeys(n for n in names if n))

    # ---- incoming: transcribe before the router looks at the message
    async def ev_message(self, msg):
        if msg.author.bot or not msg.attachments or msg.id in self.heard:
            return
        audio = next((a for a in msg.attachments if is_audio(a)), None)
        if not audio or not self.level_allowed(msg.author.id):
            return
        if not (isinstance(msg.channel, discord.DMChannel) or msg.channel.id in C.MAIN_CHANNELS
                or self._replies_to_her(msg)):
            return
        prefix = (msg.content.strip() + " ") if msg.content.strip() else ""
        if audio.size > MAX_BYTES:
            text = "(sent a voice note that's too big for me to listen to)"
        else:
            text = await self.transcribe(audio)
        self.heard[msg.id] = text
        if len(self.heard) > 500:
            self.heard.pop(next(iter(self.heard)))
        msg.content = prefix + text
        try:     # the message log (used e.g. by the lights look-back) gets the words too
            self.lato.store.log_message(msg.id, msg.channel.id, msg.author.id, self.lato.speaker(msg), msg.content[:4000],
                                        int(msg.created_at.timestamp()))
        except Exception:
            pass
        self.log.info("voice note from %s transcribed (%d chars)", self.lato.speaker(msg), len(text))

    def _replies_to_her(self, msg) -> bool:
        ref = getattr(msg, "reference", None)
        res = getattr(ref, "resolved", None) if ref else None
        return bool(res and getattr(res, "author", None) and self.lato.client.user
                    and res.author.id == self.lato.client.user.id)

    async def transcribe(self, att) -> str:
        if not self.token():
            return "(sent a voice note, but my ears aren't set up yet)"
        try:
            data = await att.read()
            mx = self.lato.S("voice_max_s")
            async with self.lato.http().post(f"{VOICE_URL}/stt", data=data, params={"max": str(mx), "hint": self.hint()},
                                             headers={"X-Lato-Token": self.token()},
                                             timeout=aiohttp.ClientTimeout(total=120)) as r:
                d = await r.json(content_type=None)
            if r.status == 422:
                return f"(sent a voice note i couldn't listen to: {d.get('error', 'unreadable')})"
            r.raise_for_status()
            words = (d.get("text") or "").strip()
            return f"(voice note) {words}" if words else "(sent a voice note with no words in it)"
        except Exception as e:
            self.log.warning("transcription failed: %r", e)
            return "(sent a voice note, but i couldn't listen to it right now)"

    # ---- the reply
    def wants_voice(self, turn) -> bool:
        if not self.lato.S("voice_speak"):
            return False
        if self.lato.S("voice_reply") == "text" and not ASK.search(turn.joined or ""):
            return False
        if not self.level_allowed(turn.msg.author.id):
            return False
        from_voice = any(m.id in self.heard for m in turn.msgs)
        return from_voice or bool(ASK.search(turn.joined or ""))

    async def context(self, turn) -> str:
        notes = []
        if any(m.id in self.heard for m in turn.msgs):
            notes.append("their message was a voice note, transcribed automatically, so a word may be misheard")
        if self.token() and self.wants_voice(turn):
            notes.append("your reply will also be sent as a voice note in your voice, so keep it short and speakable "
                         "(no lists or links)")
        return f"({'; '.join(notes)}.)" if notes else ""

    async def before_send(self, turn):
        if not (turn.text and self.token() and self.wants_voice(turn)):
            return
        turn.data["voice_text"] = turn.text
        only_voice = self.lato.S("voice_reply") == "voice" and any(m.id in self.heard for m in turn.msgs)
        if only_voice:
            turn.data["history_fallback"] = turn.text
            turn.text = ""

    async def after_send(self, turn):
        text = turn.data.get("voice_text")
        if not text:
            return
        try:
            await self.send_voice(turn.msg, text)
            turn.done.append("sent a voice note")
        except Exception as e:
            self.log.warning("voice reply failed: %r", e)
            turn.done.append("voice note failed")
            if not turn.history_text:                      # voice-only and it failed: post the words instead
                await turn.msg.channel.send(text[:1900], allowed_mentions=self.lato.allowed_mentions())
                turn.history_text = text

    async def speak(self, text: str) -> tuple[bytes, float, str]:
        body = {"text": text, "speed": self.lato.S("voice_speed") / 100}
        async with self.lato.http().post(f"{VOICE_URL}/tts", json=body, headers={"X-Lato-Token": self.token()},
                                         timeout=aiohttp.ClientTimeout(total=60)) as r:
            if r.status != 200:
                raise RuntimeError(f"tts {r.status}: {(await r.text())[:200]}")
            return await r.read(), float(r.headers.get("X-Seconds", "1")), r.headers.get("X-Waveform", "")

    async def send_voice(self, reply_to, text: str):
        """Post a real Discord voice message (flag 8192, duration + waveform), replying to reply_to."""
        ogg, secs, wave = await self.speak(text)
        payload = {"flags": 8192, "allowed_mentions": {"parse": []},
                   "attachments": [{"id": "0", "filename": "voice-message.ogg", "duration_secs": round(secs, 2),
                                    "waveform": wave}],
                   "message_reference": {"message_id": str(reply_to.id), "fail_if_not_exists": False}}
        form = [{"name": "payload_json", "value": json.dumps(payload)},
                {"name": "files[0]", "value": ogg, "filename": "voice-message.ogg", "content_type": "audio/ogg"}]
        route = Route("POST", "/channels/{channel_id}/messages", channel_id=reply_to.channel.id)
        await self.lato.client.http.request(route, form=form)
