#!/usr/bin/env python3
"""LatoChan live settings: one schema shared by the gateway, `lato-config` and the a dashboard web UI.

* Stdlib only, so the same file runs in the bot's venv and in the system python used by `lato-config`.
* The settings file is /var/lib/latochan/settings.json (owner latochan, group of the admin user, mode 660).
  Writers use a stable sidecar lock and atomic replacement. The setgid data directory preserves group access.
* Readers never trust the file: every value is coerced and clamped against SCHEMA, unknown keys are
  ignored, a torn or unreadable file keeps the last good values, a missing key falls back to its default.

CLI (used by lato-config; prints JSON where noted):
    botsettings.py show                  human summary
    botsettings.py json                  {"values": {...}, "schema": [...]}
    botsettings.py get KEY
    botsettings.py set KEY VALUE         VALUE: on/off/toggle for switches, a number or a choice otherwise
    botsettings.py apply                 JSON object {"key": value, ...} on stdin; all-or-nothing; prints the new values
    botsettings.py reset [KEY ...]       back to defaults (all keys if none given)
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from storage import atomic_write, locked

VERSION = "1.1.0"
DEFAULT_PATH = "/var/lib/latochan/settings.json"

# kind: bool | int | float | choice.  group order below is the order the dashboard shows them in.
GROUPS = [
    ("brain", "Brain", "Where her answers come from."),
    ("model", "Voice & memory", "How the local model writes. Changes apply to her next message."),
    ("sleep", "Sleep & wake", "When she is up and when she dozes off."),
    ("gifs", "GIFs", "How often she posts a gif. Links live in the GIFs tab."),
    ("timing", "Chat timing", "How patiently she waits and how chatty she is."),
    ("features", "Abilities", "Things she can do. Turn off what you don't want."),
    ("mood", "Personality", "A gentle push on top of her persona file."),
]


def _s(key, group, label, kind, default, help="", **kw):
    d = {"key": key, "group": group, "label": label, "kind": kind, "default": default, "help": help}
    d.update(kw)
    return d


SCHEMA = [
    _s("thinking", "brain", "Thinking mode", "bool", False,
       "She reasons before answering. Smarter, but 30-90 s slower per reply."),

    _s("temperature", "model", "Creativity", "float", 0.8, "Lower = steadier, higher = wilder.", min=0.1, max=1.5, step=0.05),
    _s("top_p", "model", "Word variety (top-p)", "float", 0.9, "Lower = safer word choices.", min=0.3, max=1.0, step=0.05),
    _s("max_reply_tokens", "model", "Max reply length", "int", 300, "Rough cap on one reply (about 0.75 words per token).",
       min=60, max=1000, step=20, unit="tokens"),
    _s("history_turns", "model", "Conversation memory", "int", 12, "How many back-and-forths she keeps in mind per channel.",
       min=2, max=24, step=1, unit="turns"),
    _s("memory_enabled", "model", "Personal notes", "bool", True, "Reads and writes her per-person notes ([[REMEMBER]])."),

    _s("sleep_mode", "sleep", "Sleep mode", "bool", True, "Off = always awake and answering."),
    _s("awake_min_minutes", "sleep", "Stays awake at least", "float", 3, "After being woken.", min=0.5, max=60, step=0.5, unit="min"),
    _s("awake_max_minutes", "sleep", "Stays awake at most", "float", 10, "After being woken.", min=0.5, max=120, step=0.5, unit="min"),
    _s("friends_can_wake", "sleep", "Friends can wake her", "bool", True, "Off = only you can wake her."),
    _s("chimes_enabled", "sleep", "Wakes on her own", "bool", True, "She sometimes peeks at the chat and says something."),
    _s("chime_min_hours", "sleep", "Check-in at least every", "float", 2, "", min=0.25, max=24, step=0.25, unit="h"),
    _s("chime_max_hours", "sleep", "Check-in at most every", "float", 5, "", min=0.25, max=48, step=0.25, unit="h"),
    _s("chime_wait_seconds", "sleep", "Waits for an answer", "int", 60, "Back to sleep if nobody replies to a check-in.",
       min=10, max=900, step=10, unit="s"),
    _s("quiet_hours_enabled", "sleep", "Quiet hours", "bool", False, "No unprompted check-ins during these hours (the bot's TIMEZONE)."),
    _s("quiet_from_hour", "sleep", "Quiet from", "int", 0, "", min=0, max=23, step=1, unit="o'clock"),
    _s("quiet_to_hour", "sleep", "Quiet until", "int", 8, "", min=0, max=23, step=1, unit="o'clock"),

    _s("gifs_enabled", "gifs", "Post gifs", "bool", True, "Master switch."),
    _s("gif_chance", "gifs", "Chance she posts the gif she picked", "int", 100, "When she picks a gif herself, the odds it actually goes out.",
       min=0, max=100, step=5, unit="%"),
    _s("wake_gif_chance", "gifs", "Chance of a waking-up gif", "int", 30, "When someone wakes her.", min=0, max=100, step=5, unit="%"),
    _s("gif_only", "gifs", "Gif mode (gifs only)", "bool", False,
       "Every reply is just a gif, no words (needs links in the GIFs tab). Ignores the cooldown. Great for a laugh."),
    _s("gif_reply_chance", "gifs", "Replies that come with a gif", "int", 0,
       "Odds a reply includes a gif even if she wouldn't have picked one. 0 = only when she feels like it.",
       min=0, max=100, step=5, unit="%"),
    _s("gif_only_chance", "gifs", "Gif replies with no words", "int", 0,
       "Of the replies that get a gif, how many are the gif alone.", min=0, max=100, step=5, unit="%"),
    _s("gif_cooldown_seconds", "gifs", "Minimum gap between gifs", "int", 0, "Per channel, except when you talk to her or gif mode is on. 0 = no limit.",
       min=0, max=3600, step=15, unit="s"),

    _s("debounce_seconds", "timing", "Waits for you to finish", "float", 6, "Quiet time before she answers a burst of messages.",
       min=1, max=30, step=0.5, unit="s"),
    _s("max_wait_seconds", "timing", "Longest she will wait", "int", 60, "", min=10, max=180, step=5, unit="s"),
    _s("other_cooldown_seconds", "timing", "Gap between replies to one person", "int", 6, "Anti-spam for everyone except you.",
       min=0, max=120, step=1, unit="s"),
    _s("chatter_reply_chance", "timing", "Joins in when not spoken to", "int", 100,
       "While awake, odds she even considers chatting along to friends.", min=0, max=100, step=5, unit="%"),

    _s("vision_enabled", "features", "Looks at pictures", "bool", True, "Images and gif frames sent to her."),
    _s("server_powers", "features", "Server actions", "bool", False,
       "Reactions, VC, pins, timeouts and the other action tags. Off = she can only talk."),

    _s("sass", "mood", "Sass", "int", 5, "5 = as written in her persona.", min=0, max=10, step=1),
    _s("swearing", "mood", "Swearing", "choice", "rare", "", choices=["off", "rare", "more"]),
]

BY_KEY = {s["key"]: s for s in SCHEMA}
DEFAULTS = {s["key"]: s["default"] for s in SCHEMA}
_TRUE = {"on", "true", "1", "yes", "y"}
_FALSE = {"off", "false", "0", "no", "n"}


class SettingError(ValueError):
    pass


def _snap(v, spec):
    lo, hi = spec["min"], spec["max"]
    v = min(max(v, lo), hi)
    if spec["kind"] == "float":
        step = spec.get("step") or 0
        if step:
            v = round(round(v / step) * step, 6)
        return min(max(v, lo), hi)
    return int(round(v))


def coerce(key, value, strict=False):
    """Return a clean value for key. strict=True raises SettingError instead of falling back to the default."""
    spec = BY_KEY.get(key)
    if spec is None:
        raise SettingError(f"unknown setting: {key}")
    kind = spec["kind"]
    try:
        if kind == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and value.strip().lower() in _TRUE | _FALSE:
                return value.strip().lower() in _TRUE
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value in (0, 1):
                return bool(value)
            raise ValueError("expected on/off")
        if kind in ("int", "float"):
            if isinstance(value, bool):
                raise ValueError("expected a number")
            num = float(value)
            if num != num or num in (float("inf"), float("-inf")):
                raise ValueError("expected a finite number")
            if strict and not (spec["min"] <= num <= spec["max"]):
                raise ValueError(f"must be between {spec['min']} and {spec['max']}")
            return _snap(num, spec)
        if kind == "choice":
            v = str(value).strip().lower()
            if v not in spec["choices"]:
                raise ValueError("must be one of " + ", ".join(spec["choices"]))
            return v
    except (TypeError, ValueError) as e:
        if strict:
            raise SettingError(f"{key}: {e}") from None
        return spec["default"]
    raise SettingError(f"{key}: bad schema")  # pragma: no cover


def clean(raw) -> dict:
    """Full, valid settings dict from whatever was in the file."""
    out = dict(DEFAULTS)
    if isinstance(raw, dict):
        for k in DEFAULTS:
            if k in raw:
                out[k] = coerce(k, raw[k])
    return out


def _read_raw(path):
    with locked(str(path) + '.lock', shared=True, blocking=False):
        with open(path, "rb") as f:
            data = f.read(65537)
        if len(data) > 65536:
            raise ValueError("settings file too large")
        obj = json.loads(data.decode("utf-8") or "{}")
        if not isinstance(obj, dict):
            raise ValueError("settings file is not an object")
        return obj


def read_raw(path=None) -> dict:
    """Raw dict from disk (unknown keys kept); {} when the file is missing. Raises if it is corrupt."""
    path = path or os.environ.get("SETTINGS_PATH", DEFAULT_PATH)
    try:
        return _read_raw(path)
    except FileNotFoundError:
        return {}


def write_patch(patch: dict, path=None) -> dict:
    """Validate, merge under a stable lock, and atomically replace the settings file."""
    path = path or os.environ.get("SETTINGS_PATH", DEFAULT_PATH)
    checked = {k: coerce(k, v, strict=True) for k, v in patch.items()}
    with locked(str(path) + '.lock'):
        try:
            data = Path(path).read_bytes()
        except FileNotFoundError:
            data = b'{}'
        if len(data) > 65536:
            raise SettingError("settings file too large")
        cur = json.loads(data.decode('utf-8') or '{}')
        if not isinstance(cur, dict):
            raise SettingError("settings file is not an object; restore a valid copy")
        cur.update(checked)
        data = (json.dumps(cur, indent=1, sort_keys=True) + "\n").encode()
        atomic_write(path, data)
    return clean(cur)


class Live:
    """What the gateway uses: S("key") is cheap, re-reads the file at most every `ttl` seconds."""

    def __init__(self, path=None, ttl=2.0):
        self.path = path or os.environ.get("SETTINGS_PATH", DEFAULT_PATH)
        self.ttl = ttl
        self._vals = dict(DEFAULTS)
        self._at = -1e9
        self._mtime = None

    def refresh(self, force=False):
        now = time.monotonic()
        if not force and now - self._at < self.ttl:
            return
        self._at = now
        try:
            self._vals = clean(_read_raw(self.path))
        except (OSError, ValueError):
            pass  # keep the last good snapshot; never block the gateway on a writer

    def get(self, key):
        self.refresh()
        return self._vals[key]

    __call__ = get

    def set(self, key, value):
        write_patch({key: value}, self.path)
        self.refresh(force=True)

    def all(self):
        self.refresh()
        return dict(self._vals)


# ---------------------------------------------------------------- CLI
def _fmt(spec, v):
    if spec["kind"] == "bool":
        return "ON" if v else "off"
    return f"{v}{(' ' + spec['unit']) if spec.get('unit') else ''}"


def _show(values):
    gname = {g[0]: g[1] for g in GROUPS}
    last = None
    for s in SCHEMA:
        if s["group"] != last:
            last = s["group"]
            print(f"\n[{gname[last]}]")
        print(f"  {s['key']:<24} {_fmt(s, values[s['key']])}")


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "show"
    path = os.environ.get("SETTINGS_PATH", DEFAULT_PATH)
    try:
        raw = read_raw(path)
    except (OSError, ValueError) as e:
        print(f"warning: can't read {path} ({e}); showing defaults", file=sys.stderr)
        raw = {}
    values = clean(raw)
    if cmd == "show":
        # keep the two summary lines the old tool printed first
        print("thinking:", "ON" if values["thinking"] else "off")
        _show(values)
    elif cmd == "json":
        print(json.dumps({"version": VERSION, "values": values, "defaults": DEFAULTS,
                          "groups": [{"id": g[0], "label": g[1], "help": g[2]} for g in GROUPS], "schema": SCHEMA}))
    elif cmd == "get":
        if len(argv) < 3 or argv[2] not in BY_KEY:
            sys.exit("usage: get KEY")
        print(json.dumps(values[argv[2]]))
    elif cmd == "set":
        if len(argv) < 4:
            sys.exit("usage: set KEY VALUE")
        key, val = argv[2], argv[3]
        if key not in BY_KEY:
            sys.exit(f"unknown setting: {key}")
        if val == "toggle":
            if BY_KEY[key]["kind"] != "bool":
                sys.exit("toggle only works on on/off settings")
            val = not values[key]
        try:
            new = write_patch({key: val}, path)
        except SettingError as e:
            sys.exit(str(e))
        spec = BY_KEY[key]
        if key == "thinking":   # the dashboard parses these exact words
            print("thinking: " + ("[ON]  (slower: about 30-90 s per reply)" if new[key] else "[OFF]"))
        else:
            print(f"{key}: {_fmt(spec, new[key])}")
    elif cmd == "apply":
        try:
            patch = json.loads(sys.stdin.read(65536) or "{}")
            if not isinstance(patch, dict):
                raise ValueError("expected a JSON object")
            new = write_patch(patch, path)
        except (ValueError, SettingError) as e:
            sys.exit(f"rejected: {e}")
        print(json.dumps({"values": new}))
    elif cmd == "reset":
        keys = argv[2:] or list(DEFAULTS)
        bad = [k for k in keys if k not in BY_KEY]
        if bad:
            sys.exit("unknown setting: " + ", ".join(bad))
        new = write_patch({k: DEFAULTS[k] for k in keys}, path)
        print(json.dumps({"values": new}))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
