"""Local LAN protocol for Meross-family devices (Meross / Refoss MSL120 bulbs, plugs).

POST http://<ip>/config with a JSON message whose header is signed:  sign = md5(messageId + key + timestamp).
`key` is the device key. Ours is set when the lamp is re-keyed (LIGHTS-SETUP.md), so no vendor account is involved.
Devices reject wrongly signed messages, so nobody on the LAN can drive them without it.

Bulb namespaces used:
  Appliance.System.All     GET  -> payload.all.digest.light / togglex   (state)
  Appliance.Control.ToggleX SET {"togglex": {"channel": 0, "onoff": 0|1}}
  Appliance.Control.Light   SET {"light": {"channel": 0, "capacity": bits, "rgb": int, "temperature": 1-100, "luminance": 1-100}}
                                capacity bits: 1 = rgb, 2 = colour temperature, 4 = brightness
"""
from __future__ import annotations

import hashlib
import time
import uuid

import aiohttp

CAP_RGB, CAP_TEMP, CAP_LUM = 1, 2, 4


class DeviceError(Exception):
    pass


def sign(message_id: str, key: str, ts: int) -> str:
    return hashlib.md5(f"{message_id}{key}{ts}".encode()).hexdigest()


def message(host: str, method: str, namespace: str, payload: dict, key: str) -> dict:
    mid, ts = uuid.uuid4().hex, int(time.time())
    return {"header": {"messageId": mid, "method": method, "from": f"http://{host}/config", "namespace": namespace,
                       "timestamp": ts, "timestampMs": 0, "sign": sign(mid, key, ts), "payloadVersion": 1},
            "payload": payload}


async def call(http: aiohttp.ClientSession, host: str, key: str, method: str, namespace: str, payload: dict | None = None,
               timeout: float = 5) -> dict:
    msg = message(host, method, namespace, payload or {}, key)
    try:
        async with http.post(f"http://{host}/config", json=msg, timeout=aiohttp.ClientTimeout(total=timeout),
                             allow_redirects=False) as r:
            if r.status != 200:
                raise DeviceError(f"http {r.status}")
            data = await r.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError) as e:
        raise DeviceError(f"can't reach it ({type(e).__name__})") from e
    hdr = (data or {}).get("header", {})
    if hdr.get("method") == "ERROR":
        err = (data.get("payload") or {}).get("error", {})
        raise DeviceError("wrong device key" if "sign" in str(err).lower() else f"device error {err}")
    return data.get("payload") or {}


async def state(http, host, key) -> dict:
    """-> {"on": bool, "brightness": 0-100, "rgb": int|None, "temperature": int|None}"""
    p = await call(http, host, key, "GET", "Appliance.System.All")
    dig = (p.get("all") or {}).get("digest") or {}
    tog = dig.get("togglex")
    tog = tog[0] if isinstance(tog, list) and tog else (tog or {})
    light = dig.get("light") or {}
    on = bool(tog.get("onoff", light.get("onoff", 0)))
    return {"on": on, "brightness": light.get("luminance"), "rgb": light.get("rgb"), "temperature": light.get("temperature")}


async def set_power(http, host, key, on: bool) -> None:
    await call(http, host, key, "SET", "Appliance.Control.ToggleX", {"togglex": {"channel": 0, "onoff": int(on)}})


async def set_light(http, host, key, rgb: int | None = None, temperature: int | None = None,
                    brightness: int | None = None) -> None:
    light, cap = {"channel": 0}, 0
    if rgb is not None:
        light["rgb"], cap = int(rgb), cap | CAP_RGB
    if temperature is not None:
        light["temperature"], cap = max(1, min(100, int(temperature))), cap | CAP_TEMP
    if brightness is not None:
        light["luminance"], cap = max(1, min(100, int(brightness))), cap | CAP_LUM
    light["capacity"] = cap
    await call(http, host, key, "SET", "Appliance.Control.Light", {"light": light})
