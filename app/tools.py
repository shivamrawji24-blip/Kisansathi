"""Tool registry for the farmer agent: JSON schemas the LLM sees + the functions behind them.

Design rules (they are what keeps the agent honest):
  * every number the agent can quote comes out of a tool result,
  * tools never raise to the model - failures come back as {"error": ...} so the
    agent can retry with other arguments, use another tool, or tell the farmer,
  * every result carries its data source and freshness, collected in ``ctx.sources``.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import ValidationError

from . import rag
from .crops import CROPS, UnknownCrop, resolve_crop
from .errors import MissingInput
from .fmt import merge_meta
from .http_cache import DataUnavailable
from .schemas import CompareInput, ProfitInput
from . import services

log = logging.getLogger("kisansathi.tools")


@dataclass
class ToolContext:
    lang: str = "en"
    location: dict[str, Any] | None = None  # the farmer's saved location from the UI
    metas: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, str]] = field(default_factory=list)

    def sources(self) -> list[dict[str, Any]]:
        return merge_meta(self.metas)


def _loc_fallback(ctx: ToolContext) -> dict[str, Any] | None:
    return ctx.location if ctx.location and ctx.location.get("lat") is not None else None


# ------------------------------------------------------------------ tool bodies

def t_get_weather(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    loc, metas = services.resolve_location(place=args.get("place"), lat=args.get("lat"), lon=args.get("lon"),
                                           lang=ctx.lang, fallback=_loc_fallback(ctx))
    ctx.metas += metas
    out = services.weather_service(loc["lat"], loc["lon"], args.get("crop"))
    out["place"] = loc.get("name")
    out["days"] = out["days"][:7]
    return out


def t_get_mandi_prices(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    state = args.get("state") or (ctx.location or {}).get("state")
    district = args.get("district") or (ctx.location or {}).get("district")
    out = services.mandi_service(args["crop"], state, district)
    out["markets"] = out["markets"][:10]  # keep the model's context small
    return out


def t_compare_mandis(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    inp = CompareInput(
        crop=args["crop"], quantity_q=args["quantity_quintal"], origin_place=args.get("origin_place"),
        vehicle=args.get("vehicle", "open"), cost_per_km=args.get("cost_per_km", 20),
        commission_pct=args.get("commission_pct", 0), handling_per_q=args.get("handling_per_quintal", 0),
        top_n=min(int(args.get("max_options", 5)), 8),
    )
    out = services.compare_service(inp, lang=ctx.lang, fallback=_loc_fallback(ctx))
    slim = []
    for o in out["options"]:
        bd = o["best_departure"]
        slim.append({"market": o["market"], "district": o["district"], "price_modal": o["price"]["modal"],
                     "price_date": o["price"]["date"], "distance_km": round(o["distance_km"], 1),
                     "drive_hours": round(o["duration_h"], 1), "quality_on_arrival": round(bd["quality"], 3),
                     "net_income": round(o["net"]), "net_per_quintal": round(o["net_per_q"]),
                     "best_departure_time": bd["depart"], "spoilage_loss": round(bd["spoilage_loss"]),
                     "route_estimated": o["route_estimated"]})
    return {"crop": out["crop"], "quantity_quintal": out["quantity_q"], "best_market": out["best"],
            "options": slim, "explanation": out["explanation"][ctx.lang], "skipped": out["skipped"],
            "sources": out["sources"], "_full": out}


def t_estimate_profit(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    data = dict(args)
    data["area_acres"] = data.get("area_acres", 1)
    loc = ctx.location or {}
    for k in ("state", "district", "lat", "lon"):
        data.setdefault(k, loc.get(k))
    inp = ProfitInput(**{k: v for k, v in data.items() if v is not None})
    out = services.profit_service(inp)
    keep = {k: out[k] for k in ("crop", "area_acres", "price", "needs_price", "price_error", "inputs_defaulted",
                                "profit", "revenue", "roi_pct", "breakeven_price", "breakeven_yield_per_acre",
                                "total_cost", "sources") if k in out}
    keep["yield_per_acre_q"] = {k: round(v, 2) for k, v in out["yield"]["per_acre"].items()}
    keep["costs_per_acre"] = out["costs"]["per_acre"]
    keep["nutrients"] = [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in n.items()} for n in out["nutrients"]]
    keep["advice"] = [a[ctx.lang] for a in out["advice"]][:6]
    keep["_full"] = out
    return keep


def t_search_advisory(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    res = rag.search_advisory(args["query"], ctx.lang, k=3)
    for h in res["hits"]:
        ctx.citations.append({"title": h["title"], "file": h["file"], "source": h["source"]})
    if not res["found"]:
        res["note"] = "No matching advisory found. Say so honestly and suggest the local KVK / agriculture officer."
    return res


# ------------------------------------------------------------------ registry

CROP_LIST = ", ".join(sorted(CROPS))

TOOLS: dict[str, dict[str, Any]] = {
    "get_weather": {
        "fn": t_get_weather,
        "description": "Live 7-day weather forecast (temperature, rain, evapotranspiration) plus crop-specific heat, cold and rain alerts. "
                       "Use for any question about weather, irrigation timing, spraying or harvesting timing.",
        "input_schema": {"type": "object", "properties": {
            "place": {"type": "string", "description": "Village/town name. Omit to use the farmer's saved location."},
            "crop": {"type": "string", "description": f"Optional crop for crop-specific alerts. One of: {CROP_LIST}"}}},
    },
    "get_mandi_prices": {
        "fn": t_get_mandi_prices,
        "description": "Latest live mandi (Agmarknet) prices in INR per quintal for a crop, per market, plus a price band. "
                       "Use for any price/rate question.",
        "input_schema": {"type": "object", "required": ["crop"], "properties": {
            "crop": {"type": "string", "description": f"One of: {CROP_LIST}"},
            "state": {"type": "string", "description": "State name; defaults to the farmer's saved location."},
            "district": {"type": "string"}}},
    },
    "compare_mandis": {
        "fn": t_compare_mandis,
        "description": "Compare nearby mandis by NET income after transport, fees and spoilage (uses live prices, road routes and "
                       "forecast temperatures). Use when the farmer asks where or when to sell, or which market is best.",
        "input_schema": {"type": "object", "required": ["crop", "quantity_quintal"], "properties": {
            "crop": {"type": "string", "description": f"One of: {CROP_LIST}"},
            "quantity_quintal": {"type": "number", "description": "Quantity to sell, in quintals (1 quintal = 100 kg)."},
            "origin_place": {"type": "string", "description": "Where the produce is. Omit to use saved location."},
            "vehicle": {"type": "string", "enum": ["open", "covered", "reefer"]},
            "cost_per_km": {"type": "number", "description": "Transport cost in INR per km, if the farmer says."},
            "commission_pct": {"type": "number"}, "handling_per_quintal": {"type": "number"}}},
    },
    "estimate_profit": {
        "fn": t_estimate_profit,
        "description": "Predict yield and profit for a crop from the farmer's inputs and the live mandi price. "
                       "Only fields the farmer gave are needed; missing ones fall back to the recommended dose or crop defaults "
                       "(listed in inputs_defaulted - mention that).",
        "input_schema": {"type": "object", "required": ["crop", "area_acres"], "properties": {
            "crop": {"type": "string", "description": f"One of: {CROP_LIST}"},
            "area_acres": {"type": "number"},
            "state": {"type": "string"},
            "irrigation": {"type": "string", "enum": ["rainfed", "canal", "tubewell", "drip"]},
            "soil_fertility": {"type": "string", "enum": ["low", "medium", "high"]},
            "seed_quality": {"type": "string", "enum": ["farmer_saved", "certified", "hybrid"]},
            "fertilizer_n_kg_acre": {"type": "number"}, "fertilizer_p2o5_kg_acre": {"type": "number"},
            "fertilizer_k2o_kg_acre": {"type": "number"}, "manure_t_acre": {"type": "number"},
            "labour_days_acre": {"type": "number"}, "wage_per_day": {"type": "number"},
            "machinery_cost_acre": {"type": "number"}, "irrigation_cost_acre": {"type": "number"},
            "land_rent_acre": {"type": "number"}, "pesticide_cost_acre": {"type": "number"},
            "price_per_q": {"type": "number", "description": "Farmer's own expected price (INR/quintal); omit to use live price."},
            "transport_per_q": {"type": "number"}, "sowing_delay_days": {"type": "integer"}}},
    },
    "search_advisory": {
        "fn": t_search_advisory,
        "description": "Search the farming advisory notes (weather protection, fertiliser, pests/diseases, post-harvest, irrigation). "
                       "Use for how-to and 'what should I do' questions. Query in English. Never invent pesticide names or doses.",
        "input_schema": {"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string", "description": "Short English search query, e.g. 'frost protection vegetables'."}}},
    },
}


def tool_specs() -> list[dict[str, Any]]:
    return [{"name": n, "description": t["description"], "input_schema": t["input_schema"]} for n, t in TOOLS.items()]


def run_tool(name: str, args: dict[str, Any], ctx: ToolContext) -> tuple[dict[str, Any], bool]:
    """Execute a tool. Returns (result, ok). Never raises."""
    spec = TOOLS.get(name)
    if not spec:
        return {"error": "unknown_tool", "available": sorted(TOOLS)}, False
    fn: Callable[[dict[str, Any], ToolContext], dict[str, Any]] = spec["fn"]
    try:
        result = fn(args or {}, ctx)
        ctx.metas += [m for m in (result.get("sources") or []) if isinstance(m, dict)]
        return result, True
    except UnknownCrop as exc:
        return {"error": "unknown_crop", "given": exc.name, "supported_crops": exc.supported,
                "hint": "Ask the farmer which of the supported crops they mean; do not guess."}, False
    except MissingInput as exc:
        return {"error": "missing_input", "field": exc.field, "ask_farmer_en": exc.hint_en,
                "ask_farmer_hi": exc.hint_hi}, False
    except DataUnavailable as exc:
        return {"error": "data_unavailable", "source": exc.source, "detail": exc.detail,
                "hint": "Tell the farmer this live source is unavailable; do not estimate the missing numbers."}, False
    except (ValidationError, KeyError, TypeError, ValueError) as exc:
        return {"error": "invalid_arguments", "detail": str(exc)[:300],
                "hint": "Fix the arguments and call the tool again, or ask the farmer for the missing value."}, False
    except Exception as exc:  # pragma: no cover - last-resort guard so the agent loop survives
        log.exception("tool %s crashed", name)
        return {"error": "internal_error", "detail": f"{type(exc).__name__}: {exc}"}, False


def result_for_llm(result: dict[str, Any], limit: int = 9000) -> str:
    """JSON text for the model: drops internal blobs and caps the size."""
    clean = {k: v for k, v in result.items() if not k.startswith("_")}
    text = json.dumps(clean, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + '..."truncated"'
