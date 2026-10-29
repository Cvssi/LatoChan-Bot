"""LatoChan per-person memory: short notes in SQLite, keyed by Discord user ID.

Explicit owner /lato remember and /lato forget commands manage private notes (handled in gateway.py).
Only a few short notes about the people in the conversation go into each prompt, picked by
word overlap with the message, then by recency, so the prompt stays small.

CLI (run via the lato-memory wrapper):
  lato-memory list [name|id]          show notes (all people, or one)
  lato-memory add <id> <name> <note>  add a note by hand
  lato-memory del <note_id>           delete one note
  lato-memory wipe <user_id>          delete every note about one person
  lato-memory export                  print all notes as an editable text file
  lato-memory import < file.txt       replace all notes with the file's contents
"""
import difflib
import hashlib
import json
import uuid
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

DB_PATH = Path(os.environ.get("MEMORY_DB", "/var/lib/latochan/memory.db"))
MAX_NOTE = 140        # characters per note
MAX_PER_USER = 25     # oldest notes beyond this are dropped
SHOW_SELF = 6         # notes about the speaker per prompt
SHOW_OTHER = 3        # notes about someone mentioned (owner's messages only)
STOP = set("the and but for are was were been you your his her their they them this that with about just really very "
           "have has had not dont can will would like its it's im i'm thing things stuff that".split())


def _db():
    con = sqlite3.connect(DB_PATH, timeout=5)
    con.execute("""CREATE TABLE IF NOT EXISTS notes(
        id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT, note TEXT NOT NULL,
        by_id INTEGER, created INTEGER NOT NULL, updated INTEGER NOT NULL)""")
    con.execute("CREATE INDEX IF NOT EXISTS notes_user ON notes(user_id)")
    try:
        os.chmod(DB_PATH, 0o660)  # the service (latochan) and the admin user's tools both write it
    except OSError:
        pass
    return con


def _run(fn, *, write=False):
    con = _db()
    try:
        with con:
            if write:
                con.execute("BEGIN IMMEDIATE")
            return fn(con)
    finally:
        con.close()


def _clean(note: str) -> str:
    return re.sub(r"\s+", " ", note).strip(" -•.\"'").lower()[:MAX_NOTE]


def _words(text: str) -> set:
    text = re.sub(r"(\w)-(\w)", r"\1\2", text.lower())  # e-bike -> ebike
    return {w for w in re.findall(r"[a-z0-9']+", text) if len(w) > 2 and w not in STOP}


def _overlap(a: set, b: set) -> int:
    """Words in a that match a word in b, allowing plurals/partials like cat~cats, bike~ebike."""
    return sum(1 for w in a if any(w == x or (min(len(w), len(x)) >= 4 and (w in x or x in w)) for x in b))


def add(user_id: int, name: str, note: str, by_id: int | None = None) -> str:
    note = _clean(note)
    if len(note) < 4:
        return "too short"
    now = int(time.time())

    def go(con):
        for nid, old in con.execute("SELECT id, note FROM notes WHERE user_id=?", (user_id,)):
            if difflib.SequenceMatcher(None, old, note).ratio() > 0.8:  # same fact again: refresh it
                con.execute("UPDATE notes SET note=?, name=?, updated=? WHERE id=?", (note, name, now, nid))
                return "updated"
        con.execute("INSERT INTO notes(user_id,name,note,by_id,created,updated) VALUES(?,?,?,?,?,?)",
                    (user_id, name, note, by_id, now, now))
        con.execute("""DELETE FROM notes WHERE user_id=? AND id NOT IN
                       (SELECT id FROM notes WHERE user_id=? ORDER BY updated DESC LIMIT ?)""",
                    (user_id, user_id, MAX_PER_USER))
        return "saved"
    return _run(go, write=True)


def forget(user_id: int, text: str) -> str | None:
    """Delete the single note about this person that best matches text. Returns it, or None."""
    want, wtext = _words(text), _clean(text)

    def go(con):
        best, best_score = None, 0.0
        for nid, note in con.execute("SELECT id, note FROM notes WHERE user_id=?", (user_id,)):
            score = max(_overlap(want, _words(note)) / max(1, len(want)),
                        difflib.SequenceMatcher(None, wtext, note).ratio(),
                        1.0 if wtext and wtext in note else 0.0)
            if score > best_score:
                best, best_score = (nid, note), score
        if best and best_score >= 0.5:
            con.execute("DELETE FROM notes WHERE id=?", (best[0],))
            return best[1]
        return None
    return _run(go, write=True)


def pick(user_id: int, query: str, n: int) -> list[str]:
    q = _words(query)
    rows = _run(lambda con: con.execute("SELECT note, updated FROM notes WHERE user_id=?", (user_id,)).fetchall())
    rows.sort(key=lambda r: (_overlap(q, _words(r[0])), r[1]), reverse=True)
    return [r[0] for r in rows[:n]]


def block(people: list[tuple[int, str, int]], query: str) -> str:
    """people: (user_id, name, how_many). Returns a compact prompt section, or '' if nothing is stored."""
    lines = []
    for uid, name, n in people:
        notes = pick(uid, query, n)
        if notes:
            lines.append(f"- {name}: " + "; ".join(notes))
    if not lines:
        return ""
    return ("your notes about people (use naturally when relevant, don't recite them or mention having notes):\n"
            + "\n".join(lines))


TXT_HEADER = """# LatoChan memory. Edit notes, then: lato-memory import < file.txt
# One note per line under [name DiscordID]. Maximum 25 notes per person.
# If notes changed since export, import stops; export again before editing.
# Do not share this file: it contains personal notes.
#snapshot {snapshot}
"""


def _snapshot(rows):
    return hashlib.sha256(json.dumps(rows, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()


def _rows(con):
    return con.execute("SELECT id,user_id,name,note,by_id,created,updated FROM notes ORDER BY id").fetchall()


def export_text() -> str:
    rows = _run(_rows)
    out, cur = [TXT_HEADER.format(snapshot=_snapshot(rows))], None
    for _, uid, name, note, _, _, _ in sorted(rows, key=lambda r: (r[1], r[0])):
        if uid != cur:
            safe_name = re.sub(r"[\[\]\r\n]", "", name or "someone")[:32]
            out.append(f"\n[{safe_name} {uid}]")
            cur = uid
        out.append(note)
    return "\n".join(out) + "\n"


def import_text(text: str) -> str:
    if len(text.encode()) > 1_000_000:
        raise ValueError("memory import too large")
    snapshot, initial, people, cur = None, False, {}, None
    for n, line in enumerate(text.replace("\r", "").split("\n"), 1):
        line = line.strip()
        if line.startswith('#snapshot '):
            if snapshot is not None:
                raise ValueError('duplicate snapshot marker')
            snapshot = line.split()[1]
            if not re.fullmatch(r'[0-9a-f]{64}', snapshot):
                raise ValueError('invalid snapshot marker')
            continue
        if line == '#initial-import':
            initial = True
            continue
        if not line or line.startswith('#'):
            continue
        m = re.fullmatch(r"\[\s*(.+?)\s+(\d{15,20})\s*\]", line)
        if m:
            cur = int(m[2])
            if cur > 2**63 - 1:
                raise ValueError('Discord ID is out of range')
            people.setdefault(cur, [m[1].strip().lower(), []])
            continue
        if cur is None or line.startswith('['):
            raise ValueError(f'line {n}: expected a [name DiscordID] heading')
        note = _clean(line)
        if len(note) >= 4 and note not in people[cur][1]:
            people[cur][1].append(note)
            if len(people[cur][1]) > MAX_PER_USER:
                raise ValueError(f'line {n}: maximum {MAX_PER_USER} notes per person')
    if snapshot is None and not initial:
        raise ValueError('Export a fresh version first: a #snapshot marker is required.')
    if initial and snapshot is not None:
        raise ValueError('Use either #initial-import or #snapshot, not both.')

    def go(con):
        rows = _rows(con)
        if initial and rows:
            raise ValueError('Initial import requires an empty database; export existing notes first.')
        if snapshot is not None and snapshot != _snapshot(rows):
            raise ValueError('Notes changed since export. Nothing imported; export again and merge your edits.')
        bak = DB_PATH.with_name(DB_PATH.name + '.bak-' + uuid.uuid4().hex)
        # Separate reader can back up while BEGIN IMMEDIATE excludes competing writers.
        source = sqlite3.connect(DB_PATH)
        backup = sqlite3.connect(bak)
        try:
            os.chmod(bak, 0o660)
            source.backup(backup)
        finally:
            source.close()
            backup.close()
        old = {(uid, nt): (c, up, by) for _, uid, _, nt, by, c, up in rows}
        con.execute('DELETE FROM notes')
        now, count = int(time.time()), 0
        for uid, (name, notes) in people.items():
            for nt in notes:
                c, up, by = old.get((uid, nt), (now, now, None))
                con.execute('INSERT INTO notes(user_id,name,note,by_id,created,updated) VALUES(?,?,?,?,?,?)',
                            (uid, name, nt, by, c, up))
                count += 1
        return f'imported {count} notes for {len(people)} people. backup: {bak.name}'
    return _run(go, write=True)


def _cli(argv):
    cmd = argv[0] if argv else "list"
    if cmd == "list":
        who = argv[1].lower() if len(argv) > 1 else None
        rows = _run(lambda con: con.execute(
            "SELECT id, user_id, name, note, datetime(updated,'unixepoch') FROM notes ORDER BY user_id, updated DESC").fetchall())
        for nid, uid, name, note, upd in rows:
            if who and who not in (str(uid), (name or "").lower()):
                continue
            print(f"{nid:>4}  {name or uid:<16} {upd}  {note}")
        if not rows:
            print("(no notes yet)")
    elif cmd == "add" and len(argv) >= 4:
        print(add(int(argv[1]), argv[2].lower(), " ".join(argv[3:])))
    elif cmd == "del" and len(argv) == 2:
        n = _run(lambda con: con.execute("DELETE FROM notes WHERE id=?", (int(argv[1]),)).rowcount, write=True)
        print("deleted" if n else "no such note")
    elif cmd == "wipe" and len(argv) == 2:
        n = _run(lambda con: con.execute("DELETE FROM notes WHERE user_id=?", (int(argv[1]),)).rowcount, write=True)
        print(f"deleted {n} notes")
    elif cmd == "export":
        sys.stdout.write(export_text())
    elif cmd == "import":
        print(import_text(sys.stdin.read(1_000_001)))
    else:
        print(__doc__.split("CLI", 1)[1])


if __name__ == "__main__":
    try:
        _cli(sys.argv[1:])
    except (ValueError, OSError, sqlite3.Error) as exc:
        sys.exit(str(exc))
