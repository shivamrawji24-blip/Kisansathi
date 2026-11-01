"""Deterministic fallback agent (no LLM needed).

Used when no LLM key is configured or the LLM call fails, so the demo never dies.
It detects intent with English/Hindi keywords, calls the *same* live-data tools the
LLM agent uses, and formats the tool results in the farmer's language. It never
invents numbers: anything it says comes straight from a tool result.
"""
from __future__ import annotations

import re
import time
from typing import Any

from .crops import CROPS, find_crop_in_text
from .fmt import inr
from .tools import ToolContext, run_tool

PRICE_WORDS = ("price", "rate", "bhav", "bhaav", "mandi", "भाव", "दाम", "कीमत", "रेट", "मंडी")
WEATHER_WORDS = ("weather", "forecast", "rain", "temperature", "mausam", "barish", "मौसम", "बारिश", "तापमान", "पूर्वानुमान")
SELL_WORDS = ("sell", "best market", "which market", "where to", "bech", "बेच", "बेचूं", "बेचूँ", "बेचना", "बिक्री")
PROFIT_WORDS = ("profit", "income", "earn", "loss", "munafa", "मुनाफा", "मुनाफ़ा", "लाभ", "कमाई", "फायदा", "नफा")

_QTY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(quintals?|qtl|q\b|क्विंटल|kg|किलो|ton(?:ne)?s?|टन)", re.I)
_AREA_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(acres?|एकड़|एकड|hectares?|हेक्टेयर|ha\b)", re.I)


def _has(text: str, words: tuple[str, ...]) -> bool:
    low = text.lower()
    return any(w in low for w in words)


def _quantity_q(text: str) -> float | None:
    m = _QTY_RE.search(text)
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2).lower()
    if unit.startswith(("kg", "किलो")):
        return n / 100.0
    if unit.startswith(("ton", "टन")):
        return n * 10.0
    return n


def _area_acres(text: str) -> float | None:
    m = _AREA_RE.search(text)
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2).lower()
    return n * 2.471 if unit.startswith(("hect", "हेक्टेयर", "ha")) else n


def _t(lang: str, en: str, hi: str) -> str:
    return hi if lang == "hi" else en


def _call(ctx: ToolContext, trace: list[dict[str, Any]], name: str, args: dict[str, Any]):
    t0 = time.time()
    result, ok = run_tool(name, args, ctx)
    trace.append({"tool": name, "args": args, "ok": ok, "ms": int((time.time() - t0) * 1000),
                  "error": None if ok else result.get("error"),
                  "stale": any(s.get("stale") for s in (result.get("sources") or []))})
    return result, ok


def _error_text(lang: str, r: dict[str, Any]) -> str:
    e = r.get("error")
    if e == "missing_input":
        return r["ask_farmer_hi"] if lang == "hi" else r["ask_farmer_en"]
    if e == "data_unavailable":
        return _t(lang, f"The live {r['source']} data is not available right now ({r['detail']}). I will not guess the numbers — please try again in a few minutes.",
                  f"अभी लाइव {r['source']} डेटा उपलब्ध नहीं है ({r['detail']})। मैं अंदाज़े से आँकड़े नहीं दूँगा — कुछ मिनट बाद फिर कोशिश कीजिए।")
    if e == "unknown_crop":
        names = ", ".join(CROPS[c]["hi" if lang == "hi" else "en"] for c in r["supported_crops"])
        return _t(lang, f"I don't have data for '{r['given']}'. Supported crops: {names}.",
                  f"'{r['given']}' का डेटा मेरे पास नहीं है। उपलब्ध फसलें: {names}।")
    return _t(lang, f"Sorry, that did not work ({e}). Please check the details and try again.",
              f"माफ़ कीजिए, यह नहीं हो पाया ({e})। कृपया जानकारी जाँचकर फिर कोशिश कीजिए।")


def _stale_note(lang: str, sources: list[dict[str, Any]]) -> str:
    stale = [s["source"] for s in sources if s.get("stale")]
    if not stale:
        return ""
    return "\n" + _t(lang, f"Note: saved data from an earlier fetch was used for {', '.join(stale)} because the live source did not respond.",
                     f"ध्यान दें: {', '.join(stale)} के लिए लाइव स्रोत नहीं चला, इसलिए पहले से सहेजा हुआ डेटा इस्तेमाल हुआ।")


def _crop_name(lang: str, crop_id: str) -> str:
    return CROPS[crop_id]["hi" if lang == "hi" else "en"]


def _ask_crop(lang: str) -> str:
    names = ", ".join(c["hi" if lang == "hi" else "en"] for c in CROPS.values())
    return _t(lang, f"Which crop? I can help with: {names}.", f"कौन-सी फसल? मैं इनमें मदद कर सकता हूँ: {names}।")


def run_rule_agent(message: str, lang: str, ctx: ToolContext, trace: list[dict[str, Any]]) -> str:
    crop = find_crop_in_text(message)
    qty, area = _quantity_q(message), _area_acres(message)

    # --- where to sell
    if _has(message, SELL_WORDS) and not _has(message, PROFIT_WORDS):
        if not crop:
            return _ask_crop(lang)
        if qty is None:
            return _t(lang, f"How many quintals of {crop['en']} do you want to sell? (1 quintal = 100 kg)",
                      f"कितने क्विंटल {crop['hi']} बेचना चाहते हैं? (1 क्विंटल = 100 किलो)")
        r, ok = _call(ctx, trace, "compare_mandis", {"crop": crop["id"], "quantity_quintal": qty})
        if not ok:
            return _error_text(lang, r)
        return r["explanation"] + _stale_note(lang, r["sources"])

    # --- profit
    if _has(message, PROFIT_WORDS):
        if not crop:
            return _ask_crop(lang)
        r, ok = _call(ctx, trace, "estimate_profit", {"crop": crop["id"], "area_acres": area or 1})
        if not ok:
            return _error_text(lang, r)
        if r.get("needs_price"):
            return _t(lang, "I need a price to estimate profit, and live mandi prices are not available. Tell me your expected price per quintal or use the Profit tab.",
                      "मुनाफा निकालने के लिए भाव चाहिए, और लाइव मंडी भाव अभी उपलब्ध नहीं हैं। अपना अपेक्षित भाव प्रति क्विंटल बताइए या 'मुनाफा अनुमान' टैब इस्तेमाल कीजिए।")
        p = r["profit"]
        a = area or 1
        head = _t(lang,
                  f"Quick estimate for {a:g} acre of {crop['en']} with the recommended inputs: expected profit {inr(p['expected'])} (range {inr(p['cautious'])} to {inr(p['optimistic'])}); break-even price about {inr(r['breakeven_price'])}/quintal.",
                  f"{a:g} एकड़ {crop['hi']} का अनुशंसित इनपुट पर त्वरित अनुमान: अपेक्षित मुनाफा {inr(p['expected'])} (सीमा {inr(p['cautious'])} से {inr(p['optimistic'])}); बराबरी का भाव लगभग {inr(r['breakeven_price'])}/क्विंटल।")
        tail = _t(lang, "Enter your real fertiliser, labour and other costs in the Profit tab for an accurate number.",
                  "सही आँकड़े के लिए 'मुनाफा अनुमान' टैब में अपनी असली खाद, मज़दूरी और अन्य लागत भरिए।")
        return f"{head} {tail}" + _stale_note(lang, r["sources"])

    # --- prices
    if _has(message, PRICE_WORDS):
        if not crop:
            return _ask_crop(lang)
        r, ok = _call(ctx, trace, "get_mandi_prices", {"crop": crop["id"]})
        if not ok:
            return _error_text(lang, r)
        if not r["markets"]:
            return _t(lang, f"No recent {crop['en']} prices were reported for your state.",
                      f"आपके राज्य में {crop['hi']} के हाल के भाव दर्ज नहीं हैं।")
        top = r["markets"][:3]
        lines = "; ".join(f"{m['market']} {inr(m['modal'])}" for m in top)
        b = r["band"]
        return _t(lang,
                  f"{crop['en']} modal prices (₹/quintal, as of {b['as_of']}): {lines}. Typical range across markets: {inr(b['low'])} – {inr(b['high'])}.",
                  f"{crop['hi']} के मोडल भाव (₹/क्विंटल, {b['as_of']} तक): {lines}। मंडियों में सामान्य सीमा: {inr(b['low'])} – {inr(b['high'])}।") + _stale_note(lang, r["sources"])

    # --- weather
    if _has(message, WEATHER_WORDS):
        r, ok = _call(ctx, trace, "get_weather", {"crop": crop["id"]} if crop else {})
        if not ok:
            return _error_text(lang, r)
        days = "; ".join(f"{d['date'][5:]}: {d['tmax']:.0f}/{d['tmin']:.0f}°C, {_t(lang, 'rain', 'बारिश')} {d['rain_mm']:.0f} mm" for d in r["days"][:3])
        out = _t(lang, f"Next days (max/min): {days}.", f"अगले दिन (अधिकतम/न्यूनतम): {days}।")
        if r["alerts"]:
            out += " " + r["alerts"][0][lang if lang in ("en", "hi") else "en"]
        return out + _stale_note(lang, r["sources"])

    # --- advisory (default)
    r, ok = _call(ctx, trace, "search_advisory", {"query": message})
    if not ok:
        return _error_text(lang, r)
    if not r["found"]:
        return _t(lang, "I could not find a matching advisory. Please ask your local KVK or agriculture officer. You can also ask me about weather, mandi prices, where to sell, or profit.",
                  "मुझे इससे मेल खाती सलाह नहीं मिली। कृपया अपने KVK या कृषि अधिकारी से पूछिए। आप मुझसे मौसम, मंडी भाव, कहाँ बेचें या मुनाफे के बारे में भी पूछ सकते हैं।")
    parts = [h["text"] if lang == "hi" else f"{h['title']}: {h['text']}" for h in r["hits"][:2]]
    return "\n\n".join(parts)
