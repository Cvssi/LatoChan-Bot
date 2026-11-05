"""LatoChan voice worker: a tiny local HTTP service so the bot never loads the speech models itself.

  POST /stt?max=120&hint=Owner,Alice      body = audio file bytes  -> {"text", "language", "seconds"}
  POST /tts  {"text", "voice", "speed"}                           -> audio/ogg (Opus); headers X-Seconds, X-Waveform
  GET  /health                                                    -> {"ok": true, ...}

Loopback only (localhost), every request needs the shared X-Lato-Token (VOICE_TOKEN in /etc/latovoice.env and
/etc/latochan.env). Audio stays in memory and is never written to disk or logged; transcripts aren't logged either.
One job at a time, so it can't starve the chat model of CPU. Runs as user latovoice (systemd latovoice.service).
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
import time

from aiohttp import web

import audio

log = logging.getLogger("latovoice")
TOKEN = os.environ.get("VOICE_TOKEN", "")
HOST, PORT = "localhost", int(os.environ.get("VOICE_PORT", "8770"))
MAX_UPLOAD = 15 * 1024 * 1024
DEFAULT_VOICE = os.environ.get("VOICE_NAME", "")
lock = asyncio.Lock()


@web.middleware
async def auth(request, handler):
    if request.path != "/health" and not (TOKEN and hmac.compare_digest(request.headers.get("X-Lato-Token", ""), TOKEN)):
        return web.json_response({"error": "unauthorised"}, status=401)
    return await handler(request)


async def run(fn, *a):
    async with lock:
        return await asyncio.to_thread(fn, *a)


async def stt(request):
    data = await request.read()
    if not data:
        return web.json_response({"error": "no audio"}, status=400)
    try:
        mx = min(600.0, float(request.query.get("max", "120")))
    except ValueError:
        mx = 120.0
    t = time.monotonic()
    try:
        res = await run(audio.stt, data, mx, request.query.get("hint", ""))
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=422)
    log.info("stt %.1fs audio in %.1fs (%s)", res["seconds"], time.monotonic() - t, res["language"])
    return web.json_response(res)


async def tts(request):
    try:
        body = await request.json()
        text = str(body.get("text", ""))[:1500]
        voice = str(body.get("voice") or DEFAULT_VOICE)
        speed = float(body.get("speed", 1.0))
    except Exception:
        return web.json_response({"error": "bad request"}, status=400)
    if not text.strip():
        return web.json_response({"error": "no text"}, status=400)
    if not voice:
        return web.json_response({"error": "configure VOICE_NAME to enable speech synthesis"}, status=422)
    t = time.monotonic()
    try:
        res = await run(audio.tts, text, voice, speed)
    except (ValueError, FileNotFoundError) as e:
        return web.json_response({"error": str(e)}, status=422)
    log.info("tts %.1fs audio in %.1fs (%s)", res["seconds"], time.monotonic() - t, voice)
    return web.Response(body=res["ogg"], content_type="audio/ogg",
                        headers={"X-Seconds": str(res["seconds"]), "X-Waveform": res["waveform"]})


async def health(request):
    voices = sorted(p.stem for p in audio.VOICES.glob("*.onnx"))
    return web.json_response({"ok": True, "whisper": audio.WHISPER_MODEL, "voices": voices, "default": DEFAULT_VOICE})


async def warm(app):
    try:     # load both models at start so the first voice note isn't slow
        await run(audio.whisper)
        if DEFAULT_VOICE:
            await run(audio.voice, DEFAULT_VOICE)
        log.info("voice worker models loaded")
    except Exception:
        log.exception("model warm-up failed; will retry on first use")


async def start_warm(app):
    app["warm"] = asyncio.create_task(warm(app))


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not TOKEN:
        raise SystemExit("VOICE_TOKEN is not set")
    app = web.Application(client_max_size=MAX_UPLOAD, middlewares=[auth])
    app.add_routes([web.post("/stt", stt), web.post("/tts", tts), web.get("/health", health)])
    app.on_startup.append(start_warm)
    web.run_app(app, host=HOST, port=PORT, access_log=None, print=None)


if __name__ == "__main__":
    main()
