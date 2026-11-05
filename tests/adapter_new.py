"""Golden-test adapter for the modular core (core/ + modules/)."""
import asyncio
import json
import time
from pathlib import Path


class Adapter:
    def __init__(self, tmp: Path):
        from core.app import Lato
        import memory
        self.memory, self.tmp = memory, tmp
        self.lato = lato = Lato()
        asyncio.run(lato.setup_modules())
        self.prompts, self.canned, self.routed = [], "", []

        async def fake_llm(messages, think=False, **kw):
            self.prompts.append(messages[-1]["content"])
            return self.canned, ""

        async def fake_burst(key, b):
            self.routed.append(b.reason)

        lato.llm.chat = fake_llm
        lato.router.run_burst = fake_burst
        self.sleep, self.gifs = lato.module("sleep"), lato.module("gifs")

    def reset(self):
        lato = self.lato
        for d in (lato.router.bursts, lato.last_seen, self.gifs.last_gif, self.sleep.awake_until, self.sleep.chime_open):
            d.clear()
        lato.history.clear()
        self.memory.DB_PATH.unlink(missing_ok=True)
        self.routed.clear()

    def settings(self, kw):
        kw = {"mod_pings": False, **kw}          # v1 had no pings
        Path(self.lato.S.path).write_text(json.dumps(kw))
        self.lato.S.refresh(force=True)

    def set_awake(self, cid, awake):
        if awake:
            self.sleep.awake_until[cid] = time.monotonic() + 600
        else:
            self.sleep.awake_until.pop(cid, None)

    def is_awake(self, cid):
        return self.sleep.is_awake(cid)

    async def respond(self, msgs, reason):
        await self.lato.pipeline.respond(msgs, reason)

    async def route(self, msg):
        await self.lato.router.handle(msg)
        await asyncio.sleep(0)
        return self.routed[-1] if self.routed else None

    def history(self, cid):
        return list(self.lato.history.get(cid))

    def memory_dump(self):
        return self.memory.export_text()

    def thinking(self):
        return self.lato.S("thinking")
