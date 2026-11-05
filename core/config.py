"""Static configuration: IDs (from latoids.py), paths and model settings from the environment.

Secrets are never read here at import time; the bot token is only read by app.run().
"""
from __future__ import annotations

import os
from pathlib import Path

import latoids

VERSION = "2.7.1"

OWNER_ID = int(latoids.OWNER_ID or 0)
GUILD_ID = int(latoids.GUILD_ID or 0)
MAIN_CHANNELS = {int(x) for x in latoids.MAIN_CHANNELS}
PRIVATE_CHANNELS = {int(x) for x in latoids.id_list("PRIVATE_CHANNEL_IDS")}
CONTENT_LOGS = os.environ.get("CONTENT_LOGS", "0") == "1"


def primary_channel() -> int:
    """The first configured channel receives scheduled messages."""
    return int(latoids.MAIN_CHANNEL)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODEL = os.environ.get("LLM_MODEL", "llama3.1:8b")
NUM_CTX = int(os.environ.get("NUM_CTX", "8192"))
HISTORY_MAX_TURNS = 24          # buffer size; the "history_turns" setting decides how many she actually sees

BOT_NAME = os.environ.get("BOT_NAME", "latochan")
USER_NAME = os.environ.get("USER_NAME", "owner")
TIMEZONE = os.environ.get("TIMEZONE", "UTC")

ROOT = Path(__file__).resolve().parent.parent
# Her prompt = PERSONA_PATH (who she is, owner-edited) + core/rules.md (how the chat works) + each enabled module's
# persona text. The repository ships a neutral example.
PERSONA_PATH = Path(os.environ.get("PERSONA_PATH", ROOT / "persona.md"))
RULES_PATH = ROOT / "core" / "rules.md"
SETTINGS_PATH = Path(os.environ.get("SETTINGS_PATH", "/var/lib/latochan/settings.json"))
DATA_DIR = Path(os.environ.get("LATO_DATA", "/var/lib/latochan"))
MODULES_DIR = ROOT / "modules"
EMOTES_PATH = Path(os.environ.get("EMOTES_PATH", DATA_DIR / "emotes.txt"))   # "<:name:id> meaning" per line
FRIENDS_PATH = Path(os.environ.get("FRIENDS_PATH", DATA_DIR / "friends.json"))

MEMBERS_INTENT = os.environ.get("MEMBERS_INTENT", "0") == "1"    # 'Server Members Intent' is ON in the portal
PRESENCE_INTENT = os.environ.get("PRESENCE_INTENT", "0") == "1"  # 'Presence Intent' is ON in the portal (activity module)
