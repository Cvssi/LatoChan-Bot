"""Discord slash commands. For now just /help (roadmap #19); later commands are added here by modules.

/help is built from the modules that are switched on, filtered by who's asking (owner / trusted / friends; strangers
get one line), and only the asker sees it (ephemeral). Each module says what it does in its HELP entry:

    HELP = {"title": "lights", "line": "turn the lamp on/off, colours, brightness",
            "examples": ("lato turn the lamp off",), "who": "lights_who"}
    HELP = {}     # nothing for /help (internal feature)

"who" is a level ("owner" / "trusted" / "friends" / "anyone"), a "<feature>_who" setting, or an on/off setting
meaning "friends can use it". Commands are synced to the server (GUILD_ID) at start-up, so
they appear instantly. If /help doesn't show up, the bot needs re-inviting with the applications.commands scope.
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands

from . import access
from . import config as C

log = logging.getLogger("latochan")
LEVELS = ("owner", "trusted", "friends", "anyone")


def level_of(lato, uid: int) -> str:
    return access.level_of(uid == C.OWNER_ID, lato.is_trusted(uid), lato.is_friend(uid))


def who_for(lato, help_: dict) -> str:
    who = help_.get("who", "friends")
    if who in LEVELS:
        return who
    val = lato.setting(who, None)
    if isinstance(val, bool):
        return "friends" if val else "owner"
    return val if val in LEVELS else "owner"


def may_see(level: str, who: str) -> bool:
    if who == "anyone":
        return True
    if level == "stranger":
        return False
    return access.allows_level(level, who)


def build_help(lato, uid: int) -> str:
    lv = level_of(lato, uid)
    if lv == "stranger":
        return f"hi, i'm lato. i mostly just talk to {C.USER_NAME} and his friends, sorry ^^"
    name = C.USER_NAME
    out = ["**what i can do** (only you can see this)",
           "**chatting**: ping me, reply to me or say my name. i nap between chats; any of those wakes me up."
           + (" \"lato go to sleep\" puts me back to bed." if lv == "owner" else "")]
    for m in lato.active():
        h = getattr(m, "HELP", None)
        if not h or not may_see(lv, who_for(lato, h)):
            continue
        line = f"**{h['title']}**: {h['line']}"
        if h.get("examples"):
            line += "\n" + "  ".join(f"`{e}`" for e in h["examples"][:3])
        out.append(line)
    if lv != "owner":
        out.append(f"anything else is up to {name}.")
    return "\n".join(out)[:1990]


def install(lato, client: discord.Client) -> app_commands.CommandTree:
    tree = app_commands.CommandTree(client)

    @tree.command(name="help", description="What LatoChan can do (only you see the answer)")
    async def help_cmd(interaction: discord.Interaction):
        try:
            text = build_help(lato, interaction.user.id)
        except Exception:
            log.exception("/help failed")
            text = "something broke, try again later"
        await interaction.response.send_message(text, ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())
        log.info("/help for %s", getattr(interaction.user, "name", interaction.user.id))
    return tree


def signature(tree: app_commands.CommandTree) -> str:
    import hashlib
    import json
    data = [c.to_dict(tree) if "tree" in c.to_dict.__code__.co_varnames else c.to_dict() for c in tree.get_commands()]
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


async def sync(tree: app_commands.CommandTree, data_dir=None) -> None:
    """Push the commands to the server, but only when they changed (Discord rate-limits syncing hard, and a
    blocked sync used to hold up logging in)."""
    mark = (data_dir / "slash-commands.sha") if data_dir else None
    guild = discord.Object(id=C.GUILD_ID) if C.GUILD_ID else None
    if C.GUILD_ID:
        tree.copy_global_to(guild=guild)
    sig = signature(tree) + f":{C.GUILD_ID}"
    try:
        if mark and mark.exists() and mark.read_text().strip() == sig:
            log.info("slash commands unchanged, not re-syncing")
            return
    except OSError:
        pass
    try:
        done = await tree.sync(guild=guild)
        log.info("slash commands synced: %s", ", ".join("/" + c.name for c in done))
        if mark:
            try:
                mark.write_text(sig)
            except OSError:
                pass
    except discord.Forbidden:
        log.warning("slash commands not synced: re-invite the bot with the applications.commands scope")
    except Exception:
        log.exception("slash command sync failed (the bot works without them)")
