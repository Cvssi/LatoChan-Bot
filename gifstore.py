#!/usr/bin/env python3
"""LatoChan gif library: /var/lib/latochan/gifs/<mood>/links.txt, one https link per line (# comments ok).

Stdlib only. Used by `lato-gifs` (and through it by the LatoDash GIFs tab). The bot itself only reads these files.

CLI:
    gifstore.py json            {"moods": {"happy": ["https://..", ..], ..}, "known": [...], "limits": {...}}
    gifstore.py apply           JSON on stdin: {"op": "add"|"remove"|"replace", "moods": {"happy": ["https://..."]}}
                                prints {"added": n, "removed": n, "skipped": [...], "moods": {...}}
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

KNOWN_MOODS = ["waking", "sleepy", "happy", "sad", "annoyed", "bored", "love", "laughing", "confused", "excited", "smug", "shocked"]
MOOD_RE = re.compile(r"^[a-z_]{1,24}$")
MAX_LINK = 500
MAX_PER_MOOD = 300
MAX_MOODS = 40


def gif_dir() -> Path:
    return Path(os.environ.get("GIF_DIR", "/var/lib/latochan/gifs"))


def clean_mood(name) -> str:
    m = re.sub(r"[\s-]+", "_", str(name).strip().lower())
    if not MOOD_RE.match(m):
        raise ValueError(f"bad mood name: {str(name)[:30]!r} (letters and underscores only, up to 24)")
    return m


def parse_links(text: str):
    """Pull https links out of pasted text. Returns (links, skipped) with duplicates removed, order kept."""
    good, bad, seen = [], [], set()
    for line in str(text).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for tok in line.split():
            link = check_link(tok)
            if link is None:
                bad.append(tok[:60])
            elif link not in seen:
                seen.add(link)
                good.append(link)
    return good, bad


def check_link(tok: str):
    tok = tok.strip().strip("<>")
    if not tok.startswith("https://") or len(tok) > MAX_LINK or len(tok) < 12:
        return None
    if any(c in tok for c in ' \t\r\n"\'<>\\`') or any(ord(c) < 33 or ord(c) == 127 for c in tok):
        return None
    return tok


def _file(mood: str) -> Path:
    return gif_dir() / mood / "links.txt"


def read_lines(mood: str):
    try:
        return _file(mood).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def links_of(mood: str):
    seen, out = set(), []
    for ln in read_lines(mood):
        ln = ln.strip()
        if ln.startswith("https://") and ln not in seen:
            seen.add(ln)
            out.append(ln)
    return out


def all_moods() -> dict:
    root = gif_dir()
    names = list(KNOWN_MOODS)
    try:
        for d in sorted(root.iterdir()):
            if d.is_dir() and MOOD_RE.match(d.name) and d.name not in names:
                names.append(d.name)
    except OSError:
        pass
    return {m: links_of(m) for m in names}


def _write(mood: str, lines):
    d = gif_dir() / mood
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o2775)
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".links-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))
        os.chmod(tmp, 0o664)   # the bot runs as another user in the same group
        os.replace(tmp, d / "links.txt")
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class PartialUpdateError(OSError):
    """At least one mood was saved before a later write failed."""


def apply(op: str, moods: dict) -> dict:
    if op not in ("add", "remove", "replace"):
        raise ValueError("op must be add, remove or replace")
    if not isinstance(moods, dict) or not moods:
        raise ValueError("nothing to do")
    existing = all_moods()
    changes = {}
    added = removed = 0
    skipped = []
    names = set()
    for k, v in moods.items():
        mood = clean_mood(k)
        if mood in names:
            raise ValueError(f"duplicate mood name: {mood}")
        names.add(mood)
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise ValueError(f"{mood}: expected a list of links")
        links, bad = parse_links("\n".join(v))
        skipped += bad
        cur = read_lines(mood)
        if op == "add":
            have = {ln.strip() for ln in cur}
            fresh = [ln for ln in links if ln not in have]
            if len(existing.get(mood, ())) + len(fresh) > MAX_PER_MOOD:
                raise ValueError(f"{mood}: too many links (max {MAX_PER_MOOD})")
            if fresh:
                changes[mood] = [*cur, *fresh]
            added += len(fresh)
        elif op == "remove":
            drop = set(links)
            keep = [ln for ln in cur if ln.strip() not in drop]
            removed += len(cur) - len(keep)
            if len(keep) != len(cur):
                changes[mood] = keep
        else:
            if len(links) > MAX_PER_MOOD:
                raise ValueError(f"{mood}: too many links (max {MAX_PER_MOOD})")
            removed += len(existing.get(mood, ()))
            added += len(links)
            changes[mood] = links

    if len(existing.keys() | names) > MAX_MOODS:
        raise ValueError("too many mood folders")
    written = []
    for mood, lines in changes.items():
        try:
            _write(mood, lines)
        except OSError as exc:
            if written:
                raise PartialUpdateError(f"Saved {', '.join(written)} before the write failed for {mood}: {exc}") from exc
            raise
        written.append(mood)
    return {"added": added, "removed": removed, "skipped": skipped[:20], "moods": all_moods()}


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "json"
    if cmd == "json":
        print(json.dumps({"moods": all_moods(), "known": KNOWN_MOODS,
                          "limits": {"per_mood": MAX_PER_MOOD, "moods": MAX_MOODS}}))
    elif cmd == "apply":
        import fcntl

        try:
            req = json.loads(sys.stdin.read(2_000_000) or "{}")
            if not isinstance(req, dict):
                raise ValueError("expected a JSON object")
            root = gif_dir()
            root.mkdir(parents=True, exist_ok=True)
            with (root / ".library.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                result = apply(req.get("op", ""), req.get("moods"))
            print(json.dumps(result))
        except PartialUpdateError as e:
            sys.exit(f"partial update: {e}")
        except (ValueError, OSError) as e:
            sys.exit(f"rejected: {e}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
