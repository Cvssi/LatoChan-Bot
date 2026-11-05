"""recall.db: what she can look back on (memory v2, roadmap #7). One SQLite file, three views of the same chunks:

  chunks      the text + where it came from (kind, scope, channel, people, time span, message ids)
  vec_chunks  sqlite-vec vec0 table, one embedding per chunk (rowid = chunk id)
  fts_chunks  FTS5 keyword index (rowid = chunk id), so search still works if embeddings are down

Kinds: chat (pieces of conversation, expire with the 180-day message log), episode (nightly summary of a channel's
day, kept forever), later note/me/archive. Scopes: "channel:<id>" (later "dm:<user>", "owner"). Search never returns
a scope the caller didn't pass in: the privacy filter is in SQL, not in the prompt.
This file is a cache: everything in it can be rebuilt from chatlog.db (lato-recall reindex).
"""
from __future__ import annotations

import json
import re
import sqlite3
import struct
import threading
import time
from pathlib import Path

DIM = 768
RRF_K = 60


def _pack(vec) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


class RecallDB:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.vec_ok = False
        path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(path, timeout=10, check_same_thread=False)
        self.con.execute("PRAGMA journal_mode=WAL")
        try:
            import sqlite_vec
            self.con.enable_load_extension(True)
            sqlite_vec.load(self.con)
            self.con.enable_load_extension(False)
            self.vec_ok = True
        except Exception:
            self.vec_ok = False
        self.con.executescript("""
            CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, scope TEXT NOT NULL,
                channel_id INTEGER, people TEXT NOT NULL DEFAULT '[]', t_start INTEGER NOT NULL, t_end INTEGER NOT NULL,
                text TEXT NOT NULL, msg_ids TEXT NOT NULL DEFAULT '[]', model TEXT, created INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS chunks_scope ON chunks(scope, t_end);
            CREATE TABLE IF NOT EXISTS chunk_msgs(msg_id INTEGER NOT NULL, chunk_id INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS chunk_msgs_msg ON chunk_msgs(msg_id);
            CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT);
            CREATE VIRTUAL TABLE IF NOT EXISTS fts_chunks USING fts5(text, tokenize='porter unicode61');
        """)
        if self.vec_ok:
            self.con.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(scope text partition key, "
                             f"embedding float[{DIM}] distance_metric=cosine)")
        self.con.commit()

    # ---- small helpers
    def _q(self, sql, args=()):
        with self.lock:
            cur = self.con.execute(sql, args)
            rows = cur.fetchall()
            self.con.commit()
            return rows

    def get(self, key: str, default=None):
        r = self._q("SELECT value FROM state WHERE key=?", (key,))
        return json.loads(r[0][0]) if r else default

    def put(self, key: str, value) -> None:
        self._q("INSERT OR REPLACE INTO state(key, value) VALUES(?,?)", (key, json.dumps(value)))

    # ---- writing
    def add(self, kind: str, scope: str, text: str, t_start: int, t_end: int, *, channel_id=None, people=(),
            msg_ids=(), vec=None, model=None) -> int:
        with self.lock:
            try:
                cur = self.con.execute(
                    "INSERT INTO chunks(kind,scope,channel_id,people,t_start,t_end,text,msg_ids,model,created) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (kind, scope, channel_id, json.dumps(sorted(set(people))), int(t_start), int(t_end), text,
                     json.dumps(list(msg_ids)), model if vec is not None else None, int(time.time())))
                cid = cur.lastrowid
                self.con.execute("INSERT INTO fts_chunks(rowid, text) VALUES(?,?)", (cid, text))
                self.con.executemany("INSERT INTO chunk_msgs(msg_id, chunk_id) VALUES(?,?)", [(m, cid) for m in msg_ids])
                if vec is not None and self.vec_ok:
                    self.con.execute("INSERT INTO vec_chunks(rowid, scope, embedding) VALUES(?,?,?)", (cid, scope, _pack(vec)))
                self.con.commit()
            except Exception:
                self.con.rollback()         # never leave half a chunk behind for the next commit to save
                raise
            return cid

    def delete(self, chunk_id: int) -> None:
        with self.lock:
            self.con.execute("DELETE FROM chunks WHERE id=?", (chunk_id,))
            self.con.execute("DELETE FROM fts_chunks WHERE rowid=?", (chunk_id,))
            self.con.execute("DELETE FROM chunk_msgs WHERE chunk_id=?", (chunk_id,))
            if self.vec_ok:
                self.con.execute("DELETE FROM vec_chunks WHERE rowid=?", (chunk_id,))
            self.con.commit()

    def chunks_with_msg(self, msg_id: int) -> list[dict]:
        rows = self._q("SELECT c.id, c.kind, c.scope, c.channel_id, c.people, c.t_start, c.t_end, c.text, c.msg_ids "
                       "FROM chunk_msgs m JOIN chunks c ON c.id = m.chunk_id WHERE m.msg_id=?", (msg_id,))
        return [self._row(r) for r in rows]

    def prune(self, kind: str, older_than: int) -> int:
        ids = [r[0] for r in self._q("SELECT id FROM chunks WHERE kind=? AND t_end < ?", (kind, older_than))]
        for i in ids:
            self.delete(i)
        return len(ids)

    @staticmethod
    def _row(r) -> dict:
        return {"id": r[0], "kind": r[1], "scope": r[2], "channel_id": r[3], "people": json.loads(r[4]),
                "t_start": r[5], "t_end": r[6], "text": r[7], "msg_ids": json.loads(r[8])}

    # ---- reading
    def search(self, scopes: list[str], qtext: str, qvec=None, *, k: int = 8, before: int | None = None,
               kinds=("chat", "episode"), max_distance: float = 0.5) -> list[dict]:
        """Hybrid search inside the given scopes only. Vector top-N (cosine distance <= max_distance) and
        FTS5 keyword top-N, merged by reciprocal rank fusion. before = ignore chunks ending after this time."""
        if not scopes:
            return []
        ph, kp = ",".join("?" * len(scopes)), ",".join("?" * len(kinds))
        where = f"c.scope IN ({ph}) AND c.kind IN ({kp})" + (" AND c.t_end < ?" if before else "")
        args = [*scopes, *kinds] + ([int(before)] if before else [])
        ranks: dict[int, float] = {}
        dist: dict[int, float] = {}
        if qvec is not None and self.vec_ok:
            # KNN inside each allowed scope (partition key), then the kind/time filter on the hits
            rows = []
            for sc in scopes:
                rows += self._q("SELECT rowid, distance FROM vec_chunks WHERE embedding MATCH ? AND k = ? AND scope = ?",
                                (_pack(qvec), max(32, k * 4), sc))
            rows.sort(key=lambda r: r[1])
            ok = {r[0] for r in self._q(f"SELECT c.id FROM chunks c WHERE {where} AND c.id IN ({','.join(str(r[0]) for r in rows) or '0'})", args)}
            n = 0
            for rid, d in rows:
                if rid in ok and d <= max_distance:
                    n += 1
                    ranks[rid] = ranks.get(rid, 0) + 1 / (RRF_K + n)
                    dist[rid] = d
        words = re.findall(r"[a-z0-9']{3,}", qtext.lower())[:12]
        if words:
            fq = " OR ".join(f'"{w}"' for w in words)
            rows = self._q(f"""SELECT f.rowid, bm25(fts_chunks) AS s FROM fts_chunks f JOIN chunks c ON c.id = f.rowid
                               WHERE fts_chunks MATCH ? AND {where} ORDER BY s LIMIT ?""", (fq, *args, k * 4))
            for n, (rid, _s) in enumerate(rows, 1):
                ranks[rid] = ranks.get(rid, 0) + 1 / (RRF_K + n)
        if not ranks:
            return []
        best = sorted(ranks, key=ranks.get, reverse=True)[:k]
        rows = self._q(f"SELECT id, kind, scope, channel_id, people, t_start, t_end, text, msg_ids FROM chunks "
                       f"WHERE id IN ({','.join(map(str, best))})")
        out = {r[0]: {**self._row(r), "score": ranks[r[0]], "distance": dist.get(r[0])} for r in rows}
        return [out[i] for i in best if i in out]

    def stats(self) -> dict:
        rows = self._q("SELECT kind, scope, count(*), min(t_start), max(t_end) FROM chunks GROUP BY kind, scope")
        return {"vec": self.vec_ok, "groups": [dict(zip(("kind", "scope", "n", "from", "to"), r, strict=True)) for r in rows]}
