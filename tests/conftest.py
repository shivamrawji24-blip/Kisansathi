"""Test harness: a fake upstream HTTP server that speaks the same JSON as
Open-Meteo (geocoding + forecast), OSRM and data.gov.in/Agmarknet.

The synthetic payloads live ONLY in the tests; the application itself never
contains mock data. Because the app talks to this server over real HTTP, the
tests exercise the actual request code, caching and stale-fallback paths.
"""
from __future__ import annotations

import json
import math
import re
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PLACES = {
    # name -> list of geocoder hits (a decoy in another state comes first for "Bundi")
    "kota": [("Kota", "Rajasthan", "Kota", 25.18, 75.83)],
    "bundi": [("Bundi", "Jharkhand", "Ranchi", 23.35, 85.33), ("Bundi", "Rajasthan", "Bundi", 25.44, 75.64)],
    "jhalawar": [("Jhalawar", "Rajasthan", "Jhalawar", 24.60, 76.16)],
    "baran": [("Baran", "Rajasthan", "Baran", 25.10, 76.52)],
    "ramganj mandi": [("Ramganj Mandi", "Rajasthan", "Kota", 24.65, 75.93)],
    "jaipur": [("Jaipur", "Rajasthan", "Jaipur", 26.91, 75.79)],
    "jodhpur": [("Jodhpur", "Rajasthan", "Jodhpur", 26.29, 73.02)],
    "ludhiana": [("Ludhiana", "Punjab", "Ludhiana", 30.90, 75.85)],
}

# market -> (district, modal price Rs/q for tomato, latest date)
MARKETS = {
    "Kota": ("Kota", 2400, "31/10/2026"),
    "Bundi": ("Bundi", 2600, "31/10/2026"),
    "Ramganj Mandi": ("Kota", 2900, "30/10/2026"),
    "Jhalawar": ("Jhalawar", 3200, "31/10/2026"),
    "Baran": ("Baran", 2300, "31/10/2026"),
    "Jaipur": ("Jaipur", 3000, "31/10/2026"),
    "Jodhpur": ("Jodhpur", 3600, "31/10/2026"),  # beyond the default 300 km
}


def _hav(a, b, c, d):
    r = 6371.0
    p1, p2 = math.radians(a), math.radians(c)
    dphi, dl = p2 - p1, math.radians(d - b)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


class FakeUpstream(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.fail: set[str] = set()      # path prefixes that should answer HTTP 500
        self.calls: list[str] = []       # every request path seen
        self.base_temp = 33.0            # daytime peak is base_temp, night is ~ base_temp - 12
        self.rain_mm = [0, 0, 0, 0, 0, 0, 0]
        self.llm_script: list[dict] = []  # canned responses for the fake LLM endpoints
        self.llm_requests: list[dict] = []
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def count(self, prefix: str) -> int:
        return sum(1 for c in self.calls if c.startswith(prefix))


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def _send(self, code: int, payload) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # fake LLM endpoints
        srv: FakeUpstream = self.server  # type: ignore[assignment]
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        srv.llm_requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
        srv.calls.append(self.path)
        if not srv.llm_script:
            return self._send(500, {"error": "no scripted response"})
        self._send(200, srv.llm_script.pop(0))

    def do_GET(self):
        srv: FakeUpstream = self.server  # type: ignore[assignment]
        u = urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        srv.calls.append(u.path)
        if any(u.path.startswith(p) for p in srv.fail):
            return self._send(500, {"error": "boom"})
        if u.path == "/geocode":
            hits = PLACES.get(q.get("name", "").strip().lower(), [])
            if not hits:
                return self._send(200, {"generationtime_ms": 1})  # real API omits "results" when empty
            return self._send(200, {"results": [
                {"name": n, "admin1": a1, "admin2": a2, "latitude": la, "longitude": lo, "country": "India",
                 "population": 1000} for n, a1, a2, la, lo in hits]})
        if u.path == "/weather":
            return self._send(200, self._weather(srv, float(q["latitude"]), float(q["longitude"])))
        m = re.match(r"/route/v1/driving/([-\d.]+),([-\d.]+);([-\d.]+),([-\d.]+)", unquote(u.path))
        if m:
            lo1, la1, lo2, la2 = map(float, m.groups())
            km = _hav(la1, lo1, la2, lo2) * 1.3
            return self._send(200, {"code": "Ok", "routes": [{"distance": km * 1000, "duration": km / 40 * 3600}]})
        if u.path == "/mandi":
            return self._send(200, self._mandi(q))
        self._send(404, {"error": "not found"})

    @staticmethod
    def _weather(srv: "FakeUpstream", lat: float, lon: float) -> dict:
        start = datetime.now(timezone.utc).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0) + timedelta(seconds=19800)
        start = start.replace(hour=0)
        times, temps, hum, prec = [], [], [], []
        for h in range(7 * 24):
            t = start + timedelta(hours=h)
            times.append(t.strftime("%Y-%m-%dT%H:%M"))
            diurnal = math.sin((t.hour - 9) / 24 * 2 * math.pi)  # peak around 15:00
            temps.append(round(srv.base_temp - 6 + 6 * diurnal - (lat - 25) * 0.3, 2))
            hum.append(50)
            prec.append(0.0)
        days = [(start + timedelta(days=d)).strftime("%Y-%m-%d") for d in range(7)]
        tmax = [round(max(temps[d * 24:(d + 1) * 24]), 1) for d in range(7)]
        tmin = [round(min(temps[d * 24:(d + 1) * 24]), 1) for d in range(7)]
        return {"latitude": lat, "longitude": lon, "timezone": "Asia/Kolkata", "utc_offset_seconds": 19800,
                "hourly": {"time": times, "temperature_2m": temps, "relative_humidity_2m": hum, "precipitation": prec},
                "daily": {"time": days, "temperature_2m_max": tmax, "temperature_2m_min": tmin,
                          "precipitation_sum": list(srv.rain_mm), "precipitation_probability_max": [10] * 7,
                          "et0_fao_evapotranspiration": [4.5] * 7}}

    @staticmethod
    def _mandi(q: dict) -> dict:
        commodity = q.get("filters[commodity]", "")
        if commodity != "Tomato":
            return {"total": 0, "count": 0, "records": []}
        recs = []
        for market, (district, modal, date) in MARKETS.items():
            # an older, different price for the same market to prove "latest date wins"
            recs.append({"State": "Rajasthan", "District": district, "Market": market, "Commodity": "Tomato",
                         "Variety": "Other", "Arrival_Date": "20/10/2026", "Min_x0020_Price": str(modal - 900),
                         "Max_x0020_Price": str(modal - 300), "Modal_x0020_Price": str(modal - 600)})
            recs.append({"State": "Rajasthan", "District": district, "Market": market, "Commodity": "Tomato",
                         "Variety": "Other", "Arrival_Date": date, "Min_x0020_Price": str(modal - 400),
                         "Max_x0020_Price": str(modal + 400), "Modal_x0020_Price": str(modal)})
        recs.append({"State": "Punjab", "District": "Ludhiana", "Market": "Ludhiana", "Commodity": "Tomato",
                     "Variety": "Other", "Arrival_Date": "31/10/2026", "Min_x0020_Price": "1500",
                     "Max_x0020_Price": "2100", "Modal_x0020_Price": "1800"})
        state = q.get("filters[state.keyword]")
        if state:
            recs = [r for r in recs if r["State"] == state]
        return {"total": len(recs), "count": len(recs), "records": recs}


@pytest.fixture()
def upstream():
    srv = FakeUpstream()
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture()
def env(upstream, monkeypatch, tmp_path):
    """Point the app at the fake upstream with an empty cache; no LLM keys."""
    for k in ("ANTHROPIC_API_KEY", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("WEATHER_URL", upstream.url + "/weather")
    monkeypatch.setenv("GEOCODE_URL", upstream.url + "/geocode")
    monkeypatch.setenv("OSRM_URL", upstream.url)
    monkeypatch.setenv("MANDI_URL", upstream.url + "/mandi")
    monkeypatch.setenv("DATA_GOV_API_KEY", "test-key")
    monkeypatch.setenv("CACHE_PATH", str(tmp_path / "cache.json"))
    monkeypatch.setenv("HTTP_TIMEOUT", "5")
    from app import http_cache, rag
    http_cache.set_outage([])
    rag.reset_index()
    yield upstream
    http_cache.set_outage([])
