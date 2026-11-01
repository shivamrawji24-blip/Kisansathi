"""SpoilClock engine: which mandi nets the farmer the most *after* spoilage,
transport and fees - using live prices, live routes and live forecast temperatures."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any

from . import datasources as ds
from .errors import MissingInput
from .fmt import inr, merge_meta
from .http_cache import DataUnavailable
from .schemas import CompareInput
from .shelf_life import simulate_quality

DEPARTURE_OFFSETS_H = list(range(0, 24, 2))
MAX_GEOCODE = 24


def _evaluate(inp: CompareInput, crop: dict[str, Any], market: dict[str, Any], rt: dict[str, Any],
              origin_series: ds.HourlySeries, dest_series: ds.HourlySeries, depart: datetime) -> dict[str, Any]:
    sim = simulate_quality(
        crop, depart=depart, journey_h=rt["duration_h"], temp_origin=origin_series.at,
        temp_dest=dest_series.at, hold_before_h=inp.hold_before_h, wait_after_h=inp.wait_after_h,
        vehicle=inp.vehicle,
    )
    ideal = inp.quantity_q * market["modal"]
    gross = ideal * sim["price_multiplier"]
    commission = gross * inp.commission_pct / 100.0
    transport = rt["distance_km"] * inp.cost_per_km
    handling = inp.quantity_q * inp.handling_per_q
    net = gross - commission - transport - handling
    return {
        "depart": depart.isoformat(timespec="minutes"), "quality": sim["quality"],
        "price_multiplier": sim["price_multiplier"], "sellable": sim["sellable"],
        "avg_temp_c": sim["avg_temp_c"], "gross": gross, "commission": commission,
        "transport": transport, "handling": handling, "net": net,
        "spoilage_loss": ideal - gross,
    }


def _label(o: dict[str, Any]) -> str:
    return o["market"] if o["district"].strip().lower() == o["market"].strip().lower() else f"{o['market']} ({o['district']})"


def _explain(best: dict[str, Any], top_price: dict[str, Any], nearest: dict[str, Any], qty: float) -> dict[str, str]:
    en = [f"Best choice: {_label(best)} — expected net {inr(best['net'])} for {qty:g} quintal, "
          f"leaving at {best['best_departure']['depart'][11:16]} with about {best['best_departure']['quality']:.0%} quality on arrival."]
    hi = [f"सबसे अच्छा विकल्प: {_label(best)} — {qty:g} क्विंटल पर अनुमानित शुद्ध आय {inr(best['net'])}; "
          f"{best['best_departure']['depart'][11:16]} बजे निकलें, पहुँचने पर गुणवत्ता लगभग {best['best_departure']['quality']:.0%}।"]
    if top_price["market"] != best["market"]:
        diff = best["net"] - top_price["net"]
        en.append(f"{top_price['market']} pays the highest price ({inr(top_price['price']['modal'])}/q), but after "
                  f"{top_price['distance_km']:.0f} km in about {top_price['avg_temp_c']:.0f}°C the quality drops to "
                  f"{top_price['best_departure']['quality']:.0%}, so it nets {inr(abs(diff))} {'less' if diff >= 0 else 'more'}.")
        hi.append(f"{top_price['market']} में सबसे ऊँचा भाव ({inr(top_price['price']['modal'])}/क्विंटल) है, पर "
                  f"{top_price['distance_km']:.0f} किमी और औसतन {top_price['avg_temp_c']:.0f}°C में गुणवत्ता "
                  f"{top_price['best_departure']['quality']:.0%} रह जाती है, इसलिए शुद्ध आय {inr(abs(diff))} {'कम' if diff >= 0 else 'ज़्यादा'} रहती है।")
    if nearest["market"] != best["market"]:
        diff = best["net"] - nearest["net"]
        en.append(f"Compared with the nearest market ({nearest['market']}, {nearest['distance_km']:.0f} km) it is "
                  f"{inr(abs(diff))} {'better' if diff >= 0 else 'worse'}.")
        hi.append(f"सबसे नज़दीकी मंडी ({nearest['market']}, {nearest['distance_km']:.0f} किमी) की तुलना में यह "
                  f"{inr(abs(diff))} {'बेहतर' if diff >= 0 else 'कमज़ोर'} है।")
    if not best["best_departure"]["sellable"] or best["net"] <= 0:
        en.append("Warning: even the best option is poor. Consider cold storage, a closer market, or a covered/refrigerated vehicle.")
        hi.append("चेतावनी: सबसे अच्छा विकल्प भी कमज़ोर है। कोल्ड स्टोरेज, नज़दीकी मंडी या ढकी/ठंडी गाड़ी पर विचार करें।")
    return {"en": " ".join(en), "hi": " ".join(hi)}


def compare_markets(inp: CompareInput, crop: dict[str, Any], origin: dict[str, Any],
                    now_utc: datetime | None = None) -> dict[str, Any]:
    if origin.get("lat") is None or origin.get("lon") is None:
        raise MissingInput("location", "Tell me your village or town (or pick a location) first.",
                           "पहले अपना गाँव या शहर बताइए (या लोकेशन चुनिए)।")
    state = origin.get("state") or inp.state
    if not state:
        raise MissingInput("state", "State is needed to find mandi prices. Pick a location from the search results.",
                           "मंडी भाव खोजने के लिए राज्य चाहिए। खोज परिणाम से लोकेशन चुनिए।")

    # 1) live prices for every market in the state
    prices = ds.mandi_prices(crop, state)
    markets = prices["markets"]
    if inp.markets:
        wanted = [m.lower() for m in inp.markets]
        markets = [m for m in markets if any(w in m["market"].lower() for w in wanted)]
    if not markets:
        raise DataUnavailable("mandi", f"no recent {crop['en']} prices found for {state}")

    # 2) locate markets (geocode) - same district first, then dearest first
    district = (origin.get("district") or inp.district or "").lower()
    ordered = sorted(markets, key=lambda m: (m["district"].lower() != district, -m["modal"]))[:MAX_GEOCODE]

    def locate(m: dict[str, Any]) -> dict[str, Any] | None:
        loc = ds.locate_market(m["market"], m["district"], m["state"])
        return {**m, **loc} if loc else None

    with ThreadPoolExecutor(max_workers=8) as pool:
        located = [m for m in pool.map(locate, ordered) if m]
    for m in located:
        m["straight_km"] = ds.haversine_km(origin["lat"], origin["lon"], m["lat"], m["lon"])
    in_range = sorted((m for m in located if m["straight_km"] <= inp.max_distance_km), key=lambda m: m["straight_km"])
    if not in_range:
        raise DataUnavailable("route", f"no mandi with prices found within {inp.max_distance_km:.0f} km")
    chosen = in_range[:3]
    rest = sorted(in_range[3:], key=lambda m: -m["modal"])
    chosen += rest[: max(0, inp.top_n - len(chosen))]

    # 3) live weather at origin and every destination + live road routes
    origin_w = ds.forecast(origin["lat"], origin["lon"], days=3)
    origin_series = ds.HourlySeries.from_forecast(origin_w)

    def fetch_for(m: dict[str, Any]) -> dict[str, Any]:
        try:
            rt = ds.route(origin["lat"], origin["lon"], m["lat"], m["lon"])
            dw = ds.forecast(m["lat"], m["lon"], days=3)
            return {"market": m, "route": rt, "weather": dw}
        except DataUnavailable as exc:
            return {"market": m, "error": exc.detail}

    with ThreadPoolExecutor(max_workers=6) as pool:
        fetched = list(pool.map(fetch_for, chosen))

    now = ds.local_now(origin_w, now_utc).replace(minute=0, second=0, microsecond=0)
    options, skipped, metas = [], [], [origin_w["meta"], prices["meta"]]
    for f in fetched:
        m = f["market"]
        if "error" in f:
            skipped.append({"market": m["market"], "reason": f["error"]})
            continue
        rt, dw = f["route"], f["weather"]
        metas += [rt["meta"], dw["meta"]]
        dest_series = ds.HourlySeries.from_forecast(dw)
        runs = [_evaluate(inp, crop, m, rt, origin_series, dest_series, now + timedelta(hours=h))
                for h in DEPARTURE_OFFSETS_H]
        best_run = max(runs, key=lambda r: r["net"])
        options.append({
            "market": m["market"], "district": m["district"], "state": m["state"],
            "price": {"min": m["min"], "max": m["max"], "modal": m["modal"], "date": m["date"]},
            "location_confidence": m["confidence"],
            "distance_km": rt["distance_km"], "duration_h": rt["duration_h"], "route_estimated": rt["estimated"],
            "depart_now": runs[0], "best_departure": best_run,
            "net": best_run["net"], "net_per_q": best_run["net"] / inp.quantity_q,
            "avg_temp_c": best_run["avg_temp_c"],
        })
    if not options:
        raise DataUnavailable("route", "could not fetch routes/weather for any candidate mandi")
    options.sort(key=lambda o: o["net"], reverse=True)
    best = options[0]
    top_price = max(options, key=lambda o: o["price"]["modal"])
    nearest = min(options, key=lambda o: o["distance_km"])

    return {
        "crop": crop["id"], "quantity_q": inp.quantity_q,
        "origin": {k: origin.get(k) for k in ("name", "lat", "lon", "state", "district")},
        "vehicle": inp.vehicle, "options": options, "best": best["market"],
        "explanation": _explain(best, top_price, nearest, inp.quantity_q),
        "skipped": skipped,
        "assumptions": [
            {"en": f"Transport cost assumed at {inr(inp.cost_per_km)} per km (your input); commission {inp.commission_pct:g}%; handling {inr(inp.handling_per_q)}/quintal.",
             "hi": f"ढुलाई लागत {inr(inp.cost_per_km)} प्रति किमी (आपकी दी हुई); कमीशन {inp.commission_pct:g}%; लदाई-उतराई {inr(inp.handling_per_q)}/क्विंटल।"},
            {"en": "Mandi coordinates are found by geocoding the market (or its district) name — 'district' confidence means the district centre was used.",
             "hi": "मंडी की लोकेशन बाज़ार (या ज़िले) के नाम से खोजी जाती है — 'district' का मतलब ज़िले का केंद्र इस्तेमाल हुआ।"},
            {"en": "Spoilage model: quality falls exponentially and roughly doubles-to-triples per +10°C; crop parameters are approximate (see README).",
             "hi": "खराबी का मॉडल: गुणवत्ता घातांकीय रूप से घटती है और हर +10°C पर लगभग 2-3 गुना तेज़ होती है; फसल पैरामीटर अनुमानित हैं (README देखें)।"},
        ],
        "sources": merge_meta(metas),
        "prices_as_of": max(o["price"]["date"] for o in options),
    }
