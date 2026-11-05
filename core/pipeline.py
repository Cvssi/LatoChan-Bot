"""The reply pipeline: build the prompt, ask the model, parse tags, let modules act, send.

Order of module hooks (see core/module.py):
  context -> [model] -> on_reply -> (skip check) -> clean_text -> act -> before_send -> [send] -> after_send
  -> [history] -> finish
"""
from __future__ import annotations

import logging
import random
import re
import time

import discord

from . import config as C
from .media import collect_images, has_media
from . import emotes, mix
from .text import lowercase_outside_code
from .turn import Turn

log = logging.getLogger("latochan")

REASON_NOTES = {
    "name": ("\n(system note: they said your name but didn't ping or reply to you. "
             "answer only if you actually want to join in, otherwise reply [[SKIP]].)"),
    "woke": ("\n(system note: you were asleep and this just woke you up. open with a short, slightly grumpy or "
             "sleepy bit about being woken (a few words or a sleepy emoticon like -_- zzz, keep it light, "
             "don't overdo it), then answer them properly.)"),
    "awake": ("\n(system note: you're awake and hanging around in the chat. this wasn't aimed at you. "
              "join in only if you have something to add, otherwise reply [[SKIP]].)"),
    "dm": "\n(system note: this is a private DM to you, not the server chat.)",
    "sleep_cmd": ("\n(system note: he's telling you to go to sleep. reply with a short sleepy goodnight; "
                  "you'll be asleep right after this.)"),
}


WOKE_PLAIN = ("\n(system note: you were asleep and this just woke you up, but you're not grumpy about it this time. "
              "no sleepy bit, just answer them normally.)")


class Pipeline:
    def __init__(self, lato):
        self.lato = lato

    def sleepy_this_time(self) -> bool:
        """wake_sleepy switch + wake_sleepy_chance slider (no dice roll at 100%, so the default changes nothing)."""
        S = self.lato.S
        if not S("wake_sleepy"):
            return False
        chance = S("wake_sleepy_chance")
        return chance >= 100 or (chance > 0 and random.random() * 100 < chance)

    def stay_quiet(self, turn, why: str) -> None:
        """Say nothing, but keep their message in her history (as a [[SKIP]]) so she follows the conversation."""
        cid = turn.msg.channel.id
        self.lato.history.append(cid, {"role": "user", "content": turn.user_line})
        self.lato.history.append(cid, {"role": "assistant", "content": "[[SKIP]]"})
        log.info("stayed quiet for %s: %s", self.lato.speaker(turn.msg), why)

    async def respond(self, msgs: list, reason: str) -> None:
        lato, S = self.lato, self.lato.S
        msg = msgs[-1]  # reply to (and act on) their last message
        is_owner = msg.author.id == C.OWNER_ID
        known = lato.is_known(msg.author.id)
        turn = Turn(msgs=msgs, reason=reason, is_owner=is_owner, stranger=not known, known=known,
                    trusted=is_owner or lato.is_trusted(msg.author.id))

        lines = []
        for m in msgs:
            line = lato.clean_content(m)
            if has_media(m) and known:
                if S("vision_enabled"):
                    i, k = await collect_images(lato.http(), m)
                    turn.images += i
                    if k:
                        line = (line + " [sent " + ", ".join(k) + "]").strip()
                else:
                    line = (line + " [sent a picture you can't see right now]").strip()
            if line:
                lines.append(line)
        if not lines:
            return
        label = " (stranger)" if turn.stranger else " (trusted)" if turn.trusted and not is_owner else ""
        turn.user_line = f"{lato.speaker(msg)}{label}: " + "\n".join(lines)
        note = REASON_NOTES.get(reason, "")
        if reason == "woke" and not self.sleepy_this_time():
            note = WOKE_PLAIN
        turn.llm_line = turn.user_line + note
        turn.joined = " ".join(lato.clean_content(m) for m in msgs)

        turn.mode = mix.decide(lato, turn)          # chatter not aimed at her: on read / react / gif / reply
        if turn.mode == "ignore":
            self.stay_quiet(turn, "left on read (chatter mix)")
            return
        if turn.mode == "react":
            turn.llm_line += mix.REACT_NOTE

        async with lato.locks[msg.channel.id]:
            async with msg.channel.typing():
                notes = await lato.contexts(turn)
                messages = [{"role": "system", "content": lato.system_prompt()}]
                messages += lato.recent_history(msg.channel.id)
                messages.append({"role": "user", "content": turn.llm_line + lato.turn_context(*notes),
                                 **({"images": turn.images[:6]} if turn.images else {})})
                think = bool(S("thinking"))
                log.info("IN   #%s | %s%s", getattr(msg.channel, "name", msg.channel.id),
                         turn.user_line.replace("\n", " / ") if C.CONTENT_LOGS else "[content logging disabled]",
                         f"  ({len(msgs)} msgs, {reason})" if len(msgs) > 1 or reason == "name" else "")
                t0 = time.monotonic()
                try:
                    reply, thinking = await lato.llm.chat(messages, think=think)
                    if thinking and C.CONTENT_LOGS:
                        log.info("THINK %s", thinking.replace("\n", " / ")[:3000])
                    log.info("RAW  (%.1fs%s) %s", time.monotonic() - t0, ", thinking" if think else "", reply.replace("\n", " / ") if C.CONTENT_LOGS else "[content logging disabled]")
                except Exception:
                    log.exception("LLM call failed")
                    return

            turn.raw = reply
            turn.text, turn.calls, turn.dropped = lato.tags.parse(reply)
            if turn.dropped:
                log.info("dropped %d unknown tags", len(turn.dropped))
            if turn.mode == "react":                 # a reaction and nothing else, whatever she wrote
                turn.calls = [c for c in turn.calls if c.name == "REACT"][:1]
                turn.text = ""
                if not turn.calls:
                    self.stay_quiet(turn, "no reaction picked (chatter mix)")
                    return
            await lato.run_hook("on_reply", turn)

            if turn.tags("SKIP") and not turn.has_output:
                lato.history.append(msg.channel.id, {"role": "user", "content": turn.user_line})
                lato.history.append(msg.channel.id, {"role": "assistant", "content": "[[SKIP]]"})
                log.info("stayed quiet for %s", lato.speaker(msg))
                return

            text = lato.run_sync_hook("clean_text", turn.text, turn)
            # The model sometimes copies the "name: " labels we add to user lines; strip any at the start
            names = {C.BOT_NAME, C.USER_NAME, lato.speaker(msg)}
            label = re.compile(r"^\s*(" + "|".join(re.escape(n) for n in names) + r")\s*:\s*", re.I)
            while label.match(text):
                text = label.sub("", text, count=1)
            turn.text = lowercase_outside_code(text)
            lato.history.append(msg.channel.id, {"role": "user", "content": turn.user_line})

            await lato.run_hook("act", turn)
            await lato.run_hook("before_send", turn)
            if turn.blank_text:
                turn.text = ""
            if turn.text:
                am = lato.allowed_mentions()
                out = emotes.expand(turn.text)        # :name: -> <:name:id>; history keeps the short form
                try:
                    await msg.reply(out[:1900], allowed_mentions=am, mention_author=False)
                except (discord.NotFound, discord.HTTPException) as e:
                    log.info("couldn't reply to message %s (%r); posting without the reply link", msg.id, e)
                    await msg.channel.send(out[:1900], allowed_mentions=am)
            turn.history_text = turn.text
            await lato.run_hook("after_send", turn)

            fails = [d for d in turn.done if "failed" in d or "skipped" in d]
            if turn.history_text or fails or turn.data.get("history_fallback"):
                lato.history.append(msg.channel.id, {"role": "assistant",
                                                     "content": turn.history_text or turn.data.get("history_fallback") or "(no text)"})
            if fails:
                lato.history.append(msg.channel.id, {"role": "user", "content": "(system note, not part of your message: " + "; ".join(fails) + ")"})
            await lato.run_hook("finish", turn)
            log.info("replied to %s in #%s%s%s", lato.speaker(msg), getattr(msg.channel, "name", msg.channel.id),
                     turn.data.get("log_suffix", ""), (" [" + "; ".join(turn.done) + "]") if turn.done else "")
