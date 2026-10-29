"""Read simple systemd environment assignments without executing shell code."""
import os
from pathlib import Path
import re
import shlex


def read_env(path='/etc/latochan.env'):
    values = {}
    for number, raw in enumerate(Path(path).read_text(encoding='utf-8').splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        key, sep, value = line.partition('=')
        if not sep or not re.fullmatch(r'[A-Z][A-Z0-9_]*', key.strip()):
            raise ValueError(f'Invalid environment assignment at line {number}')
        values[key.strip()] = ' '.join(shlex.split(value, comments=False))
    return values


def load_ids(path='/etc/latochan.env'):
    values = read_env(path)
    for key in ('OWNER_ID', 'BOT_ID', 'GUILD_ID', 'CHANNEL_IDS', 'FRIEND_IDS', 'PRIVATE_CHANNEL_IDS'):
        if key in values:
            os.environ[key] = values[key]
    return values
