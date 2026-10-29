import json, urllib.request
from envconfig import read_env
env = read_env()
H = {"Authorization": f"Bot {env['DISCORD_BOT_TOKEN']}", "User-Agent": "DiscordBot (latochan, 1.0)"}
for g in json.load(urllib.request.urlopen(urllib.request.Request("https://discord.com/api/v10/users/@me/guilds", headers=H), timeout=20)):
    em = json.load(urllib.request.urlopen(urllib.request.Request(f"https://discord.com/api/v10/guilds/{g['id']}/emojis", headers=H), timeout=20))
    print(g["id"], g["name"], len(em), "emojis:", " ".join(f"<{'a' if e.get('animated') else ''}:{e['name']}:{e['id']}>" for e in em)[:1500])
