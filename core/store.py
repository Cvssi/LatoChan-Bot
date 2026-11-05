"""Persistent chat state in SQLite (DATA_DIR/chatlog.db).

* History: what the model sees per channel (the same entries as before), now saved so a restart doesn't
  wipe her short-term memory. Loaded once at start-up, written on every append.
* Message log: every message she can see (id, channel, author, text, time). Nothing reads it yet; it is the
  raw material for the memory upgrade (episodes / retrieval) and for modules like activity stats.
  Kept for LOG_KEEP_DAYS, pruned at start-up.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

log = logging.getLogger("latochan")
LOG_KEEP_DAYS = 180


class Store:
    def __init__(self, path: Path, max_entries: int):
        self.path = path
        self.max = max_entries
        self._lock = threading.Lock()
        self._con: sqlite3.Connection | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(path, timeout=5, check_same_thread=False)
            con.executescript("""
                CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY, cid INTEGER NOT NULL, role TEXT NOT NULL,
                    content TEXT NOT NULL, ts INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS history_cid ON history(cid, id);
                CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, cid INTEGER NOT NULL, author_id INTEGER NOT NULL,
                    author TEXT, content TEXT, ts INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS messages_cid ON messages(cid, ts);
            """)
            con.execute("DELETE FROM messages WHERE ts < ?", (int(time.time()) - LOG_KEEP_DAYS * 86400,))
            con.commit()
            self._con = con
        except Exception:
            log.exception("chat store unavailable (%s); running without persistence", path)

    def _exec(self, sql: str, args=()):
        if not self._con:
            return None
        try:
            with self._lock:
                cur = self._con.execute(sql, args)
                self._con.commit()
                return cur.fetchall()
        except Exception:
            log.exception("chat store write failed")
            return None

    def load_history(self) -> dict[int, list[dict]]:
        rows = self._exec("SELECT cid, role, content FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY cid ORDER BY id DESC) rn "
                          "FROM history) WHERE rn <= ? ORDER BY id", (self.max,)) or []
        out: dict[int, list[dict]] = defaultdict(list)
        for cid, role, content in rows:
            out[cid].append({"role": role, "content": content})
        return out

    def add_history(self, cid: int, entry: dict) -> None:
        self._exec("INSERT INTO history(cid, role, content, ts) VALUES(?,?,?,?)",
                   (cid, entry["role"], entry["content"], int(time.time())))

    def trim_history(self) -> None:
        self._exec("DELETE FROM history WHERE id IN (SELECT id FROM (SELECT id, ROW_NUMBER() OVER "
                   "(PARTITION BY cid ORDER BY id DESC) rn FROM history) WHERE rn > ?)", (self.max,))

    def recent_from(self, cid: int, author_id: int, since_ts: int, limit: int = 5) -> list[str]:
        """The newest messages one person sent in one channel since since_ts (newest first)."""
        rows = self._exec("SELECT content FROM messages WHERE cid=? AND author_id=? AND ts>=? ORDER BY ts DESC, id DESC LIMIT ?",
                          (cid, author_id, since_ts, limit)) or []
        return [r[0] or "" for r in rows]

    def messages_after(self, cid: int, ts: int, mid: int, limit: int = 2000) -> list[tuple]:
        """(id, author_id, author, content, ts) after the (ts, id) position, oldest first."""
        return self._exec("SELECT id, author_id, author, content, ts FROM messages WHERE cid=? AND (ts > ? OR (ts = ? AND id > ?)) "
                          "ORDER BY ts, id LIMIT ?", (cid, ts, ts, mid, limit)) or []

    def messages_between(self, cid: int, t0: int, t1: int) -> list[tuple]:
        return self._exec("SELECT id, author_id, author, content, ts FROM messages WHERE cid=? AND ts >= ? AND ts < ? "
                          "ORDER BY ts, id", (cid, t0, t1)) or []

    def messages_by_author(self, cid: int, author_id: int) -> list[tuple]:
        """Everything one person said in one channel (id, author_id, author, content, ts), oldest first."""
        return self._exec("SELECT id, author_id, author, content, ts FROM messages WHERE cid=? AND author_id=? "
                          "ORDER BY ts, id", (cid, author_id)) or []

    def messages_by_ids(self, ids: list[int]) -> list[tuple]:
        if not ids:
            return []
        return self._exec(f"SELECT id, author_id, author, content, ts FROM messages WHERE id IN ({','.join('?' * len(ids))}) "
                          "ORDER BY ts, id", tuple(ids)) or []

    def delete_message(self, mid: int) -> None:
        self._exec("DELETE FROM messages WHERE id=?", (mid,))

    def update_message(self, mid: int, content: str) -> None:
        self._exec("UPDATE messages SET content=? WHERE id=?", (content, mid))

    def log_message(self, mid: int, cid: int, author_id: int, author: str, content: str, ts: int) -> None:
        self._exec("INSERT OR IGNORE INTO messages(id, cid, author_id, author, content, ts) VALUES(?,?,?,?,?,?)",
                   (mid, cid, author_id, author, content, ts))


class History:
    """Per-channel conversation buffer. Same shape as v1's dict of deques, plus persistence."""

    def __init__(self, store: Store | None, max_entries: int, channels: set[int] | None = None):
        self.store = store
        self.channels = channels
        self.max = max_entries
        self._h: dict[int, deque] = defaultdict(lambda: deque(maxlen=max_entries))
        if store:
            for cid, entries in store.load_history().items():
                if self.can_persist(cid):
                    self._h[cid].extend(entries)
            store.trim_history()

    def can_persist(self, cid: int) -> bool:
        return self.channels is None or cid in self.channels

    def get(self, cid: int) -> deque:
        return self._h[cid]

    def append(self, cid: int, entry: dict) -> None:
        self._h[cid].append(entry)
        if self.store and self.can_persist(cid):
            self.store.add_history(cid, entry)

    def recent(self, cid: int, turns: int) -> list:
        return list(self._h[cid])[-turns * 2:]

    def clear(self) -> None:
        self._h.clear()
