"""Server powers: reactions for everyone she replies to; voice, moderation, pins, status and the thinking toggle
for the owner only, and only when his own words ask for that kind of thing (OWNER_INTENT).
Switch: server_powers (off = she can only talk; [[TOGGLE_THINKING]] still works)."""
import datetime
import re

import discord

from core import config as C
from core import emotes
from core.module import Module as Base
from core.people import find_voice_channel

NAMES = ("REACT", "JOIN_VC", "LEAVE_VC", "VC_STATUS", "STATUS", "MUTE", "UNMUTE", "DEAFEN", "UNDEAFEN", "DISCONNECT", "MOVE",
         "TIMEOUT", "UNTIMEOUT", "NICK", "PIN", "UNPIN", "DELETE", "TOGGLE_THINKING", "THINKING")
OWNER_ONLY = set(NAMES) - {"REACT"}
MAX_MOD_ACTIONS = 3
BLOCKED_EMOJI = emotes.BLOCKED     # one list, in core/emotes.py

# Server powers only run when Owner's own words ask for that kind of thing. The model picks actions with other
# people's text in its prompt (chat history, saved notes, images), so this stops that text steering her.
OWNER_INTENT = {
    "JOIN_VC": r"vc|voice|join|hop|call|come",
    "LEAVE_VC": r"vc|voice|leave|dip|go away|get out|bye|disconnect",
    "VC_STATUS": r"status", "STATUS": r"status",
    "MUTE": r"mute", "UNMUTE": r"mute",
    "DEAFEN": r"deaf", "UNDEAFEN": r"deaf",
    "DISCONNECT": r"disconnect|\bdc\b|kick|boot|yeet|out of",
    "MOVE": r"move|drag|send|put|throw|chuck",
    "TIMEOUT": r"time ?out|silence|shut", "UNTIMEOUT": r"time ?out|silence|unmute",
    "NICK": r"nick|rename|name",
    "PIN": r"pin", "UNPIN": r"pin",
    "DELETE": r"delete|\bdel\b|remove|nuke|get rid|bin it",
    "TOGGLE_THINKING": r"think|switch|toggle", "THINKING": r"think|switch|toggle",
}
assert OWNER_INTENT.keys() >= OWNER_ONLY, OWNER_ONLY - OWNER_INTENT.keys()
BRACKET_REACT = re.compile(r"\[\s*reacted\s*([^\]\s]*)\s*\]", re.I)
BRACKET_PAST = re.compile(r"\[\s*(reacted|joined|left|muted|unmuted|deafened|undeafened|moved|disconnected|timed out|"
                          r"removed timeout|renamed|pinned|unpinned|deleted|vc status|status set)[^\]]*\]", re.I)


def owner_asked(name: str, request_text: str) -> bool:
    pat = OWNER_INTENT.get(name)
    return bool(pat and re.search(pat, request_text or "", re.I))


def parse_minutes(text: str) -> int:
    m = re.search(r"(\d+)\s*(s|sec|m|min|h|hr|hour)?", text or "")
    if not m:
        return 5
    n, unit = int(m.group(1)), (m.group(2) or "m")[0]
    mins = max(1, n // 60) if unit == "s" else n * 60 if unit == "h" else n
    return min(mins, 60)


class Module(Base):
    HELP = {"title": "server stuff", "line": "join/leave vc, mute or move people, pin messages, my status, thinking on/off", "examples": ("lato join vc", "lato pin that"), "who": "owner"}
    ORDER = 60
    TAGS = dict.fromkeys(NAMES, ())
    TAGS["REACT"] = ("REACTION", "REACTED", "REACTS", "EMOJI")   # spellings the model invents

    def persona(self):
        text = self.persona_file("persona.md" if self.lato.S("server_powers") else "persona-limited.md")
        return "\n\n".join(t for t in (text, emotes.prompt_block()) if t)

    async def on_reply(self, turn):
        acts = [(c.name, c.arg) for c in turn.calls if c.owner == self.NAME]
        turn.data["actions"] = acts
        if acts:
            turn.has_output = True      # a reaction instead of words is a reply: [[SKIP]] doesn't swallow it

    def clean_text(self, turn, text):
        acts = turn.data.setdefault("actions", [])
        for m in BRACKET_REACT.finditer(text):    # "[reacted 😂]" written out as text: do it for real
            if m.group(1):
                acts.append(("REACT", m.group(1)))
        return BRACKET_PAST.sub("", text).strip()

    async def act(self, turn):
        acts = turn.data.get("actions") or []
        if not acts:
            return
        done = await self.do_actions(turn.msg, acts, turn.is_owner, turn.joined)
        turn.done += done
        flag = " ".join(d for d in done if d in ("[ON]", "[OFF]"))
        if flag:
            turn.text = (turn.text + " " + flag).strip()

    async def do_actions(self, msg, acts, is_owner: bool, request_text: str = ""):
        S, lato = self.lato.S, self.lato
        done, mod_count = [], 0
        for name, arg in acts:
            if not S("server_powers") and name not in ("TOGGLE_THINKING", "THINKING"):
                continue   # abilities switched off in settings: she can only talk
            if name in OWNER_ONLY and not is_owner:
                continue
            if name in OWNER_ONLY and not owner_asked(name, request_text):
                self.log.info("action %s blocked: his message didn't ask for it", name)
                done.append(f"{name.lower()} skipped (he didn't ask for that)")
                continue
            if name in ("TOGGLE_THINKING", "THINKING"):
                want = (not S("thinking")) if name == "TOGGLE_THINKING" else arg.strip().lower() in ("on", "true", "1", "yes")
                lato.set_setting("thinking", want)
                done.append("[ON]" if want else "[OFF]")
                continue
            if name not in ("REACT", "STATUS", "JOIN_VC", "LEAVE_VC", "VC_STATUS"):
                mod_count += 1
                if mod_count > MAX_MOD_ACTIONS:
                    done.append(f"{name.lower()} skipped (too many actions)")
                    continue
            target_ref, _, extra = arg.partition("->")
            try:
                await self._one(msg, name, arg, target_ref, extra, done)
            except Exception as e:
                self.log.warning("action %s failed: %r", name, e)
                done.append(f"{name.lower()} failed")
        return done

    async def _one(self, msg, name, arg, target_ref, extra, done):
        lato = self.lato
        if name == "REACT" and arg:
            arg = emotes.react_code(arg.split()[0])
            emo = discord.PartialEmoji.from_str(arg)
            if emo.name and emo.name.lower() in BLOCKED_EMOJI:
                return
            if emo.id and msg.guild and not any(e.id == emo.id for e in msg.guild.emojis):
                return  # custom emoji from somewhere else: can't use it
            await msg.add_reaction(emo if emo.id else arg.split()[0])
            done.append(f"reacted {arg.split()[0]}")
        elif name == "JOIN_VC" and msg.guild:
            vs = msg.author.voice
            if not vs or not vs.channel:
                done.append("join_vc failed: owner not in a voice channel")
                return
            vc = msg.guild.voice_client
            if vc and vc.is_connected():
                await vc.move_to(vs.channel)
            else:
                await vs.channel.connect(self_deaf=True, timeout=20)
            done.append(f"joined #{vs.channel.name}")
            await lato.dispatch("joined_vc", vs.channel)
        elif name == "LEAVE_VC" and msg.guild and msg.guild.voice_client:
            await msg.guild.voice_client.disconnect(force=False)
            done.append("left vc")
        elif name == "VC_STATUS" and msg.guild:
            vc = msg.guild.voice_client
            ch = vc.channel if vc and vc.is_connected() else (msg.author.voice.channel if msg.author.voice else None)
            if ch:
                await lato.client.http.request(discord.http.Route("PUT", "/channels/{channel_id}/voice-status", channel_id=ch.id),
                                               json={"status": arg[:500]})
                done.append(f"vc status set in #{ch.name}")
        elif name == "STATUS":
            lato.custom_activity = discord.CustomActivity(name=arg[:128]) if arg else None
            await lato.client.change_presence(status=lato.presence_status(), activity=lato.custom_activity)
            done.append("status set")
        elif name in ("MUTE", "UNMUTE", "DEAFEN", "UNDEAFEN", "DISCONNECT", "MOVE", "TIMEOUT", "UNTIMEOUT", "NICK"):
            m = lato.find_member(msg, target_ref)
            if not m:
                done.append(f"{name.lower()} failed: couldn't tell who '{target_ref.strip()}' is")
                return
            who = m.display_name
            if name in ("MUTE", "UNMUTE", "DEAFEN", "UNDEAFEN", "DISCONNECT", "MOVE") and not (m.voice and m.voice.channel):
                done.append(f"{name.lower()} failed: {who} isn't in voice")
                return
            if name == "MUTE":
                await m.edit(mute=True)
                done.append(f"muted {who}")
            elif name == "UNMUTE":
                await m.edit(mute=False)
                done.append(f"unmuted {who}")
            elif name == "DEAFEN":
                await m.edit(deafen=True)
                done.append(f"deafened {who}")
            elif name == "UNDEAFEN":
                await m.edit(deafen=False)
                done.append(f"undeafened {who}")
            elif name == "DISCONNECT":
                await m.move_to(None)
                done.append(f"disconnected {who}")
            elif name == "MOVE":
                ch = find_voice_channel(msg.guild, extra)
                if not ch:
                    done.append(f"move failed: no single voice channel matches '{extra.strip()}'")
                    return
                await m.move_to(ch)
                done.append(f"moved {who} to {ch.name}")
            elif name == "TIMEOUT":
                mins = parse_minutes(extra or target_ref)
                await m.timeout(datetime.timedelta(minutes=mins), reason=f"asked by {C.USER_NAME} via latochan")
                done.append(f"timed out {who} for {mins}m")
            elif name == "UNTIMEOUT":
                await m.timeout(None)
                done.append(f"removed timeout for {who}")
            elif name == "NICK":
                new_nick = extra.strip()[:32] or None
                await m.edit(nick=new_nick)
                done.append(f"renamed {who} to {new_nick or 'default'}")
        elif name in ("PIN", "UNPIN", "DELETE"):
            ref = msg.reference.resolved if msg.reference else None
            target = ref if isinstance(ref, discord.Message) else None
            if not target:
                done.append(f"{name.lower()} failed: reply to the message you mean")
                return
            if name == "PIN":
                await target.pin()
                done.append("pinned it")
            elif name == "UNPIN":
                await target.unpin()
                done.append("unpinned it")
            elif name == "DELETE":
                await target.delete()
                done.append("deleted it")
