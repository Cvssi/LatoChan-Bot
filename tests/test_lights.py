"""Lights module against a simulated Meross bulb (checks signatures like the real one): parsing, protocol, wrong key,
permissions, the intent check, state in the prompt, and failure notes."""
import asyncio

MOCK_MAC = bytes(range(1, 7)).hex(":")


def lan(suffix: int) -> str:
    return ".".join(map(str, (192, 0, 2, suffix)))

import json
import os
import unittest

from aiohttp import web

import common
from test_core import fake_msg
from modules.lights import meross
from modules.lights.module import parse

KEY = "test-key-123"


class FakeBulb:
    def __init__(self):
        self.on, self.rgb, self.temp, self.lum, self.calls = False, None, 50, 80, []

    async def handle(self, req):
        m = await req.json()
        h = m["header"]
        self.calls.append(h["namespace"])
        if meross.sign(h["messageId"], KEY, h["timestamp"]) != h["sign"]:
            return web.json_response({"header": {"method": "ERROR"}, "payload": {"error": {"code": 5001, "detail": "sign error"}}})
        p = m["payload"]
        if h["namespace"] == "Appliance.System.All":
            return web.json_response({"header": {"method": "GETACK"}, "payload": {"all": {"digest": {
                "togglex": [{"channel": 0, "onoff": int(self.on)}], "light": {"rgb": self.rgb, "temperature": self.temp, "luminance": self.lum}}}}})
        if h["namespace"] == "Appliance.Control.ToggleX":
            self.on = bool(p["togglex"]["onoff"])
        if h["namespace"] == "Appliance.Control.Light":
            li = p["light"]
            if li["capacity"] & 1:
                self.rgb = li["rgb"]
            if li["capacity"] & 2:
                self.temp = li["temperature"]
            if li["capacity"] & 4:
                self.lum = li["luminance"]
        return web.json_response({"header": {"method": "SETACK"}, "payload": {}})


class Parse(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse("lamp blue 40%"), {"power": None, "rgb": 0x0040FF, "temperature": None, "brightness": 40, "rest": "lamp"})
        self.assertEqual(parse("living room lamp off")["power"], "off")
        self.assertEqual(parse("lamp warm dim")["temperature"], 1)
        self.assertEqual(parse("lamp warm dim")["brightness"], 20)
        self.assertEqual(parse("lamp 250")["brightness"], 100)


class Lights(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["MEROSS_KEY"] = KEY
        from core.app import Lato
        cls.lato = Lato()
        asyncio.run(cls.lato.setup_modules())
        cls.mod = cls.lato.module("lights")

    def run_with_bulb(self, coro_fn, key=KEY):
        bulb = FakeBulb()

        async def go():
            app = web.Application()
            app.router.add_post("/config", bulb.handle)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "localhost", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            self.mod.devices_file.write_text(json.dumps([{"name": "living room lamp", "host": f"localhost:{port}", "aliases": ["lamp"]}]))
            self.mod.key = key
            try:
                return await coro_fn(bulb)
            finally:
                await self.lato.close_http()
                await runner.cleanup()
        return asyncio.run(go()), bulb

    def settings(self, **kw):
        common.TMP.joinpath("settings.json").write_text(json.dumps({"mod_lights": True, "sleep_mode": False, "mod_pings": False, **kw}))
        self.lato.S.refresh(force=True)

    def ask(self, text, reply, author=(10**17 + 2)):
        async def go(bulb):
            prompts = []

            async def chat(messages, think=False, **kw):
                prompts.append(messages[-1]["content"])
                return reply, ""
            self.lato.llm.chat = chat
            m, sent = fake_msg(text, author=author)
            await self.lato.pipeline.respond([m], "main")
            return prompts, sent, list(self.lato.history.get(m.channel.id))
        return self.run_with_bulb(go)

    def setUp(self):
        self.settings()
        self.lato.history.clear()

    def test_owner_turns_lamp_blue(self):
        (prompts, sent, hist), bulb = self.ask("lato make the lamp blue and dim it", "there\n[[LIGHT: lamp blue 30%]]")
        self.assertTrue(bulb.on)
        self.assertEqual(bulb.rgb, 0x0040FF)
        self.assertEqual(bulb.lum, 30)
        self.assertIn("lights right now: the living room lamp is off", prompts[0])
        self.assertEqual(sent[0][1], "there")

    def test_off_and_toggle(self):
        async def go(bulb):
            bulb.on = True
            r1 = await self.mod.apply("lamp off")
            r2 = await self.mod.apply("lamp toggle")
            return r1, r2, bulb.on
        (r1, r2, on), _ = self.run_with_bulb(go)
        self.assertEqual(r1, "living room lamp: off")
        self.assertEqual(r2, "living room lamp: on")
        self.assertTrue(on)

    def test_wrong_key_reported_not_hidden(self):
        async def go(bulb):
            return await self.mod.apply("lamp off")
        res, bulb = self.run_with_bulb(go, key="nope")
        self.assertIn("wrong device key", res)
        self.assertNotIn("Appliance.Control.ToggleX", [c for c in bulb.calls if False])

    def test_who_levels(self):
        import friends
        fid = (10**17 + 1)
        friends.trust(fid, False)
        self.lato.friends.refresh(force=True)
        # default "trusted": a plain friend can't, a trusted friend can, a stranger never
        (_, _, _), bulb = self.ask("lato lamp off", "ok [[LIGHT: lamp off]]", author=fid)
        self.assertNotIn("Appliance.Control.ToggleX", bulb.calls)
        friends.trust(fid, True)
        self.lato.friends.refresh(force=True)
        try:
            (_, _, hist), bulb = self.ask("lato turn the lamp on", "ok [[LIGHT: lamp on]]", author=fid)
            self.assertTrue(bulb.on)
            self.assertIn("(trusted):", hist[-2]["content"])
            (_, _, _), bulb = self.ask("lato what's up", "ok [[LIGHT: lamp on]]", author=fid)    # didn't ask for lights
            self.assertFalse(bulb.on)
            self.settings(lights_who="owner")
            (_, _, _), bulb = self.ask("lato turn the lamp on", "ok [[LIGHT: lamp on]]", author=fid)
            self.assertFalse(bulb.on)
        finally:
            friends.trust(fid, False)
            self.lato.friends.refresh(force=True)
        self.settings(lights_who="friends")
        (_, _, _), bulb = self.ask("lato turn the lamp on", "ok [[LIGHT: lamp on]]", author=fid)
        self.assertTrue(bulb.on)
        (_, _, _), bulb = self.ask("lato turn the lamp on", "ok [[LIGHT: lamp on]]", author=999)
        self.assertFalse(bulb.on)

    def test_owner_words_must_mention_lights(self):
        (_, sent, hist), bulb = self.ask("what's up", "[[LIGHT: lamp off]] hi")
        self.assertNotIn("Appliance.Control.ToggleX", bulb.calls)
        self.assertIn("light skipped", hist[-1]["content"])

    def test_follow_up_counts_their_earlier_lights_message(self):
        import time as _t
        cid = 100
        self.lato.store._exec("DELETE FROM messages WHERE cid=?", (cid,))
        self.lato.store.log_message(901, cid, (10**17 + 2), "owner", "the lamp is way too blue", int(_t.time()) - 60)
        (_, sent, _), bulb = self.ask("no mf i meant the thing you didnt reply to", "my bad, fixing it [[LIGHT: lamp warm]]")
        self.assertTrue(bulb.on)
        self.assertEqual(sent[0][1], "my bad, fixing it")
        # someone else's lights message doesn't count, and neither does an old one
        self.lato.store._exec("DELETE FROM messages WHERE cid=?", (cid,))
        self.lato.store.log_message(902, cid, 12345, "alice", "lamp off pls", int(_t.time()) - 60)
        self.lato.store.log_message(903, cid, (10**17 + 2), "owner", "lamp red", int(_t.time()) - 3600)
        (_, sent, _), bulb = self.ask("do it", "ok [[LIGHT: lamp off]]")
        self.assertNotIn("Appliance.Control.ToggleX", bulb.calls)
        self.lato.store._exec("DELETE FROM messages WHERE cid=?", (cid,))

    def test_blocked_light_is_admitted_in_the_reply(self):
        (_, sent, _), bulb = self.ask("what's up", "fixing the lights rn [[LIGHT: lamp off]]")
        self.assertNotIn("Appliance.Control.ToggleX", bulb.calls)
        self.assertIn("fixing the lights rn\n-# i didn't touch the lights", sent[0][1])

    def test_failure_goes_into_history(self):
        async def go(bulb):
            self.mod.devices_file.write_text(json.dumps([{"name": "living room lamp", "host": "localhost:1", "aliases": []}]))
            return await self.mod.apply("lamp off")
        res, _ = self.run_with_bulb(go)
        self.assertIn("light failed", res)
        self.assertIn("can't reach it", res)


def load_cli():
    import importlib.util
    spec = importlib.util.spec_from_file_location("lights_cli2", common.ROOT / "modules" / "lights" / "cli.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    return cli


class KeyFileAndDashboard(unittest.TestCase):
    """Re-keyed route: key in mod/lights/key (no env), dashboard json/apply."""

    def setUp(self):
        self.cli = load_cli()
        os.environ.pop("MEROSS_KEY", None)
        self.cli.DATA.mkdir(parents=True, exist_ok=True)
        self.cli.KEY_FILE.unlink(missing_ok=True)

    def test_newkey_is_private_and_not_overwritten(self):
        self.assertTrue(self.cli.new_key())
        k1 = self.cli.key()
        self.assertRegex(k1, r"^[0-9a-f]{32}$")
        self.assertEqual(oct(self.cli.KEY_FILE.stat().st_mode & 0o777), "0o640")
        self.assertFalse(self.cli.new_key())
        self.assertEqual(self.cli.key(), k1)
        self.assertTrue(self.cli.new_key(force=True))
        self.assertNotEqual(self.cli.key(), k1)

    def test_bot_uses_key_file_without_restart(self):
        from core.app import Lato
        lato = Lato()
        asyncio.run(lato.setup_modules())
        mod = lato.module("lights")
        mod.key = ""
        self.assertEqual(mod.current_key(), "")
        self.cli.KEY_FILE.write_text("abc123\n")
        self.assertEqual(mod.current_key(), "abc123")

    def test_apply_json_validates_and_drives_bulb(self):
        self.cli.KEY_FILE.write_text(KEY + "\n")
        bulb = FakeBulb()

        async def go():
            app = web.Application()
            app.router.add_post("/config", bulb.handle)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "localhost", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            self.cli.save([{"name": "living room lamp", "host": f"localhost:{port}", "aliases": ["lamp"]}])
            import aiohttp
            try:
                async with aiohttp.ClientSession() as http:
                    r = await self.cli.apply_json(http, {"device": "living room lamp", "color": "purple", "brightness": 40})
                    errors = []
                    for bad in ({"device": "nope"}, {"device": "living room lamp", "power": "explode"},
                                {"device": "living room lamp", "brightness": 400}, {"device": "living room lamp", "color": "ultraviolet"}):
                        try:
                            await self.cli.apply_json(http, bad)
                        except ValueError as e:
                            errors.append(str(e))
                    off = await self.cli.apply_json(http, {"device": "living room lamp", "power": "off"})
                    return r, errors, off
            finally:
                await runner.cleanup()
        r, errors, off = asyncio.run(go())
        self.assertTrue(r["state"]["on"])
        self.assertEqual(bulb.rgb, 0x8A2BE2)
        self.assertEqual(bulb.lum, 40)
        self.assertEqual(len(errors), 4)
        self.assertFalse(off["state"]["on"])



class BindResponder(unittest.TestCase):
    DEV = "2301111676648352050148e1e9b6c65b"

    def bind_msg(self, key):
        import hashlib
        import time as _t
        mid, ts = "fe25388b6cb4557df109e0326e147a9e", int(_t.time())
        return json.dumps({"header": {"messageId": mid, "namespace": "Appliance.Control.Bind", "method": "SET", "payloadVersion": 1,
                                      "from": f"/appliance/{self.DEV}/subscribe", "timestamp": ts, "timestampMs": 1,
                                      "sign": hashlib.md5(f"{mid}{key}{ts}".encode()).hexdigest()},
                           "payload": {"bind": {"hardware": {"type": "msl120d"}}}}).encode()

    def test_only_answers_binds_signed_with_our_key(self):
        from modules.lights import mqtt
        topic = f"/appliance/{self.DEV}/publish"
        rep = mqtt.bind_reply(topic, self.bind_msg(KEY), KEY)
        self.assertIsNotNone(rep)
        to, body = rep
        ack = json.loads(body)
        self.assertEqual(to, f"/appliance/{self.DEV}/subscribe")
        self.assertEqual(ack["header"]["method"], "SETACK")
        self.assertTrue(mqtt.signed_ok(ack["header"], KEY))
        self.assertIsNone(mqtt.bind_reply(topic, self.bind_msg("someone-else"), KEY))
        self.assertIsNone(mqtt.bind_reply(topic, b"not json", KEY))
        report = json.loads(self.bind_msg(KEY)); report["header"]["namespace"] = "Appliance.System.Report"
        self.assertIsNone(mqtt.bind_reply(topic, json.dumps(report).encode(), KEY))

    def test_round_trip_with_fake_broker(self):
        from modules.lights import mqtt
        import logging
        got = {}

        async def broker(reader, writer):
            t, d = await mqtt.read_pkt(reader)
            got["connect"] = t >> 4
            writer.write(b"\x20\x02\x00\x00")
            t, d = await mqtt.read_pkt(reader)
            got["subscribe"] = d[4:4 + len(mqtt.TOPIC_ALL)].decode()
            writer.write(b"\x90\x03\x00\x01\x00")
            writer.write(mqtt.publish_pkt(f"/appliance/{self.DEV}/publish", self.bind_msg(KEY)))
            await writer.drain()
            t, d = await mqtt.read_pkt(reader)
            got["reply"] = mqtt.parse_publish(d)
            stop.set()

        async def go():
            global stop
            srv = await asyncio.start_server(broker, "localhost", 0)
            port = srv.sockets[0].getsockname()[1]
            await asyncio.wait_for(mqtt.serve(lambda: KEY, logging.getLogger("t"), port=port, stop=stop), 30)
            srv.close()
        global stop
        stop = None

        async def main():
            global stop
            stop = asyncio.Event()
            await go()
        asyncio.run(main())
        self.assertEqual(got["connect"], 1)
        self.assertEqual(got["subscribe"], mqtt.TOPIC_ALL)
        topic, body = got["reply"]
        self.assertEqual(topic, f"/appliance/{self.DEV}/subscribe")
        self.assertEqual(json.loads(body)["header"]["method"], "SETACK")



class FixIp(unittest.TestCase):
    def test_moves_device_to_ip_found_by_mac(self):
        cli = load_cli()
        cli.DATA.mkdir(parents=True, exist_ok=True)
        cli.save([{"name": "living room lamp", "host": f"{lan(59)}", "aliases": [], "mac": MOCK_MAC},
                  {"name": "old lamp", "host": f"{lan(10)}", "aliases": []}])
        cli.arp_table = lambda: {MOCK_MAC: lan(77)}
        cli.lan_base = lambda: lan(0).rsplit(".", 1)[0]

        async def no_net(*a, **k):
            raise OSError("test: no network")
        cli.asyncio.open_connection = no_net
        try:
            asyncio.run(cli.fix_ip())
        finally:
            import asyncio as real
            cli.asyncio.open_connection = real.open_connection
        hosts = {d["name"]: d["host"] for d in cli.load()}
        self.assertEqual(hosts, {"living room lamp": f"{lan(77)}", "old lamp": f"{lan(10)}"})



class FollowsMovedLamp(unittest.TestCase):
    def test_bot_finds_lamp_by_mac_and_retries(self):
        os.environ["MEROSS_KEY"] = KEY
        from core.app import Lato
        lato = Lato()
        asyncio.run(lato.setup_modules())
        mod = lato.module("lights")
        mod.key = KEY
        bulb = FakeBulb()

        async def go():
            app = web.Application()
            app.router.add_post("/config", bulb.handle)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "localhost", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            # saved IP is stale (nothing listens there); the "ARP table" says the MAC is now at localhost:<port>
            mod.devices_file.write_text(json.dumps([{"name": "living room lamp", "host": "localhost:1", "aliases": ["lamp"],
                                                     "mac": MOCK_MAC}]))
            real_open = open

            def fake_open(path, *a, **k):
                if path == "/proc/net/arp":
                    import io
                    return io.StringIO("IP address HW type Flags HW address Mask Device\n"
                                       f"localhost:{port} 0x1 0x2 {MOCK_MAC} * eth-test\n")
                return real_open(path, *a, **k)
            import builtins
            builtins.open = fake_open
            try:
                return await mod.apply("lamp off")
            finally:
                builtins.open = real_open
                await lato.close_http()
                await runner.cleanup()
        bulb.on = True
        res = asyncio.run(go())
        self.assertEqual(res, "living room lamp: off")
        self.assertFalse(bulb.on)
        self.assertIn("localhost:", json.loads(mod.devices_file.read_text())[0]["host"])
        self.assertNotEqual(json.loads(mod.devices_file.read_text())[0]["host"], "localhost:1")


if __name__ == "__main__":
    unittest.main()
