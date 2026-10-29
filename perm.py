"""Read-only Discord permission diagnostic. Uses IDs from /etc/latochan.env."""
import json
import urllib.request
from envconfig import load_ids

settings = load_ids()
import latoids

headers = {'Authorization': 'Bot ' + settings['DISCORD_BOT_TOKEN'], 'User-Agent': 'DiscordBot (latochan, 1.1)'}


def get(path):
    request = urllib.request.Request('https://discord.com/api/v10' + path, headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


channel = get('/channels/' + latoids.MAIN_CHANNEL)
guild_id = latoids.GUILD_ID or channel['guild_id']
bot_id = latoids.BOT_ID or get('/users/@me')['id']
member = get(f'/guilds/{guild_id}/members/{bot_id}')
roles = {role['id']: role for role in get(f'/guilds/{guild_id}/roles')}
role_ids = set(member['roles']) | {guild_id}
permissions = 0
for rid in role_ids:
    permissions |= int(roles[rid]['permissions'])
admin = bool(permissions & 8)
if not admin:
    overwrites = channel.get('permission_overwrites', [])
    everyone = next((o for o in overwrites if o['id'] == guild_id), {})
    permissions = permissions & ~int(everyone.get('deny', 0)) | int(everyone.get('allow', 0))
    allow = deny = 0
    for overwrite in overwrites:
        if overwrite['type'] == 0 and overwrite['id'] in role_ids - {guild_id}:
            allow |= int(overwrite['allow'])
            deny |= int(overwrite['deny'])
    permissions = permissions & ~deny | allow
    own = next((o for o in overwrites if o['type'] == 1 and o['id'] == bot_id), {})
    permissions = permissions & ~int(own.get('deny', 0)) | int(own.get('allow', 0))
print('Channel:', channel['id'], '| administrator:', admin)
for label, bit in {'View Channel': 10, 'Send Messages': 11, 'Delete Messages': 13, 'Read History': 16,
                   'Connect (check target VC separately)': 20, 'Mute Members': 22, 'Deafen Members': 23,
                   'Move Members': 24, 'Manage Nicknames': 27, 'Timeout Members': 40,
                   'Set VC Status': 48, 'Pin Messages': 51}.items():
    print(f"{'yes' if admin or permissions & (1 << bit) else 'NO ':3}  {label}")
print('Role hierarchy, timeout state, and each target voice channel can impose additional restrictions.')
