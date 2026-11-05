#!/usr/bin/env python3
"""Live settings schema and CLI for LatoChan."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from storage import atomic_write, locked

VERSION = "2.1.0"
DEFAULT_PATH = "/var/lib/latochan/settings.json"

# kind: bool | int | float | choice.  group order below is the order the dashboard shows them in.
GROUPS = [
    ("brain", "Brain", "Where her answers come from."),
    ("model", "Voice & memory", "How the local model writes. Changes apply to her next message."),
    ("sleep", "Sleep & wake", "When she is up and when she dozes off."),
    ("gifs", "GIFs", "How often she posts a gif. Links live in the GIFs tab."),
    ("replies", "Chatter",
     "How she answers chat that isn't aimed at her (your messages in the main channel, friends' chat while she's "
     "awake). When someone pings her, replies to her, says her name or asks a question, she always answers."),
    ("timing", "Chat timing", "How patiently she waits."),
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
    _s("memory_enabled", "model", "Personal notes", "bool", False, "Reads and writes her per-person notes ([[REMEMBER]])."),

    _s("sleep_mode", "sleep", "Sleep mode", "bool", True, "Off = always awake and answering."),
    _s("awake_min_minutes", "sleep", "Stays awake at least", "float", 3, "After being woken.", min=0.5, max=60, step=0.5, unit="min"),
    _s("awake_max_minutes", "sleep", "Stays awake at most", "float", 10, "After being woken.", min=0.5, max=120, step=0.5, unit="min"),
    _s("wake_sleepy", "sleep", "Sleepy when woken", "bool", True,
       "She opens with a short grumpy/sleepy bit (-_- zzz) when someone wakes her. Off = she just answers."),
    _s("wake_sleepy_chance", "sleep", "How often she's sleepy", "int", 100, "Of the times she's woken up.",
       min=0, max=100, step=5, unit="%"),
    _s("friends_can_wake", "sleep", "Friends can wake her", "bool", True, "Off = only you can wake her."),
    _s("chimes_enabled", "sleep", "Wakes on her own", "bool", True, "She sometimes peeks at the chat and says something."),
    _s("chime_min_hours", "sleep", "Check-in at least every", "float", 2, "", min=0.25, max=24, step=0.25, unit="h"),
    _s("chime_max_hours", "sleep", "Check-in at most every", "float", 5, "", min=0.25, max=48, step=0.25, unit="h"),
    _s("chime_wait_seconds", "sleep", "Waits for an answer", "int", 60, "Back to sleep if nobody replies to a check-in.",
       min=10, max=900, step=10, unit="s"),
    _s("quiet_hours_enabled", "sleep", "Quiet hours", "bool", False, "No unprompted check-ins during these hours (server time)."),
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
    _s("chatter_reply_chance", "replies", "Notices friends' chatter", "int", 100,
       "While awake, how much of friends' chat she even looks at. The rest she doesn't see at all.",
       min=0, max=100, step=5, unit="%"),
    # The four mix_* shares are one "pie" (LatoDash shows them as a single control that always adds up to 100).
    # The bot scales them to 100 if they don't add up; shares she can't do right now (gifs or reactions off) go to reply.
    _s("mix_ignore", "replies", "Leaves it on read", "int", 0, "No reply at all.", min=0, max=100, step=5, unit="%"),
    _s("mix_react", "replies", "Just reacts", "int", 0, "An emoji reaction, no words.", min=0, max=100, step=5, unit="%"),
    _s("mix_gif", "replies", "Just a gif", "int", 0, "A reaction gif, no words.", min=0, max=100, step=5, unit="%"),
    _s("mix_reply", "replies", "Replies", "int", 100, "A normal reply (she can still decide to skip).",
       min=0, max=100, step=5, unit="%"),

    _s("vision_enabled", "features", "Looks at pictures", "bool", True, "Images and gif frames sent to her."),
    _s("server_powers", "features", "Server actions", "bool", True,
       "Reactions, VC, pins, timeouts and the other action tags. Off = she can only talk."),

    _s("sass", "mood", "Sass", "int", 5, "5 = as written in her persona.", min=0, max=10, step=1),
    _s("swearing", "mood", "Swearing", "choice", "rare", "", choices=["off", "rare", "more"]),
]


# ---------------------------------------------------------------- module settings
# Each feature module may have modules/<name>/settings.py with:
#   GROUP = {"id": "...", "label": "...", "help": "..."}     (optional: its own card in the dashboard)
#   SETTINGS = [{"key": ..., "label": ..., "kind": ..., "default": ..., "help": ..., "group": ...}, ...]
# Settings without a "group" go in the module's GROUP; master switches (mod_<name>) use group "modules".
# Stdlib only, plain data: they're loaded here by whatever python runs lato-config.
MODULES_GROUP = ("modules", "Modules", "Whole features on or off. Each one is described in MODULES.md.")


def _load_module_settings():
    import importlib.util
    from pathlib import Path
    base = Path(__file__).resolve().parent / "modules"
    groups, specs = [], []
    if not base.is_dir():
        return groups, specs
    for f in sorted(base.glob("*/settings.py")):
        name = f.parent.name
        try:
            spec = importlib.util.spec_from_file_location(f"_lato_settings_{name}", f)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            g = getattr(m, "GROUP", None)
            if g:
                groups.append((g["id"], g["label"], g.get("help", "")))
            for s in getattr(m, "SETTINGS", []):
                d = dict(s)
                d.setdefault("group", g["id"] if g else MODULES_GROUP[0])
                d.setdefault("help", "")
                d["module"] = name
                specs.append(d)
        except Exception as e:   # a broken module must never break lato-config or the bot
            print(f"warning: ignoring {f}: {e}", file=sys.stderr)
    specs.sort(key=lambda d: d["group"] != MODULES_GROUP[0])   # all on/off switches together, then each module's card
    return groups, specs


_mg, _ms = _load_module_settings()
if _ms:
    GROUPS.append(MODULES_GROUP)
    GROUPS.extend(g for g in _mg if g[0] not in {x[0] for x in GROUPS})
    SCHEMA.extend(s for s in _ms if s["key"] not in {x["key"] for x in SCHEMA})

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
