import json, urllib.request
from envconfig import load_ids
env = load_ids()
import latoids
H = {"Authorization": f"Bot {env['DISCORD_BOT_TOKEN']}", "User-Agent": "DiscordBot (latochan, 1.0)"}
def get(p): return json.load(urllib.request.urlopen(urllib.request.Request("https://discord.com/api/v10" + p, headers=H), timeout=20))
c = get(f"/channels/{latoids.MAIN_CHANNEL}"); print("channel:", c.get("name"), "guild", c.get("guild_id"))
for m in get(f"/channels/{latoids.MAIN_CHANNEL}/messages?limit=5"):
    print(m["id"], m["author"]["username"], m["author"]["id"], "|", m["content"][:120].replace("\n", " "))
