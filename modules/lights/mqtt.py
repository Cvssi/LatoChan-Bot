"""Just enough MQTT 3.1.1 (stdlib asyncio) to keep re-keyed Meross devices happy on our local broker.

After connecting, a Meross device publishes Appliance.Control.Bind (SET) and waits for the "cloud" to answer
SETACK; without it, it drops the connection every ~20 s and tries again. bind_reply() makes that answer, but only
for messages signed with OUR key, so nothing else on the broker can make us talk.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import struct
import time
import uuid

TOPIC_ALL = "/appliance/+/publish"


def _str(x: str) -> bytes:
    b = x.encode()
    return struct.pack("!H", len(b)) + b


def _len(n: int) -> bytes:
    out = b""
    while True:
        d, n = n % 128, n // 128
        out += bytes([d | (0x80 if n else 0)])
        if not n:
            return out


def connect_pkt(client_id: str, keepalive: int = 60) -> bytes:
    var = _str("MQTT") + bytes([4, 2]) + struct.pack("!H", keepalive)
    body = var + _str(client_id)
    return bytes([0x10]) + _len(len(body)) + body


def subscribe_pkt(topic: str, pid: int = 1) -> bytes:
    body = struct.pack("!H", pid) + _str(topic) + b"\x00"
    return bytes([0x82]) + _len(len(body)) + body


def publish_pkt(topic: str, payload: bytes) -> bytes:
    body = _str(topic) + payload
    return bytes([0x30]) + _len(len(body)) + body


PINGREQ = b"\xc0\x00"


async def read_pkt(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    h = (await reader.readexactly(1))[0]
    mult, n = 1, 0
    while True:
        d = (await reader.readexactly(1))[0]
        n += (d & 127) * mult
        mult *= 128
        if not d & 128:
            break
    return h, await reader.readexactly(n) if n else b""


def parse_publish(data: bytes) -> tuple[str, bytes]:
    tl = struct.unpack("!H", data[:2])[0]
    return data[2:2 + tl].decode("utf-8", "replace"), data[2 + tl:]


def signed_ok(header: dict, key: str) -> bool:
    try:
        want = hashlib.md5(f"{header['messageId']}{key}{header['timestamp']}".encode()).hexdigest()
    except KeyError:
        return False
    return want == header.get("sign")


def bind_reply(topic: str, payload: bytes, key: str) -> tuple[str, bytes] | None:
    """Device's Bind SET (signed with our key) -> (topic, SETACK message) to send back; anything else -> None."""
    try:
        msg = json.loads(payload)
        h = msg["header"]
    except (ValueError, KeyError, TypeError):
        return None
    if h.get("namespace") != "Appliance.Control.Bind" or h.get("method") != "SET" or not signed_ok(h, key):
        return None
    dev = topic.split("/")[2] if topic.count("/") >= 3 else ""
    if not dev:
        return None
    ts = int(time.time())
    mid = h["messageId"]
    ack = {"header": {"messageId": mid, "namespace": "Appliance.Control.Bind", "method": "SETACK", "payloadVersion": 1,
                      "from": f"/appliance/{dev}/subscribe", "timestamp": ts, "timestampMs": 0,
                      "sign": hashlib.md5(f"{mid}{key}{ts}".encode()).hexdigest()},
           "payload": {}}
    return h.get("from") or f"/appliance/{dev}/subscribe", json.dumps(ack).encode()


async def serve(get_key, log, host: str = "localhost", port: int = 1883, stop: asyncio.Event | None = None) -> None:
    """One connection's lifetime: subscribe, answer binds, keep alive. Returns/raises when the link drops."""
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 10)
    try:
        writer.write(connect_pkt(f"latochan-lights-{uuid.uuid4().hex[:8]}"))
        await writer.drain()
        t, d = await asyncio.wait_for(read_pkt(reader), 10)
        if t >> 4 != 2 or len(d) < 2 or d[1] != 0:
            raise ConnectionError(f"broker refused (CONNACK {d!r})")
        writer.write(subscribe_pkt(TOPIC_ALL))
        await writer.drain()
        last_ping = time.monotonic()
        while not (stop and stop.is_set()):
            try:
                t, d = await asyncio.wait_for(read_pkt(reader), 20)
            except asyncio.TimeoutError:
                t, d = 0, b""
            except asyncio.IncompleteReadError:
                if stop and stop.is_set():
                    return              # we were asked to stop and the broker hung up: clean exit
                raise
            if t >> 4 == 3:
                topic, payload = parse_publish(d)
                key = get_key()
                rep = bind_reply(topic, payload, key) if key else None
                if rep:
                    writer.write(publish_pkt(*rep))
                    await writer.drain()
                    log.info("LIGHTS answered bind from %s", topic.split("/")[2])
            if time.monotonic() - last_ping > 30:
                writer.write(PINGREQ)
                await writer.drain()
                last_ping = time.monotonic()
    finally:
        writer.close()
