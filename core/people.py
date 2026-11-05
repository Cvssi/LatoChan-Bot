"""Who is who: speaker names, mention clean-up, member lookup."""
from __future__ import annotations

import re

import discord

from . import config as C


def speaker(msg) -> str:
    if msg.author.id == C.OWNER_ID:
        return C.USER_NAME
    name = (msg.author.display_name or msg.author.name or "someone").strip()
    return re.sub(r"[^\w .'-]", "", name).lower()[:32] or "someone"


def who(client, m) -> str:
    if client.user and m.author.id == client.user.id:
        return C.BOT_NAME
    return speaker(m)


def clean_content(client, msg) -> str:
    text = msg.content
    if client.user:
        text = re.sub(rf"<@!?{client.user.id}>", C.BOT_NAME, text)
    for u in msg.mentions:  # show other people's mentions as @name so the model can refer to them
        if client.user and u.id == client.user.id:
            continue
        nm = C.USER_NAME if u.id == C.OWNER_ID else (getattr(u, "display_name", None) or u.name)
        text = re.sub(rf"<@!?{u.id}>", "@" + nm.lower(), text)
    return text.strip()


def addressed_to_bot(client, msg) -> bool:
    if client.user in msg.mentions:
        return True
    ref = msg.reference.resolved if msg.reference else None
    return isinstance(ref, discord.Message) and ref.author.id == client.user.id


def member_names(m) -> set[str]:
    return {n for n in ((m.display_name or "").lower(), m.name.lower(), (m.global_name or "").lower()) if n}


def find_member(msg, ref: str):
    """Resolve '@name', 'me', or a name to exactly one member; None if missing or ambiguous."""
    g = msg.guild
    ref = ref.strip().lstrip("@").strip().lower()
    if not g or not ref:
        return None
    if ref in ("me", "myself", C.USER_NAME):
        return g.get_member(C.OWNER_ID)
    if ref in ("you", "yourself", C.BOT_NAME):
        return g.me
    for u in msg.mentions:
        m = g.get_member(u.id)
        if m and ref in member_names(m):
            return m
    pool = list(g.members)
    for test in (lambda n: n == ref, lambda n: n.startswith(ref), lambda n: ref in n):
        hits = {m for m in pool for n in member_names(m) if test(n)}
        if len(hits) == 1:
            return hits.pop()
        if len(hits) > 1:
            return None  # ambiguous: do nothing rather than hit the wrong person
    return None


def find_voice_channel(g, ref: str):
    ref = ref.strip().lstrip("#").lower()
    chans = [c for c in g.voice_channels if c.name.lower() == ref] or [c for c in g.voice_channels if ref in c.name.lower()]
    return chans[0] if len(chans) == 1 else None
