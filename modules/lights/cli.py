"""lato-lights: set up and test the lights module from the server shell (run with sudo: it reads MEROSS_KEY
from /etc/latochan.env).

  sudo lato-lights find [SUBNET_PREFIX]          scan a /24 for Meross/Refoss devices (default: the server's own range)
  sudo lato-lights add "living room lamp" LAMP_HOST lamp,bulb [MAC]  add/replace a device (name, IP, aliases, MAC)
  sudo lato-lights fixip                       a light moved IP? finds each device by its MAC and updates the IP
  sudo lato-lights remove "living room lamp"
  sudo lato-lights list
  sudo lato-lights status                      ask every device how it is (checks the key too)
  sudo lato-lights on|off [name]               quick test
  sudo lato-lights newkey                      make the lamp key for re-keying (once; --force replaces it)
  lato-lights key                              print the key (for the pairing script / Home Assistant)
  lato-lights json                             devices + their state as JSON (LatoDash)
  lato-lights apply                            one change from JSON on stdin (LatoDash):
                                               {"device": "...", "power": "on|off|toggle", "brightness": 1-100,
                                                "color": "red|...", "temperature": "warm|white|cool"}
"""
import asyncio
import grp
import ipaddress
import json
import os
import pwd
import socket
import sys
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from modules.lights import meross  # noqa: E402

DATA = Path(os.environ.get("LATO_DATA", "/var/lib/latochan")) / "mod" / "lights"
DEVICES = DATA / "devices.json"
KEY_FILE = DATA / "key"


def key() -> str:
    """The lamp key: MEROSS_KEY from the environment (manual override / tests), else the local key file."""
    if os.environ.get("MEROSS_KEY"):
        return os.environ["MEROSS_KEY"]
    try:
        return KEY_FILE.read_text().strip()
    except OSError:
        return ""


def new_key(force: bool = False) -> bool:
    import secrets
    if KEY_FILE.exists() and KEY_FILE.read_text().strip() and not force:
        return False
    DATA.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_text(secrets.token_hex(16) + "\n")
    try:
        os.chown(KEY_FILE, pwd.getpwnam("latochan").pw_uid, grp.getgrnam("latochan").gr_gid)
    except (KeyError, PermissionError):
        pass
    os.chmod(KEY_FILE, 0o640)
    return True


def load() -> list:
    try:
        return json.loads(DEVICES.read_text())
    except (OSError, ValueError):
        return []


def save(devs: list) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    DEVICES.write_text(json.dumps(devs, indent=1) + "\n")
    try:
        os.chown(DEVICES, pwd.getpwnam("latochan").pw_uid, grp.getgrnam("latochan").gr_gid)
        os.chmod(DEVICES, 0o664)
    except (KeyError, PermissionError):
        pass


COLORS = {"red": 0xFF0000, "orange": 0xFF7A00, "yellow": 0xFFD000, "green": 0x00FF40, "cyan": 0x00E5FF,
          "blue": 0x0040FF, "purple": 0x8A2BE2, "pink": 0xFF4FB0, "magenta": 0xFF00FF}
TEMPS = {"warm": 1, "white": 50, "cool": 100}


async def apply_json(http, op: dict) -> dict:
    """One change from LatoDash. Everything is validated here; returns the device's new state."""
    if not isinstance(op, dict):
        raise ValueError("expected a JSON object")
    k = key()
    if not k:
        raise ValueError("no lamp key yet")
    dev = next((d for d in load() if d["name"] == str(op.get("device", "")).lower()), None)
    if not dev:
        raise ValueError("unknown device")
    host = dev["host"]
    power = op.get("power")
    if power not in (None, "on", "off", "toggle"):
        raise ValueError("power must be on, off or toggle")
    if power == "toggle":
        power = "off" if (await meross.state(http, host, k))["on"] else "on"
    rgb = COLORS.get(op["color"]) if op.get("color") else None
    if op.get("color") and rgb is None:
        raise ValueError("unknown colour")
    temp = TEMPS.get(op["temperature"]) if op.get("temperature") else None
    if op.get("temperature") and temp is None:
        raise ValueError("unknown white")
    bri = op.get("brightness")
    if bri is not None:
        bri = int(bri)
        if not 1 <= bri <= 100:
            raise ValueError("brightness must be 1-100")
    if power == "off":
        await meross.set_power(http, host, k, False)
    else:
        if power == "on" or rgb is not None or temp is not None or bri is not None:
            await meross.set_power(http, host, k, True)
        if rgb is not None or temp is not None or bri is not None:
            await meross.set_light(http, host, k, rgb, None if rgb is not None else temp, bri)
    return {"device": dev["name"], "state": await meross.state(http, host, k)}


def lan_base() -> str:
    """First three parts of the configured or detected LAN subnet."""
    configured = os.environ.get("LATO_LIGHTS_SUBNET", "")
    try:
        ip = ipaddress.IPv4Address(f"{configured}.0" if configured else socket.gethostbyname(socket.gethostname()))
    except (OSError, ValueError):
        raise SystemExit("Set LATO_LIGHTS_SUBNET to your three-octet LAN prefix.") from None
    if ip.is_loopback:
        raise SystemExit("Set LATO_LIGHTS_SUBNET to your three-octet LAN prefix.")
    return str(ip).rsplit(".", 1)[0]


def arp_table() -> dict[str, str]:
    """MAC -> IP from the kernel's neighbour table."""
    out = {}
    try:
        with open("/proc/net/arp") as fh:
            lines = fh.read().splitlines()[1:]
        for line in lines:
            f = line.split()
            if len(f) >= 4 and f[3].replace(":", "") != "0" * 12:
                out[f[3].lower()] = f[0]
    except OSError:
        pass
    return out


async def fix_ip() -> None:
    devs = load()
    if not any(d.get("mac") for d in devs):
        sys.exit("no device has a MAC saved yet: sudo lato-lights add NAME IP aliases MAC")
    base = lan_base()
    print(f"looking for your lights on {base}.x ...")

    async def poke(ip):            # any packet makes the kernel learn the MAC; a closed port is fine
        try:
            _, w = await asyncio.wait_for(asyncio.open_connection(ip, 80), 0.8)
            w.close()
        except Exception:
            pass
    await asyncio.gather(*(poke(f"{base}.{i}") for i in range(1, 255)))
    arp = arp_table()
    changed = False
    for d in devs:
        mac = d.get("mac", "").lower()
        ip = arp.get(mac)
        if not mac:
            continue
        if not ip:
            print(f"  {d['name']}: not found (is it switched on?)")
        elif ip == d["host"]:
            print(f"  {d['name']}: still at {ip}")
        else:
            print(f"  {d['name']}: moved {d['host']} -> {ip} (updated)")
            d["host"] = ip
            changed = True
    if changed:
        save(devs)
        print("saved. LatoChan and LatoDash use the new IP straight away.")
        print("Home Assistant: Settings -> Devices & services -> Meross LAN -> the lamp -> Configure -> new host.")


async def probe(http, ip):
    try:
        with socket.create_connection((ip, 80), timeout=0.4):
            pass
    except OSError:
        return None
    msg = meross.message(ip, "GET", "Appliance.System.All", {}, "x")
    try:
        async with http.post(f"http://{ip}/config", json=msg, timeout=aiohttp.ClientTimeout(total=3)) as r:
            data = await r.json(content_type=None)
            if isinstance(data, dict) and "header" in data:
                return ip
    except Exception:
        return None
    return None


async def main(argv):
    cmd = argv[1] if len(argv) > 1 else "list"
    async with aiohttp.ClientSession() as http:
        if cmd == "find":
            base = argv[2] if len(argv) > 2 else lan_base()
            print(f"scanning {base}.1-254 for Meross/Refoss devices...")
            hits = [h for h in await asyncio.gather(*(probe(http, f"{base}.{i}") for i in range(1, 255))) if h]
            print("\n".join(f"  found: {h}" for h in hits) or "  none found (is it on, and on this network?)")
        elif cmd == "list":
            for d in load():
                print(f"  {d['name']:<24} {d['host']:<18} aliases: {', '.join(d.get('aliases', [])) or '-'}")
            if not load():
                print("  no devices yet: sudo lato-lights add NAME IP [alias,alias]")
        elif cmd == "add" and len(argv) >= 4:
            devs = [d for d in load() if d["name"].lower() != argv[2].lower()]
            old = next((d for d in load() if d["name"].lower() == argv[2].lower()), {})
            entry = {"name": argv[2].lower(), "host": argv[3], "aliases": [a.strip().lower() for a in (argv[4] if len(argv) > 4 else "").split(",") if a.strip()]}
            mac = (argv[5] if len(argv) > 5 else old.get("mac", "")).lower()
            if mac:
                entry["mac"] = mac
            devs.append(entry)
            save(devs)
            print(f"added {argv[2]} at {argv[3]}")
        elif cmd == "fixip":
            await fix_ip()
        elif cmd == "remove" and len(argv) >= 3:
            save([d for d in load() if d["name"].lower() != argv[2].lower()])
            print("removed")
        elif cmd == "newkey":
            if new_key(force="--force" in argv):
                print(f"new lamp key created in {KEY_FILE} (show it with: lato-lights key)")
            else:
                print(f"a key already exists in {KEY_FILE} (kept). --force makes a new one (then re-pair the lamp!)")
        elif cmd == "key":
            k = key()
            if not k:
                sys.exit("no key yet: sudo lato-lights newkey")
            print(k)
        elif cmd == "json":
            k, out = key(), []
            for d in load():
                row = {"name": d["name"], "host": d["host"], "aliases": d.get("aliases", [])}
                if not k:
                    row["error"] = "no key"
                else:
                    try:
                        row["state"] = await meross.state(http, d["host"], k)
                    except meross.DeviceError as e:
                        row["error"] = str(e)
                out.append(row)
            print(json.dumps({"devices": out, "has_key": bool(k), "colors": list(COLORS), "temperatures": list(TEMPS)}))
        elif cmd == "apply":
            try:
                print(json.dumps(await apply_json(http, json.loads(sys.stdin.read(4096) or "{}"))))
            except (ValueError, meross.DeviceError) as e:
                sys.exit(f"rejected: {e}")
        elif cmd in ("status", "on", "off"):
            k = key()
            if not k:
                sys.exit("no lamp key yet (sudo lato-lights newkey, or see LIGHTS-SETUP.md)")
            devs = load()
            if cmd != "status" and len(argv) > 2:
                devs = [d for d in devs if argv[2].lower() in (d["name"], *d.get("aliases", []))]
            for d in devs:
                try:
                    if cmd != "status":
                        await meross.set_power(http, d["host"], k, cmd == "on")
                    print(f"  {d['name']}: {await meross.state(http, d['host'], k)}")
                except meross.DeviceError as e:
                    print(f"  {d['name']}: {e}")
        else:
            print(__doc__)


if __name__ == "__main__":
    asyncio.run(main(sys.argv))
