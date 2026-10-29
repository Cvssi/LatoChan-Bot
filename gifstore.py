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
from storage import atomic_write, locked
import os
import re
import sys
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
    for l in read_lines(mood):
        l = l.strip()
        if check_link(l) and l not in seen:
            seen.add(l)
            out.append(l)
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
    d.mkdir(parents=True, exist_ok=True, mode=0o2770)
    os.chmod(d, 0o2770)
    atomic_write(d / 'links.txt', ("\n".join(lines) + ("\n" if lines else "")).encode())


class PartialUpdateError(OSError):
    """A storage failure after earlier, durably applied moods; validation remains all-or-nothing."""


def apply(op: str, moods: dict) -> dict:
    root = gif_dir()
    root.mkdir(parents=True, exist_ok=True, mode=0o2770)
    with locked(root / '.library.lock'):
        return _apply(op, moods)


def _apply(op: str, moods: dict) -> dict:
    if op not in ("add", "remove", "replace"):
        raise ValueError("op must be add, remove or replace")
    if not isinstance(moods, dict) or not moods:
        raise ValueError("nothing to do")
    existing = all_moods()
    new_names = [m for m in (clean_mood(k) for k in moods) if m not in existing]
    if len(existing) + len(new_names) > MAX_MOODS:
        raise ValueError("too many mood folders")
    plan = {}
    skipped = []
    for k, v in moods.items():
        mood = clean_mood(k)
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise ValueError(f"{mood}: expected a list of links")
        links, bad = parse_links("\n".join(v))
        skipped += bad
        if mood in plan:
            raise ValueError("duplicate normalized mood name")
        plan[mood] = links
    # Validate every final count before changing any mood.
    for mood, links in plan.items():
        count = len(set(links_of(mood)) | set(links)) if op == 'add' else len(links)
        if op != 'remove' and count > MAX_PER_MOOD:
            raise ValueError(f'{mood}: too many links (max {MAX_PER_MOOD})')
    added = removed = 0
    completed = []
    for mood, links in plan.items():
        try:
            cur = read_lines(mood)
            have = {l.strip() for l in cur}
            if op == "add":
                fresh = [l for l in links if l not in have]
                if len(links_of(mood)) + len(fresh) > MAX_PER_MOOD:
                    raise ValueError(f"{mood}: too many links (max {MAX_PER_MOOD})")
                if fresh:
                    _write(mood, [l for l in cur] + fresh)
                added += len(fresh)
            elif op == "remove":
                drop = set(links)
                keep = [l for l in cur if l.strip() not in drop]
                removed += len(cur) - len(keep)
                if len(keep) != len(cur):
                    _write(mood, keep)
            else:
                if len(links) > MAX_PER_MOOD:
                    raise ValueError(f"{mood}: too many links (max {MAX_PER_MOOD})")
                removed += len(links_of(mood))
                added += len(links)
                _write(mood, links)
        except OSError as exc:
            raise PartialUpdateError(f"Updated {completed!r}; failed at {mood!r}. Earlier changes remain; inspect and retry.") from exc
        completed.append(mood)
    return {"added": added, "removed": removed, "skipped": skipped[:20], "moods": all_moods()}


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "json"
    if cmd == "json":
        print(json.dumps({"moods": all_moods(), "known": KNOWN_MOODS,
                          "limits": {"per_mood": MAX_PER_MOOD, "moods": MAX_MOODS}}))
    elif cmd == "apply":
        try:
            req = json.loads(sys.stdin.read(2_000_000) or "{}")
            if not isinstance(req, dict):
                raise ValueError("expected a JSON object")
            print(json.dumps(apply(req.get("op", ""), req.get("moods"))))
        except PartialUpdateError as e:
            sys.exit(f"partial update: {e}")
        except (ValueError, OSError) as e:
            sys.exit(f"rejected: {e}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
