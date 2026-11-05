"""Lights: Meross/Refoss smart bulbs on the LAN, controlled locally (no cloud) with [[LIGHT: <device> <what>]].

Config:
  devices  /var/lib/latochan/mod/lights/devices.json   [{"name": "living room lamp", "host": "lamp.example.com", "aliases": ["lamp"]}]
  key      /var/lib/latochan/mod/lights/key              (our own key after re-keying the lamp; see LIGHTS-SETUP.md)
           MEROSS_KEY in the environment overrides it    (manual testing only)
Safety: who may use it = lights_who (owner / trusted / friends, see core/access.py); the requester's own words must
mention the lights (other people's text, saved notes or web pages can't switch them); only the hosts in
devices.json are ever contacted.
"""
import asyncio
import json
import os
import re
import time

from core import access
from core.module import Module as Base

from . import meross, mqtt

INTENT = re.compile(r"\b(?:lights?|lighting|lighter|lamps?|bulbs?|dark(?:er|est|ness|en(?:ed|ing)?)?|bright(?:er|est|ness|en(?:ed|ing)?)?|"
                    r"dim(?:s|mer|med|ming)?|colou?r(?:s|ed|ing)?|glow(?:s|ing|ed)?)\b", re.I)    # whole words: not "slight", "dimension", "Brighton"
COLOURS = {"red": 0xFF0000, "orange": 0xFF7A00, "yellow": 0xFFD000, "green": 0x00FF40, "cyan": 0x00E5FF,
           "blue": 0x0040FF, "purple": 0x8A2BE2, "pink": 0xFF4FB0, "magenta": 0xFF00FF}
TEMPS = {"warm": 1, "white": 50, "neutral": 50, "cool": 100, "cold": 100, "daylight": 100}


def parse(arg: str) -> dict:
    """'lamp blue 40%' -> {"power": None, "rgb": 0x0040FF, "temperature": None, "brightness": 40, "rest": "lamp"}"""
    words = arg.lower().replace(",", " ").split()
    out = {"power": None, "rgb": None, "temperature": None, "brightness": None}
    rest = []
    for w in words:
        if w in ("on", "off", "toggle"):
            out["power"] = w
        elif m := re.fullmatch(r"(\d{1,3})%?", w):
            out["brightness"] = max(1, min(100, int(m.group(1))))
        elif w in ("dim", "dimmer", "low"):
            out["brightness"] = 20
        elif w in ("bright", "brighter", "full", "max"):
            out["brightness"] = 100
        elif w in COLOURS:
            out["rgb"] = COLOURS[w]
        elif w in TEMPS:
            out["temperature"] = TEMPS[w]
        else:
            rest.append(w)
    out["rest"] = " ".join(rest)
    return out


class Module(Base):
    HELP = {"title": "lights", "line": "the lamp: on/off, colours, brightness, or just ask if it's on", "examples": ("lato lamp off", "lato make the lamp blue and dim it"), "who": "lights_who"}
    ORDER = 85
    SWITCH = "mod_lights"
    TAGS = {"LIGHT": ("LIGHTS", "LAMP")}

    async def setup(self):
        self.key = os.environ.get("MEROSS_KEY", "")
        self.devices_file = self.data_dir() / "devices.json"
        self._last_hunt: dict[str, float] = {}

    async def loop(self):
        """Keeps re-keyed devices bound on the local MQTT broker (answers their Bind). HTTP control works without it."""
        while True:
            if self.enabled() and self.current_key() and self.devices():
                try:
                    await mqtt.serve(self.current_key, self.log)
                except (OSError, ConnectionError, asyncio.IncompleteReadError, asyncio.TimeoutError) as e:
                    self.log.info("lights: local MQTT unavailable (%s); retrying in 60 s", type(e).__name__)
            await asyncio.sleep(60)

    async def relocate(self, dev: dict) -> str | None:
        """The light stopped answering: look for its MAC on the LAN (the router may have given it a new IP).
        Updates devices.json and returns the new IP, or None. At most once per 5 minutes per device."""
        mac = (dev.get("mac") or "").lower()
        now = time.monotonic()
        if not mac or now - self._last_hunt.get(mac, -1e9) < 300:
            return None
        self._last_hunt[mac] = now
        base = ".".join(dev["host"].split(":")[0].split(".")[:3])

        async def poke(ip):
            try:
                _, w = await asyncio.wait_for(asyncio.open_connection(ip, 80), 0.8)
                w.close()
            except Exception:
                pass
        await asyncio.gather(*(poke(f"{base}.{i}") for i in range(1, 255)))
        try:
            with open("/proc/net/arp") as fh:
                arp = {f[3].lower(): f[0] for f in (ln.split() for ln in fh.read().splitlines()[1:]) if len(f) >= 4}
        except OSError:
            return None
        ip = arp.get(mac)
        if not ip or ip == dev["host"]:
            return None
        devs = self.devices()
        for d in devs:
            if d["name"] == dev["name"]:
                d["host"] = ip
        try:
            self.devices_file.write_text(json.dumps(devs, indent=1) + "\n")
        except OSError:
            self.log.exception("lights: couldn't save the new IP")
        self.log.info("LIGHTS %s moved %s -> %s (found by MAC)", dev["name"], dev["host"], ip)
        dev["host"] = ip
        return ip

    def current_key(self) -> str:
        """Env MEROSS_KEY (manual override) wins; else the local key file, re-read each time (no restart after re-keying)."""
        if self.key:
            return self.key
        try:
            return (self.data_dir() / "key").read_text().strip()
        except OSError:
            return ""

    def devices(self) -> list[dict]:
        try:
            d = json.loads(self.devices_file.read_text())
            return [x for x in d if isinstance(x, dict) and x.get("name") and x.get("host")]
        except (OSError, ValueError):
            return []

    def status(self) -> str:
        if not self.enabled():
            return "off"
        if not self.devices():
            return "on but no devices configured"
        return "on" if self.current_key() else "on but no key (see LIGHTS-SETUP.md)"

    def find(self, text: str) -> dict | None:
        devs = self.devices()
        if len(devs) == 1:
            return devs[0]
        t = text.lower()
        hits = [d for d in devs if d["name"].lower() in t or any(a.lower() in t for a in d.get("aliases", []))]
        return hits[0] if len(hits) == 1 else None

    def persona(self):
        who = {"owner": "{{USER_NAME}}", "trusted": "{{USER_NAME}} or a (trusted) friend",
               "friends": "{{USER_NAME}} or one of his friends"}.get(self.lato.S("lights_who"), "{{USER_NAME}}")
        return super().persona().replace("{{WHO}}", who)

    def allowed(self, turn) -> bool:
        return access.allows(turn, self.lato.S("lights_who"))

    async def context(self, turn) -> str:
        if not (INTENT.search(turn.joined) and self.allowed(turn) and self.devices()):
            return ""
        k = self.current_key()
        if not k:
            return "(lights: not set up yet (no device key), so you can't control them right now. say so if asked.)"
        parts = []
        for d in self.devices():
            try:
                st = await meross.state(self.lato.http(), d["host"], k)
                desc = "on" if st["on"] else "off"
                if st["on"] and st["brightness"]:
                    desc += f", {st['brightness']}% brightness"
                parts.append(f"the {d['name']} is {desc}")
            except meross.DeviceError as e:
                if "can't reach" in str(e) and await self.relocate(d):
                    try:
                        st = await meross.state(self.lato.http(), d["host"], k)
                        parts.append(f"the {d['name']} is {'on' if st['on'] else 'off'}")
                        continue
                    except meross.DeviceError:
                        pass
                parts.append(f"the {d['name']} isn't answering ({e})")
        return "(lights right now: " + "; ".join(parts) + ".)"

    async def on_reply(self, turn):
        if turn.tags("LIGHT"):
            turn.has_output = True

    async def act(self, turn):
        calls = turn.tags("LIGHT")
        if not calls:
            return
        if not self.allowed(turn):
            self.log.info("lights: %s isn't allowed to control the lights", self.lato.speaker(turn.msg))
            return
        if not self.asked_for_lights(turn):    # their own words must be about the lights, whoever they are
            turn.done.append("light skipped (they didn't ask about the lights)")
            self.own_up(turn, "i didn't touch the lights (you didn't ask about them)")
            return
        if not self.current_key():
            turn.done.append("light failed: the lights aren't set up yet (no device key)")
            self.own_up(turn, "lights aren't set up yet")
            return
        for c in calls[:3]:
            res = await self.apply(c.arg)
            turn.done.append(res)
            if res.startswith("light failed"):
                self.own_up(turn, res.removeprefix("light failed: "))

    LOOKBACK_S = 600    # "no I meant the thing you didn't reply to": their own earlier messages count for 10 minutes

    def asked_for_lights(self, turn) -> bool:
        """The asker's own words mention the lights: this burst, or one of their last few messages in this
        channel within LOOKBACK_S. Only their messages, never anyone else's, notes or tool text."""
        if INTENT.search(turn.joined):
            return True
        store = getattr(self.lato, "store", None)
        msg = turn.msg
        try:
            now = int(msg.created_at.timestamp())
        except AttributeError:
            now = int(time.time())
        try:
            earlier = store.recent_from(msg.channel.id, msg.author.id, now - self.LOOKBACK_S, limit=5) if store else []
        except Exception:
            earlier = []
        return any(INTENT.search(t) for t in earlier)

    @staticmethod
    def own_up(turn, why: str) -> None:
        """She must never claim a light change that didn't happen: add a small grey line under her reply."""
        if turn.text and not turn.blank_text:
            turn.text = turn.text.rstrip() + "\n-# " + why

    async def apply(self, arg: str) -> str:
        cmd = parse(arg)
        dev = self.find(cmd["rest"] or arg)
        if not dev:
            return f"light failed: couldn't tell which light '{cmd['rest'] or arg}' is"
        try:
            return await self._apply(dev, cmd, arg)
        except meross.DeviceError as e:
            if "can't reach" in str(e) and await self.relocate(dev):
                try:
                    return await self._apply(dev, cmd, arg)
                except meross.DeviceError as e2:
                    e = e2
            self.log.warning("light %s failed: %s", dev["name"], e)
            return f"light failed: the {dev['name']} {e}"
        except asyncio.TimeoutError:
            return f"light failed: the {dev['name']} didn't answer"

    async def _apply(self, dev: dict, cmd: dict, arg: str) -> str:
        http, host, key = self.lato.http(), dev["host"], self.current_key()
        if cmd["power"] == "toggle":
            cmd["power"] = "off" if (await meross.state(http, host, key))["on"] else "on"
        if cmd["power"] == "off":
            await meross.set_power(http, host, key, False)
            said = "off"
        else:
            bits = []
            if cmd["power"] == "on" or any(cmd[k] is not None for k in ("rgb", "temperature", "brightness")):
                await meross.set_power(http, host, key, True)
                bits.append("on")
            if any(cmd[k] is not None for k in ("rgb", "temperature", "brightness")):
                await meross.set_light(http, host, key, cmd["rgb"], None if cmd["rgb"] else cmd["temperature"], cmd["brightness"])
                bits += [f"{cmd['brightness']}%"] if cmd["brightness"] else []
                bits += [next(k for k, v in COLOURS.items() if v == cmd["rgb"])] if cmd["rgb"] else []
                bits += [next(k for k, v in TEMPS.items() if v == cmd["temperature"])] if cmd["temperature"] and not cmd["rgb"] else []
            if not bits:
                return f"light failed: didn't understand '{arg}'"
            said = " ".join(bits)
        self.log.info("LIGHT %s -> %s", dev["name"], said)
        return f"{dev['name']}: {said}"
