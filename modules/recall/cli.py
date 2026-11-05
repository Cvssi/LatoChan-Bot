"""lato-recall: look inside her long-term memory (recall.db). Changes apply straight away, no restart.

  lato-recall status                 chunk counts per kind/scope, indexing position
  lato-recall search <text>          what she'd recall for that message (main channels, as the owner would see it)
  lato-recall episodes [n]           the last n nightly summaries (default 7)
  lato-recall purge-user <user_id>   forget everything one person said (log + chunks); episodes stay, see below
  lato-recall del <chunk_id>         delete one chunk or summary
  lato-recall reindex                drop all chat chunks and index the log again from the start (summaries kept)
"""
import datetime
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core import config as C            # noqa: E402
from core.store import Store            # noqa: E402
from modules.recall.db import RecallDB  # noqa: E402
from modules.recall.module import EMBED_MODEL, EMBED_URL, TZ, clean_query  # noqa: E402


def embed(text):
    for url in dict.fromkeys((EMBED_URL, C.OLLAMA_URL)):
        try:
            req = urllib.request.Request(url + "/api/embed", data=json.dumps({"model": EMBED_MODEL, "input": [text]}).encode(),
                                         headers={"Content-Type": "application/json"})
            return json.load(urllib.request.urlopen(req, timeout=30))["embeddings"][0]
        except Exception:
            continue
    return None


def when(ts):
    return datetime.datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d %H:%M")


def main(argv):
    db = RecallDB(C.DATA_DIR / "recall.db")
    cmd = argv[0] if argv else "status"
    scopes = [f"channel:{c}" for c in sorted(C.MAIN_CHANNELS)]
    if cmd == "status":
        st = db.stats()
        print("vector search:", "on (sqlite-vec)" if st["vec"] else "OFF (keyword only)")
        for g in st["groups"]:
            print(f"  {g['kind']:<8} {g['scope']:<30} {g['n']:>6} chunks  {when(g['from'])} .. {when(g['to'])}")
        for c in sorted(C.MAIN_CHANNELS):
            p = db.get(f"chan:{c}")
            print(f"  indexed channel {c} up to", when(p["ts"]) if p else "(not yet)")
    elif cmd == "search" and len(argv) > 1:
        q = clean_query(" ".join(argv[1:]))
        hits = db.search(scopes, q, embed("search_query: " + q), k=8, max_distance=1.0)
        for h in hits:
            d = f"{h['distance']:.2f}" if h["distance"] is not None else " kw "
            print(f"[{h['id']}] {h['kind']:<7} dist {d}  {when(h['t_end'])}\n    " + h["text"][:300].replace("\n", "\n    "))
        if not hits:
            print("(nothing)")
    elif cmd == "episodes":
        n = int(argv[1]) if len(argv) > 1 else 7
        rows = db._q("SELECT id, text FROM chunks WHERE kind='episode' ORDER BY t_start DESC LIMIT ?", (n,))
        for cid, text in rows:
            print(f"[{cid}] {text}\n")
        if not rows:
            print("(no summaries yet: the first is written after the first night)")
    elif cmd == "purge-user" and len(argv) == 2:
        uid = int(argv[1])
        st = Store(C.DATA_DIR / "chatlog.db", C.HISTORY_MAX_TURNS * 2)
        n = st._exec("SELECT count(*) FROM messages WHERE author_id=?", (uid,))[0][0]
        st._exec("DELETE FROM messages WHERE author_id=?", (uid,))
        gone = 0
        for (cid, people) in db._q("SELECT id, people FROM chunks WHERE kind='chat'"):
            if uid in json.loads(people):
                db.delete(cid)
                gone += 1
        print(f"deleted {n} logged messages and {gone} chunks from {uid}. the indexer rebuilds the other people's parts "
              f"on its next round only for new messages; run 'lato-recall reindex' to rebuild everything now. "
              f"summaries that mention them: check 'lato-recall episodes' and 'lato-recall del <id>'.")
    elif cmd == "del" and len(argv) == 2:
        db.delete(int(argv[1]))
        print("deleted")
    elif cmd == "reindex":
        for (cid,) in db._q("SELECT id FROM chunks WHERE kind='chat'"):
            db.delete(cid)
        db._q("DELETE FROM state WHERE key LIKE 'chan:%'")
        print("chat chunks dropped; she re-indexes the log in the background (a few minutes per thousand messages)")
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
