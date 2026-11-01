"""Live open-data connectors: Open-Meteo (geocoding + weather), OSRM (routing),
Agmarknet via data.gov.in (mandi prices). Every function returns real API data
plus a ``meta`` block (fetched_at / stale / error) - never placeholder numbers.
"""
from __future__ import annotations

import math
import re
import statistics
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .config import get_settings
from .http_cache import DataUnavailable, fetch_json

# ----------------------------------------------------------------- helpers

_STATE_ALIASES = {
    "nctofdelhi": "delhi", "nationalcapitalterritoryofdelhi": "delhi",
    "uttrakhand": "uttarakhand", "orissa": "odisha", "pondicherry": "puducherry",
    "jammuandkashmir": "jammukashmir", "andamanandnicobarislands": "andamannicobar",
    "dadraandnagarhavelianddamananddiu": "dnhdd",
}


def state_key(name: str | None) -> str:
    """Normalise state names so Open-Meteo ('Uttarakhand') matches Agmarknet ('Uttrakhand')."""
    k = re.sub(r"[^a-z]", "", (name or "").lower())
    return _STATE_ALIASES.get(k, k)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ----------------------------------------------------------------- geocoding

def geocode(name: str, lang: str = "en", count: int = 5, country: str = "IN") -> dict[str, Any]:
    """Open-Meteo geocoding. Works for English and Hindi place names."""
    name = (name or "").strip()
    if len(name) < 2:
        raise DataUnavailable("geocode", "place name too short")

    def transform(raw: Any) -> list[dict[str, Any]]:
        out = []
        for r in (raw or {}).get("results", []) or []:
            out.append({
                "name": r.get("name"), "admin1": r.get("admin1"), "admin2": r.get("admin2"),
                "country": r.get("country"), "lat": r.get("latitude"), "lon": r.get("longitude"),
                "population": r.get("population"),
            })
        return out

    params = {"name": name, "count": count, "language": "hi" if lang == "hi" else "en", "format": "json"}
    if country:
        params["countryCode"] = country
    results, meta = fetch_json(get_settings().geocode_url, params, source="geocode", ttl=7 * 86400,
                               transform=transform)
    return {"results": results, "meta": meta}


# ----------------------------------------------------------------- weather

def forecast(lat: float, lon: float, days: int = 7) -> dict[str, Any]:
    params = {
        "latitude": round(lat, 4), "longitude": round(lon, 4),
        "hourly": "temperature_2m,relative_humidity_2m,precipitation",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,"
                 "precipitation_probability_max,et0_fao_evapotranspiration",
        "forecast_days": max(1, min(days, 16)), "timezone": "auto",
    }

    def transform(raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict) or "hourly" not in raw or "daily" not in raw:
            raise DataUnavailable("weather", "unexpected response shape")
        return {k: raw.get(k) for k in ("latitude", "longitude", "timezone", "utc_offset_seconds",
                                        "hourly", "daily")}

    data, meta = fetch_json(get_settings().weather_url, params, source="weather", ttl=1800, transform=transform)
    return {"data": data, "meta": meta}


def weather_alerts(daily_rows: list[dict[str, Any]], crop: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Threshold alerts, merged so consecutive days of the same kind become ONE alert with a date range.
    Rainfall classes follow IMD's daily-rainfall bands (moderate >= 15.6 mm, heavy >= 64.5 mm);
    heat/cold limits come from the crop table."""
    heat_limit = crop["heat_c"] if crop else 40
    cold_limit = crop["cold_c"] if crop else 2
    hits: dict[str, list[int]] = {"heat": [], "cold": [], "heavy_rain": [], "rain": []}
    for i, row in enumerate(daily_rows):
        tmax, tmin, rain = row.get("tmax"), row.get("tmin"), row.get("rain_mm")
        if tmax is not None and tmax >= heat_limit:
            hits["heat"].append(i)
        if tmin is not None and tmin <= cold_limit:
            hits["cold"].append(i)
        if rain is not None and rain >= 64.5:
            hits["heavy_rain"].append(i)
        elif rain is not None and rain >= 15.6 and (crop is None or crop.get("rain_sensitive")):
            hits["rain"].append(i)

    def runs(idx: list[int]) -> list[list[int]]:
        out: list[list[int]] = []
        for i in idx:
            if out and i == out[-1][-1] + 1:
                out[-1].append(i)
            else:
                out.append([i])
        return out

    alerts: list[dict[str, Any]] = []
    for kind, idx in hits.items():
        for run in runs(idx):
            d1, d2 = daily_rows[run[0]]["date"], daily_rows[run[-1]]["date"]
            en_d = d1 if d1 == d2 else f"{d1} to {d2}"
            hi_d = d1 if d1 == d2 else f"{d1} से {d2}"
            if kind == "heat":
                v = max(daily_rows[i]["tmax"] for i in run)
                en = f"Heat risk {en_d}: max up to {v:.0f}°C (crop stress above {heat_limit}°C). Irrigate in the evening; avoid spraying at midday."
                hi = f"{hi_d} गर्मी का खतरा: अधिकतम {v:.0f}°C तक (फसल पर तनाव {heat_limit}°C से ऊपर)। शाम को सिंचाई करें; दोपहर में छिड़काव न करें।"
            elif kind == "cold":
                v = min(daily_rows[i]["tmin"] for i in run)
                en = f"Cold/frost risk {en_d}: min down to {v:.0f}°C (crop sensitive at/below {cold_limit}°C). Light irrigation in the evening can reduce frost damage."
                hi = f"{hi_d} ठंड/पाले का खतरा: न्यूनतम {v:.0f}°C तक (फसल {cold_limit}°C या कम पर संवेदनशील)। शाम को हल्की सिंचाई से पाले का नुकसान कम हो सकता है।"
            elif kind == "heavy_rain":
                v = max(daily_rows[i]["rain_mm"] for i in run)
                en = f"Heavy rain expected {en_d}: up to {v:.0f} mm in a day. Clear drains, delay spraying and fertiliser, and harvest/cover produce before it."
                hi = f"{hi_d} भारी बारिश की संभावना: एक दिन में {v:.0f} मिमी तक। नालियाँ साफ करें, छिड़काव और खाद टालें, और उससे पहले कटाई करें या उपज ढकें।"
            else:
                v = max(daily_rows[i]["rain_mm"] for i in run)
                en = f"Moderate rain expected {en_d}: up to {v:.0f} mm in a day. Avoid spraying and fertiliser on those days; protect harvested produce."
                hi = f"{hi_d} मध्यम बारिश की संभावना: एक दिन में {v:.0f} मिमी तक। उन दिनों छिड़काव और खाद न दें; कटी हुई उपज को ढकें।"
            alerts.append({"type": kind, "date": d1, "date_end": d2, "value": v, "en": en, "hi": hi})
    order = {"heavy_rain": 0, "heat": 1, "cold": 2, "rain": 3}
    alerts.sort(key=lambda a: (a["date"], order[a["type"]]))
    return alerts


def weather_summary(w: dict[str, Any], crop: dict[str, Any] | None = None) -> dict[str, Any]:
    d = w["data"]["daily"]
    rows = []
    for i, day in enumerate(d["time"]):
        rows.append({
            "date": day,
            "tmax": d["temperature_2m_max"][i], "tmin": d["temperature_2m_min"][i],
            "rain_mm": d["precipitation_sum"][i],
            "rain_prob": (d.get("precipitation_probability_max") or [None] * len(d["time"]))[i],
            "et0_mm": (d.get("et0_fao_evapotranspiration") or [None] * len(d["time"]))[i],
        })
    return {"days": rows, "alerts": weather_alerts(rows, crop), "timezone": w["data"].get("timezone"),
            "meta": w["meta"]}


class HourlySeries:
    """Hourly temperature series with linear interpolation (clamped at the ends)."""

    def __init__(self, times: list[datetime], temps: list[float]):
        pairs = [(t, v) for t, v in zip(times, temps) if v is not None]
        if not pairs:
            raise DataUnavailable("weather", "no hourly temperatures in forecast")
        self.times = [p[0] for p in pairs]
        self.temps = [p[1] for p in pairs]

    @classmethod
    def from_forecast(cls, w: dict[str, Any]) -> "HourlySeries":
        h = w["data"]["hourly"]
        return cls([datetime.fromisoformat(t) for t in h["time"]], h["temperature_2m"])

    def at(self, when: datetime) -> float:
        ts, vs = self.times, self.temps
        if when <= ts[0]:
            return vs[0]
        if when >= ts[-1]:
            return vs[-1]
        lo, hi = 0, len(ts) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if ts[mid] <= when:
                lo = mid
            else:
                hi = mid
        span = (ts[hi] - ts[lo]).total_seconds() or 1
        frac = (when - ts[lo]).total_seconds() / span
        return vs[lo] + (vs[hi] - vs[lo]) * frac


def local_now(w: dict[str, Any], now_utc: datetime | None = None) -> datetime:
    """Current naive local time at the forecast location (forecast timestamps are local)."""
    offset = w["data"].get("utc_offset_seconds") or 0
    base = now_utc or datetime.now(timezone.utc)
    return base.replace(tzinfo=None) + timedelta(seconds=offset)


# ----------------------------------------------------------------- routing

def route(o_lat: float, o_lon: float, d_lat: float, d_lon: float) -> dict[str, Any]:
    """Road distance/time from OSRM; falls back to a clearly-flagged estimate."""
    base = get_settings().osrm_url.rstrip("/")
    url = f"{base}/route/v1/driving/{o_lon:.5f},{o_lat:.5f};{d_lon:.5f},{d_lat:.5f}"

    def transform(raw: Any) -> dict[str, Any]:
        routes = (raw or {}).get("routes") or []
        if not routes:
            raise DataUnavailable("route", "no route found")
        return {"distance_km": routes[0]["distance"] / 1000.0, "duration_h": routes[0]["duration"] / 3600.0}

    try:
        data, meta = fetch_json(url, {"overview": "false"}, source="route", ttl=7 * 86400, transform=transform)
        return {**data, "estimated": False, "meta": meta}
    except DataUnavailable as exc:
        straight = haversine_km(o_lat, o_lon, d_lat, d_lon)
        road = straight * 1.35  # typical road/straight-line ratio
        return {"distance_km": road, "duration_h": road / 35.0, "estimated": True,
                "meta": {"source": "route", "fetched_at": None, "stale": True, "from_cache": False,
                         "age_s": None, "error": f"{exc.detail}; using straight-line x1.35 at 35 km/h"}}


# ----------------------------------------------------------------- mandi prices

def _norm_key(k: str) -> str:
    k = k.lower().replace("_x0020_", "_")
    return re.sub(r"[^a-z0-9]+", "_", k).strip("_")


def _to_float(v: Any) -> float | None:
    try:
        f = float(str(v).replace(",", "").strip())
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _parse_date(v: Any) -> date | None:
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(str(v).strip(), fmt).date()
        except ValueError:
            continue
    return None


def _normalize_records(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, dict) or "records" not in raw:
        msg = (raw or {}).get("message") if isinstance(raw, dict) else None
        raise DataUnavailable("mandi", msg or "unexpected response shape (no 'records')")
    out = []
    for rec in raw["records"]:
        r = {_norm_key(k): v for k, v in rec.items()}
        modal, mn, mx = _to_float(r.get("modal_price")), _to_float(r.get("min_price")), _to_float(r.get("max_price"))
        when = _parse_date(r.get("arrival_date"))
        if modal is None or when is None:
            continue
        out.append({
            "state": (r.get("state") or "").strip(), "district": (r.get("district") or "").strip(),
            "market": (r.get("market") or "").strip(), "commodity": (r.get("commodity") or "").strip(),
            "variety": (r.get("variety") or "").strip(), "date": when.isoformat(),
            "min": mn or modal, "max": mx or modal, "modal": modal,
        })
    return out


def mandi_records(crop: dict[str, Any], state: str | None = None) -> dict[str, Any]:
    """Raw normalized Agmarknet rows (Rs/quintal) for a crop, optionally one state."""
    settings = get_settings()
    if not settings.data_gov_api_key:
        raise DataUnavailable("mandi", "DATA_GOV_API_KEY is not set (free key from data.gov.in)")
    attempts = []
    for name in crop["agmarknet"]:
        if state:
            attempts.append({"filters[commodity]": name, "filters[state.keyword]": state})
        attempts.append({"filters[commodity]": name})
    last_meta: dict[str, Any] = {}
    for flt in attempts:
        params = {"api-key": settings.data_gov_api_key, "format": "json", "limit": 1000, **flt}
        records, meta = fetch_json(settings.mandi_url, params, source="mandi", ttl=3600,
                                   transform=_normalize_records)
        last_meta = meta
        if records:
            return {"records": records, "commodity": flt["filters[commodity]"], "meta": meta}
    return {"records": [], "commodity": crop["agmarknet"][0], "meta": last_meta}


def summarize_markets(records: list[dict[str, Any]], state: str | None = None,
                      district: str | None = None) -> dict[str, Any]:
    """Latest price per market (+ price band across markets)."""
    note = None
    rows = records
    if state:
        in_state = [r for r in rows if state_key(r["state"]) == state_key(state)]
        if in_state:
            rows = in_state
        else:
            note = "no_rows_for_state"
    latest: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in rows:
        k = (r["state"], r["district"], r["market"])
        cur = latest.setdefault(k, {"date": r["date"], "rows": []})
        if r["date"] > cur["date"]:
            cur["date"], cur["rows"] = r["date"], []
        if r["date"] == cur["date"]:
            cur["rows"].append(r)
    markets = []
    for (st, dist, mkt), v in latest.items():
        rs = v["rows"]
        markets.append({
            "state": st, "district": dist, "market": mkt, "date": v["date"],
            "min": statistics.mean(x["min"] for x in rs), "max": statistics.mean(x["max"] for x in rs),
            "modal": statistics.mean(x["modal"] for x in rs), "varieties": sorted({x["variety"] for x in rs if x["variety"]}),
        })
    if district:
        local = [m for m in markets if m["district"].lower() == district.lower()]
        if local:
            markets = local
    markets.sort(key=lambda m: m["modal"], reverse=True)
    band = None
    if markets:
        band = {
            "low": statistics.median(m["min"] for m in markets),
            "mid": statistics.median(m["modal"] for m in markets),
            "high": statistics.median(m["max"] for m in markets),
            "n_markets": len(markets), "as_of": max(m["date"] for m in markets),
        }
    return {"markets": markets, "band": band, "note": note}


def mandi_prices(crop: dict[str, Any], state: str | None = None, district: str | None = None) -> dict[str, Any]:
    raw = mandi_records(crop, state)
    summary = summarize_markets(raw["records"], state, district)
    return {"crop": crop["id"], "commodity": raw["commodity"], "unit": "INR/quintal",
            **summary, "meta": raw["meta"]}


def locate_market(market: str, district: str, state: str) -> dict[str, Any] | None:
    """Mandi coordinates are not in Agmarknet, so geocode the market (then the district)."""
    for query, confidence in ((market, "market"), (district, "district")):
        if not query:
            continue
        try:
            hits = geocode(query, count=8)["results"]
        except DataUnavailable:
            continue
        for h in hits:
            if state_key(h.get("admin1")) == state_key(state) and h.get("lat") is not None:
                return {"lat": h["lat"], "lon": h["lon"], "confidence": confidence}
    return None
