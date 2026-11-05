"""Weather module: parsing WU replies, alerts + cooldowns, what she's told, and that the API key never leaks."""
import asyncio
import datetime
import json
import os
import time
import unittest

from aiohttp import web

import common
from test_core import fake_msg

WU_REPLY = {"observations": [{"stationID": "ITEST1", "epoch": 0, "lat": 50.0, "lon": -1.0, "humidity": 82.0, "winddir": 351,
                              "uv": 0.0, "solarRadiation": 0.0,
                              "metric": {"temp": 9.6, "heatIndex": 9.6, "dewpt": 6.7, "windChill": 9.6, "windSpeed": 0.0,
                                         "windGust": 2.9, "pressure": 1010.84, "precipRate": 0.0, "precipTotal": 1.52}}]}


def forecast_reply(start):
    times = [(start + datetime.timedelta(hours=i)).strftime("%Y-%m-%dT%H:00") for i in range(30)]
    return {"hourly": {"time": times, "temperature_2m": [8 + (i % 5) for i in range(30)],
                       "precipitation_probability": [10] * 4 + [80] * 26, "precipitation": [0] * 4 + [1.2] * 26,
                       "wind_gusts_10m": [20] * 30, "weather_code": [3] * 4 + [61] * 26}}


class S:
    def __init__(self, **kw):
        self.v = {"weather_alert_frost": True, "weather_alert_heat": True, "weather_alert_gust": True,
                  "weather_alert_rain": False, "weather_gust_kmh": 55, **kw}

    def __call__(self, k):
        return self.v[k]


class Pure(unittest.TestCase):
    def test_parse(self):
        from modules.weather import sources
        r = sources.parse_current(WU_REPLY)
        self.assertEqual((r["temp"], r["gust"], r["rain_today"], r["dir"]), (9.6, 2.9, 1.52, 351))
        self.assertEqual(sources.compass(351), "N")
        self.assertEqual(sources.compass(225), "SW")

    def test_freezing_uses_wind_chill(self):
        from modules.weather import sources

        reading = sources.parse_current({"observations": [{"epoch": 0, "metric": {"temp": 0, "windChill": -3}}]})
        self.assertEqual(reading["feels"], -3)

    def test_alerts_and_cooldowns(self):
        from modules.weather.module import alerts_due
        st, now = {}, 1_000_000.0
        self.assertEqual(alerts_due({"temp": -1.0, "gust": 10}, st, S(), now), ["frost"])
        st["last"]["frost"] = now
        self.assertEqual(alerts_due({"temp": -2.0, "gust": 10}, st, S(), now + 3600), [])      # cooldown 12 h
        self.assertEqual(alerts_due({"temp": -2.0, "gust": 10}, st, S(), now + 13 * 3600), ["frost"])
        self.assertEqual(alerts_due({"temp": 5, "gust": 60}, {}, S(), now), ["gust"])
        self.assertEqual(alerts_due({"temp": 5, "gust": 60}, {}, S(weather_alert_gust=False), now), [])
        self.assertEqual(alerts_due({"temp": 29, "gust": 0}, {}, S(), now), ["heat"])

    def test_rain_start_needs_three_dry_hours(self):
        from modules.weather.module import alerts_due
        st, now, s = {}, 1_000_000.0, S(weather_alert_rain=True)
        self.assertEqual(alerts_due({"temp": 9, "rain_rate": 0.5}, st, s, now), ["rain"])
        st["last"]["rain"] = now
        for k in range(1, 9):                     # keeps raining for 4 h, polled every 30 min: no new "started" alert
            self.assertEqual(alerts_due({"temp": 9, "rain_rate": 0.5}, st, s, now + k * 1800), [])
        self.assertEqual(alerts_due({"temp": 9, "rain_rate": 0}, st, s, now + 5 * 3600), [])
        self.assertEqual(alerts_due({"temp": 9, "rain_rate": 0.4}, st, s, now + 9 * 3600), ["rain"])
        self.assertEqual(alerts_due({"temp": 9, "rain_rate": 0.4}, {}, S(), now), [])            # off by default


class Live(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.update(WU_API_KEY="SECRETKEY123", WU_STATION="ITEST1")
        from core.app import Lato
        cls.lato = Lato()
        asyncio.run(cls.lato.setup_modules())
        cls.mod = cls.lato.module("weather")

    @classmethod
    def tearDownClass(cls):
        for k in ("WU_API_KEY", "WU_STATION"):
            os.environ.pop(k, None)

    def settings(self, **kw):
        common.TMP.joinpath("settings.json").write_text(json.dumps({"mod_weather": True, "sleep_mode": False, "mod_pings": False,
                                                                    "quiet_hours_enabled": False, **kw}))
        self.lato.S.refresh(force=True)

    def setUp(self):
        self.settings()
        self.lato.history.clear()

    def with_servers(self, coro_fn, wu_status=200):
        from modules.weather import sources
        seen = []
        now = datetime.datetime.now(datetime.timezone.utc).astimezone(datetime.timezone(datetime.timedelta(0)))

        async def wu(req):
            seen.append(dict(req.query))
            if wu_status != 200:
                return web.Response(status=wu_status)
            rep = json.loads(json.dumps(WU_REPLY))
            rep["observations"][0]["epoch"] = int(time.time())
            return web.json_response(rep)

        async def om(req):
            seen.append(dict(req.query))
            from zoneinfo import ZoneInfo
            start = datetime.datetime.now(ZoneInfo("UTC")).replace(minute=0, second=0, microsecond=0)
            return web.json_response(forecast_reply(start))

        async def go():
            app = web.Application()
            app.router.add_get("/wu", wu)
            app.router.add_get("/om", om)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "localhost", 0)
            await site.start()
            base = f"http://localhost:{site._server.sockets[0].getsockname()[1]}"
            old = (sources.WU, sources.OPEN_METEO)
            sources.WU, sources.OPEN_METEO = base + "/wu", base + "/om"
            try:
                return await coro_fn()
            finally:
                sources.WU, sources.OPEN_METEO = old
                await self.lato.close_http()
                await runner.cleanup()
        del now
        return asyncio.run(go()), seen

    def test_poll_stores_and_summarises(self):
        self.mod.fc_at = 0
        r, seen = self.with_servers(self.mod.poll)
        self.assertEqual(seen[0]["stationId"], "ITEST1")
        self.assertEqual(seen[1]["latitude"], "50.00")          # forecast at the station's own (rounded) position
        s = self.mod.summary()
        self.assertIn("9.6°C", s)
        self.assertIn("wind 0 km/h gusting 3 from the N", s)
        self.assertIn("1.5 mm today", s)
        self.assertIn("rain likely from about", s)
        self.assertNotIn("SECRETKEY", s)
        self.assertNotIn("50.0", s)                              # no coordinates in her prompt

    def test_last_night_low_from_history(self):
        from zoneinfo import ZoneInfo
        mid = datetime.datetime.now(ZoneInfo("UTC")).replace(hour=0, minute=0, second=0, microsecond=0)
        self.mod.store({"ts": int(mid.timestamp()) - 3 * 3600, "temp": 1.2, "gust": 5})
        self.mod.store({"ts": int(mid.timestamp()) + 4 * 3600, "temp": -0.4, "gust": 5})
        self.mod.latest = {"ts": int(time.time()), "temp": 5.0, "feels": 5.0, "humidity": 90, "wind": 3, "gust": 8,
                           "dir": 200, "rain_rate": 0, "rain_today": 0}
        self.assertIn("last night's low -0.4", self.mod.summary())

    def test_context_only_when_asked_and_not_for_strangers(self):
        self.mod.latest = {"ts": int(time.time()), "temp": 5.0, "feels": 5.0, "humidity": 90, "wind": 3, "gust": 8,
                           "dir": 200, "rain_rate": 0, "rain_today": 0}
        prompts = []

        async def chat(messages, think=False, **kw):
            prompts.append(messages[-1]["content"])
            return "ok", ""
        self.lato.llm.chat = chat
        for text in ("lato is it cold outside?", "lato what's up"):
            m, _ = fake_msg(text)
            asyncio.run(self.lato.pipeline.respond([m], "main"))
        self.assertIn("weather at home", prompts[0])
        self.assertNotIn("weather at home", prompts[1])

    def test_key_never_in_errors(self):
        async def go():
            try:
                await self.mod.poll()
            except Exception as e:
                return str(e)
        msg, _ = self.with_servers(go, wu_status=500)
        self.assertEqual(msg, "Weather Underground returned HTTP 500")

    def test_frost_alert_posts_once(self):
        said = []

        async def speak(note):
            said.append(note)
            return True
        self.mod.speak = speak
        self.mod.state = {}
        try:
            r = {"temp": -1.5, "gust": 3, "rain_rate": 0}
            asyncio.run(self.mod.check_alerts(r))
            asyncio.run(self.mod.check_alerts(r))
            self.assertEqual(len(said), 1)
            self.assertIn("frost", said[0])
            self.settings(quiet_hours_enabled=True, quiet_from_hour=0, quiet_to_hour=23, weather_quiet=True)
            self.mod.state = {}
            hour = datetime.datetime.now(__import__("zoneinfo").ZoneInfo("UTC")).hour
            asyncio.run(self.mod.check_alerts(r))
            self.assertEqual(len(said), 1 if hour < 23 else 2)   # quiet hours: nothing
        finally:
            del self.mod.speak


if __name__ == "__main__":
    unittest.main()
