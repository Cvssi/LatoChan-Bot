"""Weather from Owner's own station (Weather Underground PWS API, route A) + an Open-Meteo forecast.

* Polls the station every weather_poll_min and keeps every reading in mod/weather/weather.db (400 days), so she can
  compare with last night, yesterday, last week.
* When someone asks about the weather / rain / cold / wind..., her prompt gets a short note: now, today so far,
  last night's low, and the next 12 hours.
* Alerts in the main channel (each switchable): frost, strong gusts, rain starting, heat. Cooldowns, quiet hours.
* Optional morning line.
Needs WU_API_KEY and WU_STATION in /etc/latochan.env. The API key is never logged or put in her prompt.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import os
import re
import sqlite3
import time
from zoneinfo import ZoneInfo

from core import clock
from core import config as C
from core.module import Module as Base
from core.text import lowercase_outside_code

from . import sources

ASK = re.compile(r"\b(weather|rain\w*|cold|chilly|freezing|warm outside|hot|temp(erature)?s?|wind\w*|gust\w*|outside|"
                 r"forecast|frost\w*|snow\w*|umbrella|coat|jacket|humid\w*|sunny|degrees|storm\w*|drizzl\w*)\b", re.I)
COOLDOWN_H = {"frost": 12, "gust": 6, "rain": 3, "heat": 12}
TZ = ZoneInfo(C.TIMEZONE)


def alerts_due(r: dict, state: dict, S, now: float) -> list[str]:
    """Which alerts this reading triggers (pure, so it's testable). state = {"last": {kind: ts}, "last_rain": ts}."""
    due = []
    last = state.setdefault("last", {})

    def ready(kind):
        return now - last.get(kind, 0) >= COOLDOWN_H[kind] * 3600

    t, g, rate = r.get("temp"), r.get("gust"), r.get("rain_rate") or 0
    if S("weather_alert_frost") and t is not None and t <= 0 and ready("frost"):
        due.append("frost")
    if S("weather_alert_heat") and t is not None and t >= 28 and ready("heat"):
        due.append("heat")
    if S("weather_alert_gust") and g is not None and g >= S("weather_gust_kmh") and ready("gust"):
        due.append("gust")
    if rate > 0:
        dry_for = now - state.get("last_rain", 0)
        if S("weather_alert_rain") and dry_for >= 3 * 3600 and ready("rain"):
            due.append("rain")
        state["last_rain"] = now
    return due


class Module(Base):
    SWITCH = "mod_weather"
    ORDER = 60
    HELP = {"title": "weather", "line": "the weather at home from the operator's station + forecast; i warn about frost, gales and heat",
            "examples": ("lato what's it like outside?", "lato how cold did it get last night?", "lato will it rain later?"),
            "who": "friends"}

    async def setup(self):
        self.db_path = self.data_dir() / "weather.db"
        self.state_path = self.data_dir() / "state.json"
        con = sqlite3.connect(self.db_path)
        con.execute("CREATE TABLE IF NOT EXISTS readings(ts INTEGER PRIMARY KEY, data TEXT NOT NULL)")
        con.execute("DELETE FROM readings WHERE ts < ?", (int(time.time()) - 400 * 86400,))
        con.commit()
        con.close()
        self.latest: dict | None = None
        self.fc: list[dict] = []
        self.fc_at = 0.0
        self.error = ""
        self.state = self.load_state()

    def key(self) -> str:
        return os.environ.get("WU_API_KEY", "")

    def station(self) -> str:
        return os.environ.get("WU_STATION", "")

    def status(self) -> str:
        if not self.enabled():
            return "off"
        if not (self.key() and self.station()):
            return "on, but WU_API_KEY / WU_STATION missing"
        if self.error:
            return f"on, last error: {self.error}"
        if self.latest:
            return f"on, {self.latest.get('temp')} °C at {datetime.datetime.fromtimestamp(self.latest['ts'], TZ):%H:%M}"
        return "on, waiting for the first reading"

    # ---- storage
    def load_state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text())
        except Exception:
            return {}

    def save_state(self):
        try:
            self.state_path.write_text(json.dumps(self.state))
        except OSError:
            self.log.warning("weather: couldn't save state")

    def store(self, r: dict):
        keep = {k: v for k, v in r.items() if k not in ("lat", "lon")}
        con = sqlite3.connect(self.db_path)
        con.execute("INSERT OR REPLACE INTO readings(ts, data) VALUES(?, ?)", (r["ts"], json.dumps(keep)))
        con.commit()
        con.close()

    def between(self, t0: float, t1: float) -> list[dict]:
        con = sqlite3.connect(self.db_path)
        rows = con.execute("SELECT data FROM readings WHERE ts BETWEEN ? AND ? ORDER BY ts", (int(t0), int(t1))).fetchall()
        con.close()
        return [json.loads(x[0]) for x in rows]

    # ---- polling
    async def poll(self):
        http = self.lato.http()
        r = await sources.current(http, self.key(), self.station())
        self.store(r)
        self.latest = r
        self.error = ""
        if r.get("lat") is not None and time.time() - self.fc_at > 3600:
            try:
                self.fc = await sources.forecast(http, r["lat"], r["lon"])
                self.fc_at = time.time()
            except Exception as e:
                self.log.warning("weather: forecast failed: %r", e)
        return r

    async def loop(self):
        await asyncio.sleep(20)
        while True:
            wait = 60 * self.lato.S("weather_poll_min") if self.enabled() else 60
            if self.enabled() and self.key() and self.station():
                try:
                    r = await self.poll()
                    await self.check_alerts(r)
                    await self.maybe_morning()
                except Exception as e:
                    self.error = str(e)[:120] if "apiKey" not in str(e) else "request failed"
                    self.log.warning("weather: %s", self.error)
            await asyncio.sleep(wait)

    # ---- what she's told
    def summary(self, now: float | None = None) -> str:
        r = self.latest
        if not r:
            return ""
        now = now or time.time()
        age = max(0, int((now - r["ts"]) / 60))
        temp = r.get("temp")
        parts = [(f"{temp:.1f}°C" + (f" (feels {r['feels']:.0f})" if r.get("feels") is not None and abs(r["feels"] - temp) >= 2 else ""))
                 if temp is not None else "temperature unavailable",
                 f"humidity {r['humidity']:.0f}%" if r.get("humidity") is not None else "",
                 f"wind {r['wind'] or 0:.0f} km/h gusting {r['gust'] or 0:.0f} from the {sources.compass(r.get('dir'))}",
                 ("raining now" if (r.get("rain_rate") or 0) > 0 else "not raining now")
                 + f", {r.get('rain_today') or 0:.1f} mm today"]
        midnight = datetime.datetime.fromtimestamp(now, TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        today = self.between(midnight.timestamp(), now)
        temps = [x["temp"] for x in today if x.get("temp") is not None]
        gusts = [x["gust"] for x in today if x.get("gust") is not None]
        if temps:
            parts.append(f"today so far: high {max(temps):.1f}, low {min(temps):.1f}" + (f", top gust {max(gusts):.0f} km/h" if gusts else ""))
        night = self.between((midnight - datetime.timedelta(hours=6)).timestamp(), midnight.timestamp() + 9 * 3600)
        nt = [x["temp"] for x in night if x.get("temp") is not None]
        if nt:
            parts.append(f"last night's low {min(nt):.1f}")
        fc = self.forecast_text(now)
        if fc:
            parts.append("forecast " + fc)
        upd = "just now" if age < 2 else f"{age} min ago"
        return "weather at home (the operator's station, updated " + upd + "): " + "; ".join(p for p in parts if p)

    def forecast_text(self, now: float, hours: int = 12) -> str:
        if not self.fc:
            return ""
        start = datetime.datetime.fromtimestamp(now, TZ).strftime("%Y-%m-%dT%H:00")
        nxt = [h for h in self.fc if h["time"] >= start][:hours]
        if not nxt:
            return ""
        temps = [h["temp"] for h in nxt]
        wet = [h for h in nxt if (h["rain_prob"] or 0) >= 50 or (h["rain"] or 0) >= 0.3]
        out = f"next {len(nxt)}h: {min(temps):.0f} to {max(temps):.0f}°C, mostly {self.common(nxt)}"
        out += (f", rain likely from about {wet[0]['time'][11:16]}" if wet else ", staying dry")
        g = max((h["gust"] or 0) for h in nxt)
        if g >= 40:
            out += f", gusts up to {g:.0f} km/h"
        return out

    @staticmethod
    def common(hours) -> str:
        codes = [h["code"] for h in hours]
        c = max(set(codes), key=codes.count)
        return sources.CODES.get(c, "mixed")

    async def context(self, turn) -> str:
        if not (self.latest and ASK.search(turn.joined or "")) or turn.stranger:
            return ""
        return f"({self.summary()}. use it only if they're asking about the weather.)"

    # ---- talking by herself
    def quiet_now(self) -> bool:
        S = self.lato.S
        if not (S("weather_quiet") and S("quiet_hours_enabled")):
            return False
        return clock.in_hours(S("quiet_from_hour"), S("quiet_to_hour"))

    async def check_alerts(self, r: dict):
        if self.quiet_now():
            if (r.get("rain_rate") or 0) > 0:
                self.state["last_rain"] = time.time()
            return
        due = alerts_due(r, self.state, self.lato.S, time.time())
        self.save_state()
        for kind in due:
            what = {"frost": f"it's {r['temp']:.1f}°C outside, frost", "heat": f"it's {r['temp']:.1f}°C outside, properly hot",
                    "gust": f"a {r['gust']:.0f} km/h gust just hit at home", "rain": "it just started raining at home"}[kind]
            if await self.speak(f"you just noticed from the operator's weather station that {what}. tell the chat in one short line "
                                "(a heads-up, in character; e.g. scrape the car, bring things in, take a coat)."):
                self.state.setdefault("last", {})[kind] = time.time()
                self.save_state()

    async def maybe_morning(self):
        S = self.lato.S
        if not S("weather_morning"):
            return
        now = datetime.datetime.now(TZ)
        today = now.strftime("%Y-%m-%d")
        if now.hour != S("weather_morning_hour") or self.state.get("morning") == today:
            return
        self.state["morning"] = today
        self.save_state()
        await self.speak("it's morning. give the chat ONE short weather line from this: " + self.summary())

    async def speak(self, note: str) -> bool:
        lato = self.lato
        cid = C.primary_channel()
        try:
            ch = await lato.channel(cid)
        except Exception:
            self.log.exception("weather: can't reach the main channel")
            return False
        async with lato.locks[cid]:
            messages = [{"role": "system", "content": lato.system_prompt()}, *lato.recent_history(cid),
                        {"role": "user", "content": f"(system note: {note} no @pings, no tags.)" + lato.turn_context()}]
            try:
                reply, _ = await lato.llm.chat(messages, think=False)
            except Exception:
                self.log.exception("weather: model call failed")
                return False
        reply, _, _ = lato.tags.parse(reply)
        reply = lowercase_outside_code(re.sub(r"\[\[[^\]]*\]\]", "", reply)).strip()
        if not reply:
            return False
        self.log.info("WEATHER %s", reply.replace("\n", " / "))
        await lato.say(ch, reply)
        lato.history.append(cid, {"role": "assistant", "content": reply})
        return True
