"""Doodle mode: [[DRAW: idea]] -> the model writes a small SVG -> svgsafe cleans it -> rendered to PNG on the CPU
-> posted as a reply. Runs in the background so she keeps chatting while she draws.
Counter: mod/draw/count.json.
"""
import asyncio
import datetime
import io
import json
from zoneinfo import ZoneInfo

import discord

from core import config as C
from core.module import Module as Base
from core.tasks import spawn

from . import svgsafe

PROMPT = ("draw this as a cute, simple doodle in SVG: {idea}\n\n"
          "rules: exactly one <svg> element with viewBox=\"0 0 256 256\". flat colours, thick dark outlines, simple shapes "
          "and paths, at most about 40 elements. fill the canvas, add a simple background colour. no text unless it's asked for. "
          "no images, no links, no scripts, no css. reply with ONLY the svg code.")


class Module(Base):
    HELP = {"title": "drawing", "line": "i doodle stuff (takes a minute or two)", "examples": ("lato draw a cat on a laptop",), "who": "draw_friends"}
    ORDER = 92
    SWITCH = "mod_draw"
    TAGS = {"DRAW": ("DOODLE",)}

    async def setup(self):
        self.busy: set[int] = set()
        self.count_file = self.data_dir() / "count.json"

    def _day(self) -> str:
        return datetime.datetime.now(ZoneInfo(C.TIMEZONE)).date().isoformat()

    def used_today(self) -> int:
        try:
            d = json.loads(self.count_file.read_text())
        except (OSError, ValueError):
            return 0
        return d.get(self._day(), 0)

    def bump(self) -> None:
        self.count_file.write_text(json.dumps({self._day(): self.used_today() + 1}))

    def status(self) -> str:
        return f"on, {self.used_today()} drawings today" if self.enabled() else "off"

    async def finish(self, turn):
        calls = turn.tags("DRAW")
        if not calls or not calls[0].arg:
            return
        S = self.lato.S
        if not (turn.is_owner or (turn.known and S("draw_friends"))):
            self.log.info("draw: %s isn't allowed to ask for drawings", self.lato.speaker(turn.msg))
            return
        if not turn.is_owner and self.used_today() >= S("draw_daily_cap"):
            self.log.info("draw: daily cap reached")
            return
        if turn.cid in self.busy:
            self.log.info("draw: already drawing in this channel")
            return
        spawn(self.draw(turn.msg, calls[0].arg[:300]), "draw")

    async def draw(self, msg, idea: str) -> None:
        cid = msg.channel.id
        self.busy.add(cid)
        try:
            async with msg.channel.typing():
                png = await self.make(idea)
            if not png:
                await msg.add_reaction("💀")       # tried, the drawing came out broken
                return
            await msg.reply(file=discord.File(io.BytesIO(png), "doodle.png"), mention_author=False,
                            allowed_mentions=self.lato.allowed_mentions())
            self.bump()
            self.lato.history.append(cid, {"role": "assistant", "content": f"[posted my drawing of: {idea}]"})
            self.log.info("DRAW posted '%s'", idea)
        except Exception:
            self.log.exception("draw failed")
        finally:
            self.busy.discard(cid)

    async def make(self, idea: str) -> bytes | None:
        msgs = [{"role": "user", "content": PROMPT.format(idea=idea)}]
        try:
            raw, _ = await self.lato.llm.chat(msgs, think=False, max_tokens=int(self.lato.S("draw_max_tokens")))
        except Exception:
            self.log.exception("draw: model call failed")
            return None
        try:
            svg = svgsafe.clean(raw)
            return await asyncio.wait_for(asyncio.to_thread(svgsafe.render_png, svg), timeout=30)
        except (svgsafe.Unsafe, asyncio.TimeoutError) as e:
            self.log.warning("draw: unusable svg for '%s': %s", idea, e)
        except Exception:
            self.log.exception("draw: render failed")
        return None
