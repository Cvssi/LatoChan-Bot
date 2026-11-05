#!/usr/bin/env python3
"""LatoChan friends list: the people she chats with normally (everyone else is a stranger). Owner is always "owner"
and never needs to be listed.

File: /var/lib/latochan/friends.json (owner latochan, group owner, mode 664),
{"friends": [{"id": "...", "name": "...", "trusted": false}]}.
Trusted friends may ask for more than chat (e.g. the lights); each module decides what (a "<feature>_who" setting:
owner / trusted / friends). Owner is always above everyone.
The bot re-reads it every couple of seconds, so changes apply to her next message with no restart.
If the file doesn't exist yet, the list comes from FRIEND_IDS (env / latoids.py) and the first change creates it.
Stdlib only: the same file runs in the bot, in `lato-friends` (system python) and is called by LatoDash.

CLI (lato-friends):
    lato-friends                        list
    lato-friends add ID [NAME...]       add (or rename) a friend. ID = their Discord user ID (17-20 digits)
    lato-friends remove ID              remove
    lato-friends trust ID / untrust ID  mark a friend as trusted (or not)
    lato-friends json                   {"friends": [...], "owner_id": "...", "bot_id": "..."}  (LatoDash)
    lato-friends apply                  one JSON op on stdin: {"op": "add"|"remove"|"trust"|"untrust", "id": "...", "name": "..."}
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import sys
import time

DEFAULT_PATH = "/var/lib/latochan/friends.json"
MAX_FRIENDS = 200
ID_RE = re.compile(r"^\d{17,20}$")

try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import latoids
except Exception:   # pragma: no cover
    latoids = None


class FriendError(ValueError):
    pass


def path_() -> str:
    return os.environ.get("FRIENDS_PATH", DEFAULT_PATH)


def _seed() -> list[dict]:
    ids = latoids.FRIEND_IDS if latoids else []
    return [{"id": str(i), "name": "", "trusted": False} for i in ids]


def _clean_list(obj) -> list[dict]:
    out, seen = [], set()
    for f in (obj.get("friends") if isinstance(obj, dict) else None) or []:
        if not isinstance(f, dict):
            continue
        fid = str(f.get("id", "")).strip()
        if ID_RE.match(fid) and fid not in seen:
            seen.add(fid)
            out.append({"id": fid, "name": clean_name(f.get("name", "")), "trusted": f.get("trusted") is True})
    return out[:MAX_FRIENDS]


def clean_name(name) -> str:
    return re.sub(r"[\x00-\x1f<>@`]", "", str(name or "")).strip()[:32]


def read(path: str | None = None) -> list[dict]:
    path = path or path_()
    try:
        with open(path, "rb") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                data = f.read()
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except FileNotFoundError:
        return _seed()
    return _clean_list(json.loads(data.decode("utf-8") or "{}"))


def change(fn, path: str | None = None) -> list[dict]:
    """Read-modify-write under an exclusive lock, in place (keeps owner/group so both the bot and owner can write)."""
    path = path or path_()
    existed = os.path.exists(path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o664)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        raw = b""
        while chunk := os.read(fd, 65536):
            raw += chunk
        try:
            cur = _clean_list(json.loads(raw.decode("utf-8"))) if raw.strip() else (_seed() if not existed or not raw else [])
        except ValueError:
            cur = _seed()
        new = fn([dict(f) for f in cur])
        data = (json.dumps({"friends": new, "updated": int(time.time())}, indent=1) + "\n").encode("utf-8")
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, data)
        os.ftruncate(fd, len(data))
        os.fsync(fd)
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
    if not existed:
        try:
            os.chmod(path, 0o664)
        except OSError:
            pass
    return new


def _check_id(fid) -> str:
    fid = str(fid or "").strip()
    if not ID_RE.match(fid):
        raise FriendError("that isn't a Discord user ID (17-20 digits: right-click them in Discord -> Copy User ID)")
    if latoids and fid == str(latoids.OWNER_ID):
        raise FriendError("that's you: you're always her owner, no need to add yourself")
    if latoids and fid == str(latoids.BOT_ID):
        raise FriendError("that's LatoChan herself")
    return fid


def add(fid, name: str = "", path: str | None = None) -> list[dict]:
    fid, name = _check_id(fid), clean_name(name)

    def fn(cur):
        for f in cur:
            if f["id"] == fid:
                if name:
                    f["name"] = name
                return cur
        if len(cur) >= MAX_FRIENDS:
            raise FriendError(f"friends list is full ({MAX_FRIENDS})")
        return cur + [{"id": fid, "name": name, "trusted": False}]
    return change(fn, path)


def trust(fid, on: bool = True, path: str | None = None) -> list[dict]:
    fid = str(fid or "").strip()

    def fn(cur):
        hit = next((f for f in cur if f["id"] == fid), None)
        if not hit:
            raise FriendError("not on the friends list (add them first)")
        hit["trusted"] = bool(on)
        return cur
    return change(fn, path)


def remove(fid, path: str | None = None) -> list[dict]:
    fid = str(fid or "").strip()

    def fn(cur):
        if not any(f["id"] == fid for f in cur):
            raise FriendError("not on the friends list")
        return [f for f in cur if f["id"] != fid]
    return change(fn, path)


def fill_names(names: dict[str, str], path: str | None = None) -> None:
    """The bot fills in display names for friends added by ID only."""
    def fn(cur):
        for f in cur:
            if not f["name"] and names.get(f["id"]):
                f["name"] = clean_name(names[f["id"]])
        return cur
    change(fn, path)


class Live:
    """For the bot: ids() re-reads the file at most every `ttl` seconds; a broken file keeps the last good list."""

    def __init__(self, path: str | None = None, ttl: float = 2.0):
        self.path = path or path_()
        self.ttl = ttl
        self._ids: set[int] = {int(f["id"]) for f in _seed()}
        self._trusted: set[int] = set()
        self._list: list[dict] = _seed()
        self._at = -1e9

    def refresh(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._at < self.ttl:
            return
        self._at = now
        try:
            self._list = read(self.path)
            self._ids = {int(f["id"]) for f in self._list}
            self._trusted = {int(f["id"]) for f in self._list if f["trusted"]}
        except (OSError, ValueError):
            pass

    def ids(self) -> set[int]:
        self.refresh()
        return self._ids

    def trusted_ids(self) -> set[int]:
        self.refresh()
        return self._trusted

    def all(self) -> list[dict]:
        self.refresh()
        return list(self._list)


def main(argv) -> None:
    cmd = argv[1] if len(argv) > 1 else "list"
    try:
        if cmd == "list":
            fl = read()
            print(f"{len(fl)} friends" + ("" if os.path.exists(path_()) else " (from FRIEND_IDS: no friends.json yet)"))
            for f in fl:
                print(f"  {f['id']:<20} {f['name'] or '(name unknown yet)'}{'  [trusted]' if f['trusted'] else ''}")
        elif cmd == "json":
            print(json.dumps({"friends": read(), "owner_id": str(getattr(latoids, "OWNER_ID", "")),
                              "bot_id": str(getattr(latoids, "BOT_ID", "")), "max": MAX_FRIENDS}))
        elif cmd == "add":
            if len(argv) < 3:
                sys.exit("usage: lato-friends add ID [NAME...]")
            add(argv[2], " ".join(argv[3:]))
            print(f"added {argv[2]}. live on her next message.")
        elif cmd in ("remove", "rm", "del"):
            if len(argv) < 3:
                sys.exit("usage: lato-friends remove ID")
            remove(argv[2])
            print(f"removed {argv[2]}. live on her next message.")
        elif cmd in ("trust", "untrust"):
            if len(argv) < 3:
                sys.exit(f"usage: lato-friends {cmd} ID")
            trust(argv[2], cmd == "trust")
            print(f"{argv[2]} is {'now' if cmd == 'trust' else 'no longer'} trusted. live on her next message.")
        elif cmd == "apply":
            op = json.loads(sys.stdin.read(4096) or "{}")
            if not isinstance(op, dict) or op.get("op") not in ("add", "remove", "trust", "untrust"):
                raise FriendError("expected {\"op\": \"add\"|\"remove\"|\"trust\"|\"untrust\", \"id\": ..., \"name\": ...}")
            if op["op"] == "add":
                fl = add(op.get("id"), op.get("name", ""))
            elif op["op"] == "remove":
                fl = remove(op.get("id"))
            else:
                fl = trust(op.get("id"), op["op"] == "trust")
            print(json.dumps({"friends": fl}))
        else:
            sys.exit(__doc__)
    except FriendError as e:
        sys.exit(f"rejected: {e}")


if __name__ == "__main__":
    main(sys.argv)
