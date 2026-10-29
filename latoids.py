"""Non-secret Discord IDs for LatoChan, in ONE place. There are no built-in defaults: set them in /etc/latochan.env. The bot token stays in /etc/latochan.env.

Every value can be overridden by an environment variable of the same name (systemd loads /etc/latochan.env),
so changing channel or owner never means editing code:

    OWNER_ID            the person she obeys (you)
    BOT_ID              LatoChan's own user id
    GUILD_ID            your Discord server
    CHANNEL_IDS         main channel(s), comma separated; the first one is where lato-say posts
    FRIEND_IDS          people she chats with normally when they ping/reply/say her name

Used by gateway.py, lato-say and the diagnostic scripts (chancheck.py, perm.py).
Standalone on purpose: stdlib only, safe to import from anywhere in this folder or /opt/latochan.
"""
import os


def id_list(name: str, default: str = "") -> list[str]:
    values = [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]
    if any(not value.isdigit() or not 0 < int(value) <= 2**63 - 1 for value in values):
        raise ValueError(f'{name} must contain comma-separated positive Discord IDs')
    return values


OWNER_ID = os.environ.get("OWNER_ID", "")
BOT_ID = os.environ.get("BOT_ID", "")
GUILD_ID = os.environ.get("GUILD_ID", "")
MAIN_CHANNELS = id_list("CHANNEL_IDS")
MAIN_CHANNEL = MAIN_CHANNELS[0] if MAIN_CHANNELS else ""
FRIEND_IDS = id_list("FRIEND_IDS")

# env-file keys that are safe to copy into os.environ for scripts that parse /etc/latochan.env themselves
KEYS = ("OWNER_ID", "BOT_ID", "GUILD_ID", "CHANNEL_IDS", "FRIEND_IDS")
