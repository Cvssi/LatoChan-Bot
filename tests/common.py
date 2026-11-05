"""Shared test setup: a temp data dir + env, imported before anything from core/."""
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TMP = Path(tempfile.mkdtemp(prefix="lato-test-"))
os.environ.update(DISCORD_BOT_TOKEN="x", SLEEP_MODE="1", TIMEZONE="UTC", GUILD_ID="200", FRIEND_IDS=f'{10**17 + 1}', CHANNEL_IDS="100", PRIVATE_CHANNEL_IDS="100",
                  OWNER_ID=f'{10**17 + 2}',
                  SETTINGS_PATH=str(TMP / "settings.json"), GIF_DIR=str(TMP / "gifs"), MEMORY_DB=str(TMP / "memory.db"), FRIENDS_PATH=str(TMP / "friends.json"),
                  LATO_DATA=str(TMP), PERSONA_PATH=str(HERE / "fixtures" / "persona.md"), LATO_TEST="1")
sys.path[:0] = [str(ROOT), str(HERE)]
