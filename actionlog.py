"""Durable at-most-once admission for owner commands; no command text is retained."""
import os
from pathlib import Path
import sqlite3
import time


def claim(message_id, action):
    path = Path(os.environ.get('ACTION_DB', '/var/lib/latochan/actions.sqlite'))
    con = sqlite3.connect(path, timeout=5)
    try:
        os.chmod(path, 0o660)
        with con:
            con.execute('CREATE TABLE IF NOT EXISTS attempts(message_id INTEGER PRIMARY KEY, action TEXT, attempted INTEGER)')
            inserted = con.execute('INSERT OR IGNORE INTO attempts VALUES(?,?,?)',
                                   (message_id, action, int(time.time()))).rowcount
            # Old messages are separately rejected before admission.
            con.execute('DELETE FROM attempts WHERE attempted < ?', (int(time.time()) - 30 * 86400,))
        return bool(inserted)
    finally:
        con.close()
