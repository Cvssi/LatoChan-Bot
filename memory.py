"""LatoChan per-person memory: short notes in SQLite, keyed by Discord user ID.

She saves notes herself with [[REMEMBER: ...]] / removes them with [[FORGET: ...]] (handled in modules/memory).
Only a few short notes about the people in the conversation go into each prompt, picked by
word overlap with the message, then by recency, so the prompt stays small.

CLI (run via the lato-memory wrapper):
  lato-memory list [name|id]          show notes (all people, or one)
  lato-memory add <id> <name> <note>  add a note by hand
  lato-memory del <note_id>           delete one note
  lato-memory wipe <user_id>          delete every note about one person
  lato-memory export                  print all notes as an editable text file
  lato-memory import < file.txt       replace all notes with the file's contents
  lato-memory me                      what she has decided about herself ([[ME: topic = value]])
  lato-memory me-set <topic> = <value>   set one by hand      lato-memory me-del <topic>   delete one
  lato-memory me-history [topic]      every change she made, oldest first

Her own canon ("about me") is separate from the notes about people: one value per topic, so a new decision replaces
the old one instead of contradicting it; every change is kept in me_history.
"""
import difflib
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from pathlib import Path

DB_PATH = Path(os.environ.get("MEMORY_DB", "/var/lib/latochan/memory.db"))
MAX_NOTE = 140        # characters per note
MAX_PER_USER = 25     # oldest notes beyond this are dropped
SHOW_SELF = 6         # notes about the speaker per prompt
SHOW_OTHER = 3        # notes about someone mentioned (owner's messages only)
STOP = set("the and but for are was were been you your his her their they them this that with about just really very "
           "have has had not dont can will would like its it's im i'm thing things stuff that".split())


_ready: Path | None = None    # the DB file whose tables and permissions were already set up in this process


def _db():
    global _ready
    fresh = _ready != DB_PATH or not DB_PATH.exists()      # set up once per process (and again if the file is replaced)
    con = sqlite3.connect(DB_PATH, timeout=5)
    if fresh:
        _setup(con)
        _ready = DB_PATH
    return con


def _setup(con) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS notes(
        id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT, note TEXT NOT NULL,
        by_id INTEGER, created INTEGER NOT NULL, updated INTEGER NOT NULL)""")
    con.execute("CREATE INDEX IF NOT EXISTS notes_user ON notes(user_id)")
    con.execute("""CREATE TABLE IF NOT EXISTS me(
        topic TEXT PRIMARY KEY, value TEXT NOT NULL, asked_by INTEGER, created INTEGER NOT NULL, updated INTEGER NOT NULL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS me_history(
        id INTEGER PRIMARY KEY, topic TEXT NOT NULL, value TEXT, asked_by INTEGER, ts INTEGER NOT NULL)""")
    try:
        os.chmod(DB_PATH, 0o660)  # the service (latochan) and owner's .bat files both write it
    except OSError:
        pass


def _run(fn, *, write=False):
    con = _db()
    try:
        with con:
            if write:
                con.execute('BEGIN IMMEDIATE')
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
    return _run(go)


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
    return _run(go)


def pick(user_id: int, query: str, n: int) -> list[str]:
    q = _words(query)
    rows = _run(lambda con: con.execute("SELECT note, updated FROM notes WHERE user_id=?", (user_id,)).fetchall())
    rows.sort(key=lambda r: (_overlap(q, _words(r[0])), r[1]), reverse=True)
    return [r[0] for r in rows[:n]]


# ---------------------------------------------------------------- her own canon
MAX_ME = 60           # topics kept (least recently touched dropped beyond this)
SHOW_ME = 8           # per prompt
MAX_TOPIC, MAX_VALUE = 40, 120
SPELL = {"color": "colour", "colors": "colours", "favorite": "favourite", "favourites": "favourite", "fav": "favourite",
         "favs": "favourite", "fave": "favourite"}


def topic_key(topic: str) -> str:
    words = re.findall(r"[a-z0-9']+", topic.lower().replace("_", " "))
    words = [SPELL.get(w, w) for w in words if w not in ("my", "the", "a", "an", "lato", "latochan")]
    return " ".join(words)[:MAX_TOPIC]


def parse_me(arg: str) -> tuple[str, str] | None:
    """'role colour = teal (#2ec4b6)' / 'favourite game: hollow knight' -> (topic, value)"""
    m = re.match(r"\s*(.+?)\s*(?:=|:|->|\bis\b)\s*(.+)", arg or "")
    if not m:
        return None
    topic, value = topic_key(m.group(1)), re.sub(r"\s+", " ", m.group(2)).strip(" .\"'")[:MAX_VALUE]
    return (topic, value) if len(topic) >= 2 and value else None


def _same_topic(con, key: str) -> str:
    """An existing topic that means the same (role colour / colour of my role), else key."""
    best, score = key, 0.0
    kw = set(key.split())
    for (t,) in con.execute("SELECT topic FROM me"):
        r = max(difflib.SequenceMatcher(None, t, key).ratio(), 1.0 if kw and kw == set(t.split()) else 0.0)
        if r > score:
            best, score = t, r
    return best if score >= 0.85 else key


def me_set(topic: str, value: str, asked_by: int | None = None) -> str:
    """Save one of her own decisions. Returns 'saved' / 'same' / 'changed from <old>'."""
    key, value = topic_key(topic), re.sub(r"\s+", " ", value).strip()[:MAX_VALUE]
    if len(key) < 2 or not value:
        return "too short"
    now = int(time.time())

    def go(con):
        k = _same_topic(con, key)
        row = con.execute("SELECT value FROM me WHERE topic=?", (k,)).fetchone()
        if row and row[0].lower() == value.lower():
            con.execute("UPDATE me SET updated=? WHERE topic=?", (now, k))
            return "same"
        if row:
            con.execute("UPDATE me SET value=?, asked_by=?, updated=? WHERE topic=?", (value, asked_by, now, k))
        else:
            con.execute("INSERT INTO me(topic,value,asked_by,created,updated) VALUES(?,?,?,?,?)", (k, value, asked_by, now, now))
            con.execute("DELETE FROM me WHERE topic NOT IN (SELECT topic FROM me ORDER BY updated DESC LIMIT ?)", (MAX_ME,))
        con.execute("INSERT INTO me_history(topic,value,asked_by,ts) VALUES(?,?,?,?)", (k, value, asked_by, now))
        return f"changed from {row[0]}" if row else "saved"
    return _run(go)


def me_del(topic: str) -> bool:
    key = topic_key(topic)

    def go(con):
        k = _same_topic(con, key)
        n = con.execute("DELETE FROM me WHERE topic=?", (k,)).rowcount
        if n:
            con.execute("INSERT INTO me_history(topic,value,asked_by,ts) VALUES(?,NULL,NULL,?)", (k, int(time.time())))
        return bool(n)
    return _run(go)


def me_all() -> list[tuple[str, str, int]]:
    return _run(lambda con: con.execute("SELECT topic, value, updated FROM me ORDER BY updated DESC").fetchall())


def me_block(query: str, n: int = SHOW_ME) -> str:
    rows = me_all()
    if not rows:
        return ""
    q = _words(query)
    rows.sort(key=lambda r: (_overlap(q, _words(r[0] + " " + r[1])), r[2]), reverse=True)
    lines = [f"- {t}: {v}" for t, v, _ in rows[:n]]
    return ("things you've already decided about yourself (stay consistent; change one only on purpose, with [[ME]]):\n"
            + "\n".join(lines))


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
        n = _run(lambda con: con.execute("DELETE FROM notes WHERE id=?", (int(argv[1]),)).rowcount)
        print("deleted" if n else "no such note")
    elif cmd == "wipe" and len(argv) == 2:
        n = _run(lambda con: con.execute("DELETE FROM notes WHERE user_id=?", (int(argv[1]),)).rowcount)
        print(f"deleted {n} notes")
    elif cmd == "me":
        rows = me_all()
        for t, v, up in rows:
            print(f"{t:<28} {time.strftime('%Y-%m-%d %H:%M', time.localtime(up))}  {v}")
        if not rows:
            print("(she hasn't decided anything about herself yet)")
    elif cmd == "me-set" and "=" in " ".join(argv[1:]):
        t, _, v = " ".join(argv[1:]).partition("=")
        print(me_set(t, v))
    elif cmd == "me-del" and len(argv) >= 2:
        print("deleted" if me_del(" ".join(argv[1:])) else "no such topic")
    elif cmd == "me-history":
        k = topic_key(" ".join(argv[1:])) if len(argv) > 1 else None
        for t, v, ts in _run(lambda con: con.execute("SELECT topic, value, ts FROM me_history ORDER BY id").fetchall()):
            if not k or t == k:
                print(f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(ts))}  {t:<28} {v if v is not None else '(deleted)'}")
    elif cmd == "export":
        sys.stdout.write(export_text())
    elif cmd == "import":
        try:
            print(import_text(sys.stdin.read(1_000_001)))
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
    else:
        print(__doc__.split("CLI", 1)[1])


if __name__ == "__main__":
    _cli(sys.argv[1:])
