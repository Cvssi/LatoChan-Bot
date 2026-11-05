"""Where readings come from. Route A (now): Weather Underground's PWS API for your own station.
Route B later (catch the station's upload on the LAN, adds indoor temp) can be another function returning the same dict.
Forecast: Open-Meteo (free, no account), at the station's own coordinates from the WU reply (never stored in code)."""
from __future__ import annotations

import aiohttp

from core import config as C

WU = "https://api.weather.com/v2/pws/observations/current"
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"

CODES = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
         51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
         61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
         71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "showers", 81: "heavy showers",
         82: "violent showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorms",
         96: "thunderstorms with hail", 99: "thunderstorms with hail"}


def parse_current(d: dict) -> dict:
    o = d["observations"][0]
    m = o.get("metric") or {}
    temp = m.get("temp")
    feels = m.get("windChill") if temp is not None and temp < 10 else m.get("heatIndex")
    return {"ts": int(o["epoch"]), "temp": temp, "feels": feels,
            "humidity": o.get("humidity"), "dew": m.get("dewpt"), "wind": m.get("windSpeed"), "gust": m.get("windGust"),
            "dir": o.get("winddir"), "pressure": m.get("pressure"), "rain_rate": m.get("precipRate"),
            "rain_today": m.get("precipTotal"), "uv": o.get("uv"), "solar": o.get("solarRadiation"),
            "lat": o.get("lat"), "lon": o.get("lon")}


def parse_forecast(d: dict) -> list[dict]:
    h = d.get("hourly") or {}
    out = []
    for i, t in enumerate(h.get("time") or []):
        out.append({"time": t, "temp": h["temperature_2m"][i], "rain_prob": h["precipitation_probability"][i],
                    "rain": h["precipitation"][i], "gust": h["wind_gusts_10m"][i], "code": h["weather_code"][i]})
    return out


async def current(http, key: str, station: str) -> dict:
    params = {"stationId": station, "format": "json", "units": "m", "numericPrecision": "decimal", "apiKey": key}
    try:
        async with http.get(WU, params=params) as r:
            if r.status == 204:
                raise RuntimeError("station hasn't reported recently")
            if r.status in (401, 403):
                raise RuntimeError("WU API key rejected")
            if r.status >= 400:
                raise RuntimeError(f"Weather Underground returned HTTP {r.status}")
            return parse_current(await r.json(content_type=None))
    except (aiohttp.ClientError, TimeoutError) as exc:
        # aiohttp may include the full request URL, including the API key.
        raise RuntimeError(f"Weather Underground request failed ({type(exc).__name__})") from None


async def forecast(http, lat: float, lon: float) -> list[dict]:
    params = {"latitude": f"{lat:.2f}", "longitude": f"{lon:.2f}", "timezone": C.TIMEZONE, "forecast_days": "2",
              "wind_speed_unit": "kmh",
              "hourly": "temperature_2m,precipitation_probability,precipitation,wind_gusts_10m,weather_code"}
    async with http.get(OPEN_METEO, params=params) as r:
        r.raise_for_status()
        return parse_forecast(await r.json(content_type=None))


def compass(deg) -> str:
    if deg is None:
        return "?"
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int((deg % 360) / 45 + 0.5) % 8]
