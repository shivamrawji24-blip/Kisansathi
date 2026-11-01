"""Service layer: combines live data sources with the engines. Used by both the
HTTP API and the agent tools, so the website and the chatbot always agree."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from . import datasources as ds
from .crops import resolve_crop
from .errors import MissingInput
from .fmt import merge_meta
from .http_cache import DataUnavailable
from .market_compare import compare_markets
from .profit import compute_profit
from .schemas import CompareInput, ProfitInput


def geocode_service(query: str, lang: str = "en") -> dict[str, Any]:
    g = ds.geocode(query, lang=lang)
    return {"query": query, "results": g["results"], "sources": merge_meta([g["meta"]])}


def resolve_location(*, place: str | None = None, lat: float | None = None, lon: float | None = None,
                     state: str | None = None, district: str | None = None, lang: str = "en",
                     fallback: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[dict]]:
    """Return (location, metas). Order: explicit coordinates -> place name -> saved location."""
    if lat is not None and lon is not None:
        return {"name": place, "lat": lat, "lon": lon, "state": state, "district": district}, []
    if place:
        g = ds.geocode(place, lang=lang)
        hits = g["results"]
        if not hits:
            raise MissingInput("location", f"I could not find '{place}'. Try the nearest town name.",
                               f"'{place}' नहीं मिला। नज़दीकी कस्बे का नाम आज़माइए।")
        if state:
            same = [h for h in hits if ds.state_key(h.get("admin1")) == ds.state_key(state)]
            hits = same or hits
        h = hits[0]
        return ({"name": h["name"], "lat": h["lat"], "lon": h["lon"], "state": h.get("admin1") or state,
                 "district": h.get("admin2") or district}, [g["meta"]])
    if fallback and fallback.get("lat") is not None and fallback.get("lon") is not None:
        return {**fallback, "state": fallback.get("state") or state,
                "district": fallback.get("district") or district}, []
    raise MissingInput("location", "Tell me your village or town (or pick a location) first.",
                       "पहले अपना गाँव या शहर बताइए (या लोकेशन चुनिए)।")


def weather_service(lat: float, lon: float, crop_id: str | None = None, days: int = 7) -> dict[str, Any]:
    crop = resolve_crop(crop_id) if crop_id else None
    w = ds.forecast(lat, lon, days)
    summary = ds.weather_summary(w, crop)
    return {"lat": lat, "lon": lon, "days": summary["days"], "alerts": summary["alerts"],
            "timezone": summary["timezone"], "sources": merge_meta([w["meta"]])}


def mandi_service(crop_id: str, state: str | None, district: str | None) -> dict[str, Any]:
    crop = resolve_crop(crop_id)
    p = ds.mandi_prices(crop, state, district)
    return {"crop": crop["id"], "commodity": p["commodity"], "unit": p["unit"], "state": state,
            "markets": p["markets"], "band": p["band"], "note": p["note"],
            "sources": merge_meta([p["meta"]])}


def profit_service(inp: ProfitInput) -> dict[str, Any]:
    crop = resolve_crop(inp.crop)
    metas: list[dict | None] = []
    price, price_error = None, None
    if inp.price_per_q is None:
        try:
            price = ds.mandi_prices(crop, inp.state, inp.district)
            metas.append(price["meta"])
            if price["band"]:
                price["band"]["scope"] = inp.state if (inp.state and price["note"] != "no_rows_for_state") else "India"
            else:
                price, price_error = None, f"no recent {crop['en']} prices found"
        except DataUnavailable as exc:
            price_error = f"{exc.source}: {exc.detail}"
    alerts: list[dict[str, Any]] = []
    weather_error = None
    if inp.lat is not None and inp.lon is not None:
        try:
            w = ds.forecast(inp.lat, inp.lon, 7)
            metas.append(w["meta"])
            alerts = ds.weather_summary(w, crop)["alerts"]
        except DataUnavailable as exc:
            weather_error = f"{exc.source}: {exc.detail}"
    result = compute_profit(inp, price, alerts)
    result["price_error"] = price_error
    result["weather_error"] = weather_error
    result["sources"] = merge_meta(metas)
    return result


def compare_service(inp: CompareInput, lang: str = "en", fallback: dict[str, Any] | None = None,
                    now_utc: datetime | None = None) -> dict[str, Any]:
    crop = resolve_crop(inp.crop)
    origin, metas = resolve_location(place=inp.origin_place, lat=inp.lat, lon=inp.lon, state=inp.state,
                                     district=inp.district, lang=lang, fallback=fallback)
    result = compare_markets(inp, crop, origin, now_utc=now_utc)
    result["sources"] = merge_meta([*metas, *result["sources"]])
    return result
