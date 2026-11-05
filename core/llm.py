"""Talking to Ollama. One place for the model call, so a GPU queue / model routing can be added here later."""
from __future__ import annotations

import logging

import aiohttp

from . import config as C

log = logging.getLogger("latochan")


class LLM:
    def __init__(self, lato):
        self.lato = lato

    async def chat(self, messages: list[dict], think: bool = False, max_tokens: int | None = None,
                   model: str | None = None) -> tuple[str, str]:
        S = self.lato.S
        payload = {
            "model": model or C.MODEL, "messages": messages, "stream": False, "think": think, "keep_alive": "24h",
            "options": {"num_ctx": C.NUM_CTX, "num_predict": 2048 if think else int(max_tokens or S("max_reply_tokens")),
                        "temperature": S("temperature"), "top_p": S("top_p"), "min_p": 0.05, "repeat_penalty": 1.08},
        }
        async with self.lato.http().post(f"{C.OLLAMA_URL}/api/chat", json=payload,
                                         timeout=aiohttp.ClientTimeout(total=600)) as r:
            r.raise_for_status()
            data = await r.json()
        m = data["message"]
        content, thinking = (m.get("content") or "").strip(), (m.get("thinking") or "").strip()
        if think and not content:  # thought itself out of room: answer without thinking
            log.info("Thinking used the response budget. Retrying without it.")
            content, _ = await self.chat(messages, think=False, max_tokens=max_tokens, model=model)
        return content, thinking
