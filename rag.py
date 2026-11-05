"""History retrieval for LatoChan: lets the local model 'know' the owner.

Two jobs:
  ingest:  python rag.py ingest path/to/export.jsonl [more.jsonl ...]
  query:   Retriever().context_for("message text") -> text block for the system prompt

Input format (one JSON object per line):
  {"ts": "2024-05-01T12:34:56", "author": "me" | "other:<name>", "text": "...", "channel": "optional"}

Only the owner's side is used for the "how he writes" examples. Messages from other
people are used only as surrounding conversation context, and can be omitted entirely.

Use EMBED_URL to select an embedding endpoint. The main Ollama endpoint is the fallback.
Brute-force numpy search is fine up to a few hundred thousand chunks; beyond that, swap in
sqlite-vec, LanceDB or Qdrant.
"""
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import requests

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")
STORE_DIR = Path(os.environ.get("RAG_DIR", Path(__file__).parent))
DB = STORE_DIR / "rag_store.sqlite"
VEC = STORE_DIR / "rag_store.npz"
GAP_MINUTES = 30      # a pause this long starts a new conversation chunk
MAX_CHUNK_CHARS = 1200
BATCH = 64


EMBED_URL = os.environ.get("EMBED_URL", os.environ.get("OLLAMA_URL", "http://localhost:11434"))


def embed(texts: list[str]) -> np.ndarray:
    last = None
    for url in dict.fromkeys((EMBED_URL, OLLAMA_URL)):
        try:
            r = requests.post(f"{url}/api/embed", json={"model": EMBED_MODEL, "input": texts}, timeout=600)
            r.raise_for_status()
            break
        except requests.RequestException as e:
            last = e
    else:
        raise last
    v = np.array(r.json()["embeddings"], dtype=np.float32)
    return v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)


def _parse(ts: str):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def chunks_from(path: str):
    """Group consecutive messages into conversation windows."""
    cur, last, size = [], None, 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                m = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = (m.get("text") or "").strip()
            if not text or (text.startswith(("http://", "https://")) and " " not in text):
                continue  # drop empty / bare-link messages
            t = _parse(m.get("ts", ""))
            who = "me" if m.get("author") == "me" else "other"
            gap = (t - last).total_seconds() / 60 if (t and last) else 0
            if cur and (gap > GAP_MINUTES or size > MAX_CHUNK_CHARS):
                yield cur
                cur, size = [], 0
            cur.append({"ts": m.get("ts", ""), "who": who, "text": text})
            size += len(text)
            last = t or last
    if cur:
        yield cur


def ingest(paths: list[str]):
    db = sqlite3.connect(DB)
    db.execute("CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, ts TEXT, text TEXT, my_text TEXT)")
    vecs = [np.load(VEC)["v"]] if VEC.exists() else []
    seen = {r[0] for r in db.execute("SELECT text FROM chunks")}
    batch_rows, batch_texts = [], []

    def flush():
        nonlocal batch_rows, batch_texts
        if not batch_rows:
            return
        allv = np.concatenate([*vecs, embed(batch_texts)])
        db.executemany("INSERT INTO chunks(ts,text,my_text) VALUES(?,?,?)", batch_rows)
        db.commit()
        np.savez_compressed(VEC, v=allv)
        vecs[:] = [allv]
        batch_rows, batch_texts = [], []
        print(".", end="", flush=True)

    for p in paths:
        for c in chunks_from(p):
            text = "\n".join(f"{'Me' if m['who'] == 'me' else 'Them'}: {m['text']}" for m in c)
            if text in seen or not any(m["who"] == "me" for m in c):
                continue
            seen.add(text)
            mine = "\n".join(m["text"] for m in c if m["who"] == "me")
            batch_rows.append((c[0]["ts"], text, mine))
            batch_texts.append(text)
            if len(batch_rows) >= BATCH:
                flush()
    flush()
    print("\ndone")


class Retriever:
    def __init__(self, k: int = 4):
        self.k = k
        self.db = sqlite3.connect(DB, check_same_thread=False)
        self.v = np.load(VEC)["v"]

    def context_for(self, query: str) -> str:
        q = embed([query])[0]
        scores = self.v @ q
        top = np.argsort(-scores)[: self.k]
        rows = [self.db.execute("SELECT text, my_text FROM chunks WHERE id=?", (int(i) + 1,)).fetchone() for i in top]
        rows = [r for r, s in zip(rows, scores[top], strict=True) if r and s > 0.45]
        if not rows:
            return ""
        notes = "\n---\n".join(r[0] for r in rows)
        style = "\n".join(f"- {r[1].splitlines()[0][:200]}" for r in rows if r[1])
        return (
            "Background from past conversations (for understanding only; do not quote it or claim to remember beyond it):\n"
            f"{notes}\n\nHow he tends to write:\n{style}"
        )


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "ingest":
        ingest(sys.argv[2:])
    else:
        print(__doc__)
