"""Retrieval over Owner's own chat history (../../rag.py). Owner only: his private history never reaches anyone
else's replies. Does nothing until a store has been ingested (see rag.py)."""
import asyncio
import os

from core import config as C

from core.module import Module as Base

try:
    from rag import Retriever
except Exception:   # numpy missing etc.
    Retriever = None


class Module(Base):
    HELP = {}
    ORDER = 20

    async def setup(self):
        self.retriever = None
        if Retriever and os.environ.get("RAG_ENABLED") == "1":
            try:
                self.retriever = Retriever()
            except Exception:
                self.retriever = None
        self.log.info("retrieval %s", "ready" if self.retriever else "off (no store)")

    def status(self) -> str:
        return "on" if getattr(self, "retriever", None) else "idle (no store)"

    async def context(self, turn) -> str:
        if not (getattr(self, "retriever", None) and turn.is_owner and turn.msg.channel.id in C.PRIVATE_CHANNELS):
            return ""
        try:
            return await asyncio.to_thread(self.retriever.context_for, turn.joined)
        except Exception:
            self.log.exception("retrieval failed; continuing without it")
            return ""
