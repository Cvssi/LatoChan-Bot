"""How she answers chatter that isn't aimed at her: leave it on read, just react, just a gif, or reply.

Settings mix_ignore / mix_react / mix_gif / mix_reply are shares of one pie (LatoDash keeps them at 100%; if they
don't add up they're scaled). Only for "main" (the owner chatting in the main channel) and "awake" (friends' chat
while she's up) turns that aren't addressed to her: a ping, a reply to her, her name or a question always gets a
proper answer. A share she can't do right now (server powers off = no reactions, gifs off / no links = no gifs)
goes to "reply"."""
from __future__ import annotations

import random

from .people import addressed_to_bot
from .router import NAME_RE

MODES = ("ignore", "react", "gif", "reply")
CHATTER = {"main", "awake"}

REACT_NOTE = ("\n(system note: this one gets a reaction, not words. your whole reply is a single [[REACT: emoji]] line "
              "that fits (a unicode emoji or a server emote), nothing else.)")


def addressed(lato, turn) -> bool:
    for m in turn.msgs:
        if addressed_to_bot(lato.client, m) or NAME_RE.search(m.content or ""):
            return True
    return "?" in (turn.joined or "")


def shares(lato) -> dict[str, float]:
    S = lato.S
    w = {m: max(0.0, float(S(f"mix_{m}"))) for m in MODES}
    if not S("server_powers"):
        w["reply"] += w.pop("react")
    gifs = lato.service("gifs")
    if not (S("gifs_enabled") and gifs and gifs.any_mood()):
        w["reply"] += w.pop("gif")
    total = sum(w.values())
    return {m: v / total for m, v in w.items()} if total > 0 else {"reply": 1.0}


def decide(lato, turn, roll: float | None = None) -> str:
    if turn.reason not in CHATTER or turn.stranger or addressed(lato, turn):
        return "reply"
    sh = shares(lato)
    if sh.get("reply", 0) >= 1.0:
        return "reply"
    r = random.random() if roll is None else roll
    acc = 0.0
    for m in MODES:
        acc += sh.get(m, 0.0)
        if r < acc:
            return m
    return "reply"
