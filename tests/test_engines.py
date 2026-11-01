"""Pure-logic tests: profit model, shelf-life model, retrieval, formatting, grounding check."""
from __future__ import annotations

from datetime import datetime

import pytest

from app.agent import grounding_check
from app.crops import UnknownCrop, find_crop_in_text, resolve_crop
from app.fmt import inr, merge_meta
from app.profit import compute_profit, nutrient_response
from app.rag import search_advisory
from app.schemas import ProfitInput
from app.shelf_life import decay_rate, price_multiplier, simulate_quality

BAND = {"band": {"low": 2200, "mid": 2400, "high": 2600, "n_markets": 12, "as_of": "2026-10-31"}}


def base(**kw):
    d = dict(crop="wheat", area_acres=5, labour_days_acre=8, wage_per_day=400, machinery_cost_acre=3000,
             irrigation_cost_acre=1500)
    d.update(kw)
    return ProfitInput(**d)


# ------------------------------------------------------------------ crops / formatting
def test_crop_resolution_english_hindi_alias():
    assert resolve_crop("Wheat")["id"] == "wheat"
    assert resolve_crop("गेहूं")["id"] == "wheat"
    assert resolve_crop("gehu")["id"] == "wheat"
    assert resolve_crop("paddy")["id"] == "rice"
    assert resolve_crop("Rice")["id"] == "rice"
    assert resolve_crop("टमाटर")["id"] == "tomato"


@pytest.mark.parametrize("bad", ["", "  ", "unobtainium", "dragonfruit"])
def test_unknown_crop_raises(bad):
    with pytest.raises(UnknownCrop) as e:
        resolve_crop(bad)
    assert "wheat" in e.value.supported


def test_find_crop_in_text():
    assert find_crop_in_text("mera tamatar kharab ho raha")["id"] == "tomato"
    assert find_crop_in_text("गेहूं का भाव")["id"] == "wheat"
    assert find_crop_in_text("what is the weather") is None


def test_inr_indian_grouping():
    assert inr(1234567) == "₹12,34,567"
    assert inr(100000) == "₹1,00,000"
    assert inr(999) == "₹999"
    assert inr(-4500) == "-₹4,500"
    assert inr(None) == "—"


def test_merge_meta_stale_and_oldest():
    merged = merge_meta([
        {"source": "weather", "stale": False, "fetched_at": "2026-11-01T10:00:00+00:00", "error": None},
        {"source": "weather", "stale": True, "fetched_at": "2026-11-01T08:00:00+00:00", "error": "HTTP 500"},
        {"source": "route", "stale": False, "fetched_at": None, "error": None}, None])
    w = next(m for m in merged if m["source"] == "weather")
    assert w["stale"] is True and w["fetched_at"].startswith("2026-11-01T08") and w["error"] == "HTTP 500"
    assert len(merged) == 2


# ------------------------------------------------------------------ profit model
def test_nutrient_response_shape():
    r = [nutrient_response(x * 50, 50, 0.55) for x in (0, 0.5, 1, 1.25, 1.5, 2, 3)]
    assert r[0] == pytest.approx(0.55)                      # soil supply with no fertiliser
    assert r[0] < r[1] < r[2]                               # more fertiliser -> more yield...
    assert r[1] - r[0] > r[2] - r[1]                        # ...with diminishing returns
    assert r[2] == r[3] == r[4] == pytest.approx(1.0)       # plateau
    assert r[5] < r[4] and r[6] < r[5]                      # heavy over-use hurts
    assert nutrient_response(10, 0, 0.5) == 1.0             # nutrient not needed


def test_profit_basic_arithmetic_is_consistent():
    r = compute_profit(base(), BAND)
    y = r["yield"]["per_acre"]["p50"]
    q = y * (1 - r["yield"]["post_harvest_loss_pct"] / 100) * 5
    gross = q * 2400
    assert r["revenue"]["expected"] == pytest.approx(gross)
    assert r["profit"]["expected"] == pytest.approx(gross - r["costs"]["fixed_total"])
    assert r["profit"]["cautious"] < r["profit"]["expected"] < r["profit"]["optimistic"]
    assert r["yield"]["per_acre"]["p10"] < y < r["yield"]["per_acre"]["p90"]


def test_breakeven_price_gives_zero_profit():
    r = compute_profit(base(commission_pct=2, transport_per_q=30), BAND)
    be = r["breakeven_price"]
    r2 = compute_profit(base(commission_pct=2, transport_per_q=30, price_per_q=be), None)
    assert r2["profit"]["expected"] == pytest.approx(0, abs=0.01)


def test_more_fertiliser_helps_until_plateau_then_hurts():
    p = lambda n: compute_profit(base(fertilizer_n_kg_acre=n), BAND)["yield"]["per_acre"]["p50"]
    assert p(10) < p(30) < p(50)
    assert p(50) == pytest.approx(p(60), rel=1e-6)   # plateau between 1.0x and 1.5x of the dose
    assert p(150) < p(50)


def test_every_input_factor_moves_the_yield_the_right_way():
    y = lambda **kw: compute_profit(base(**kw), BAND)["yield"]["per_acre"]["p50"]
    ref = y()
    assert y(irrigation="rainfed") < ref < y(irrigation="drip") + 1e-9
    # soil supply only matters when fertiliser is below the recommended dose (plateau otherwise)
    assert y(soil_fertility="low", fertilizer_n_kg_acre=20) < y(fertilizer_n_kg_acre=20) < y(soil_fertility="high", fertilizer_n_kg_acre=20)
    assert y(soil_fertility="low") == pytest.approx(ref)
    assert y(seed_quality="farmer_saved") < ref
    assert y(sowing_delay_days=20) < ref
    assert y(pest_management="none") < ref
    assert y(manure_t_acre=4, fertilizer_n_kg_acre=20) > y(manure_t_acre=0, fertilizer_n_kg_acre=20)
    assert y(crop="rice", seed_quality="hybrid") > y(crop="rice", seed_quality="certified")


def test_defaults_are_reported_and_fertiliser_cost_estimated():
    r = compute_profit(base(), BAND)
    assert {"fertilizer_n", "fertilizer_p2o5", "fertilizer_k2o", "seed_cost", "post_harvest_loss"} <= set(r["inputs_defaulted"])
    assert r["costs"]["fertilizer_estimated"] is True
    spent = compute_profit(base(fertilizer_cost_acre=5000), BAND)
    assert spent["costs"]["per_acre"]["fertilizer"] == 5000 and spent["costs"]["fertilizer_estimated"] is False


def test_farmer_price_overrides_market_and_no_price_asks_for_one():
    r = compute_profit(base(price_per_q=3000), BAND)
    assert r["price"]["source"] == "farmer" and r["price"]["mid"] == 3000
    none = compute_profit(base(), None)
    assert none["needs_price"] is True and "profit" not in none
    assert any("price" in a["en"].lower() for a in none["advice"])


def test_loss_scenario_gets_a_bad_advice_card():
    r = compute_profit(base(price_per_q=500), None)
    assert r["profit"]["expected"] < 0
    assert any(a["severity"] == "bad" for a in r["advice"])


def test_advice_is_bilingual_and_flags_problems():
    r = compute_profit(base(fertilizer_n_kg_acre=10, irrigation="rainfed", sowing_delay_days=15,
                            pest_management="none"), BAND)
    for a in r["advice"]:
        assert a["en"] and a["hi"]
    text = " ".join(a["en"] for a in r["advice"])
    assert "below the recommended" in text and "late" in text and "No crop protection" in text
    overuse = compute_profit(base(fertilizer_n_kg_acre=120), BAND)
    assert any("more than 25% above" in a["en"] for a in overuse["advice"])


def test_weather_alerts_widen_uncertainty():
    alerts = [{"type": "heat", "date": "2026-11-02", "value": 41, "en": "hot", "hi": "गर्म"}] * 3
    calm = compute_profit(base(), BAND)["yield"]["sigma"]
    stormy = compute_profit(base(), BAND, alerts)["yield"]["sigma"]
    assert stormy == pytest.approx(calm + 0.09)


def test_scenario_grid_is_monotonic():
    g = compute_profit(base(), BAND)["scenario_grid"]
    assert [r["yield"] for r in g] == ["p10", "p50", "p90"]
    for row in g:
        assert row["low"] < row["mid"] < row["high"]
    assert g[0]["mid"] < g[1]["mid"] < g[2]["mid"]


def test_msp_scenario():
    r = compute_profit(base(msp_per_q=2275), BAND)
    assert r["msp_scenario"]["price"] == 2275


@pytest.mark.parametrize("bad", [dict(area_acres=0), dict(area_acres=-2), dict(wage_per_day=-1),
                                 dict(irrigation="flood"), dict(commission_pct=50)])
def test_profit_input_validation(bad):
    with pytest.raises(Exception):
        base(**bad)


# ------------------------------------------------------------------ shelf-life model
def flat(t):
    return lambda when: t


def test_reference_point_matches_definition():
    import math
    tom = resolve_crop("tomato")
    assert math.exp(-decay_rate(tom, 20) * tom["shelf"]["hours"]) == pytest.approx(0.70)


def test_q10_rule_spoilage_scales_with_temperature():
    tom = resolve_crop("tomato")
    assert decay_rate(tom, 30) / decay_rate(tom, 20) == pytest.approx(tom["shelf"]["q10"])
    assert 2 <= tom["shelf"]["q10"] <= 3


def test_quality_drops_with_heat_distance_and_time():
    tom = resolve_crop("tomato")
    d = datetime(2026, 11, 1, 6)
    q = lambda T, h, **kw: simulate_quality(tom, depart=d, journey_h=h, temp_origin=flat(T), temp_dest=flat(T), **kw)["quality"]
    assert q(40, 5) < q(30, 5) < q(20, 5)
    assert q(35, 10) < q(35, 5) < q(35, 1)
    assert q(38, 5, vehicle="covered") < q(38, 5, vehicle="open")
    assert q(38, 5, vehicle="reefer") > q(38, 5, vehicle="open")


def test_perishable_vs_durable_crops():
    d = datetime(2026, 11, 1, 6)
    run = lambda c: simulate_quality(resolve_crop(c), depart=d, journey_h=10, temp_origin=flat(36),
                                     temp_dest=flat(36))["quality"]
    assert run("spinach") < run("tomato") < run("onion") <= run("wheat")


def test_price_multiplier_bands():
    assert price_multiplier(0.95) == 1.0 and price_multiplier(0.90) == 1.0
    assert price_multiplier(0.75) == pytest.approx(0.75)
    assert price_multiplier(0.60) == pytest.approx(0.5)
    assert price_multiplier(0.59) == 0.0


def test_unsellable_flag():
    sp = resolve_crop("spinach")
    s = simulate_quality(sp, depart=datetime(2026, 11, 1, 12), journey_h=24, temp_origin=flat(40),
                         temp_dest=flat(40))
    assert s["sellable"] is False and s["price_multiplier"] == 0.0


# ------------------------------------------------------------------ retrieval
@pytest.mark.parametrize("query,lang,title", [
    ("how to protect crops from heat", "en", "Heat stress and heatwaves"),
    ("frost tonight what to do", "en", "Frost and cold wave"),
    ("पाला पड़ने वाला है", "hi", "Frost and cold wave"),
    ("बारिश से पहले क्या करें", "hi", "Heavy rain and waterlogging"),
    ("how much water does drip irrigation save", "en", "Drip, sprinkler and mulch"),
    ("which market to sell tomatoes", "en", "Choosing where to sell"),
])
def test_retrieval_finds_the_right_topic(query, lang, title):
    r = search_advisory(query, lang, 3)
    assert r["found"] and r["hits"][0]["title"] == title


def test_retrieval_returns_language_and_source_and_refuses_offtopic():
    hi = search_advisory("heat stress irrigation", "hi", 1)["hits"][0]
    en = search_advisory("heat stress irrigation", "en", 1)["hits"][0]
    assert "सिंचाई" in hi["text"] and "irrigate" in en["text"].lower()
    assert en["source"] and en["file"].endswith(".md")
    assert search_advisory("quantum computing blockchain", "en")["found"] is False
    assert search_advisory("", "en")["hits"] == []


# ------------------------------------------------------------------ grounding check
def test_grounding_check():
    known = '{"net_income": 12345, "quality": 0.87, "distance_km": 42.4}  farmer: 50 quintals'
    assert grounding_check("Net ₹12,345 for 50 quintals, 42 km away", known) == []
    assert grounding_check("Net ₹99,999", known) == [99999.0]
    assert grounding_check("Rounded 12,300 is close", known) == []            # within ~1% rounding -> ok
    assert grounding_check("About 13,000", known) == [13000.0]             # >1% off -> flagged
    assert grounding_check("In 2026 take 3 steps", known) == []             # years and tiny numbers ignored


# ------------------------------------------------------------------ weather alert merging
def test_consecutive_days_merge_into_one_alert_with_range():
    from app.datasources import weather_alerts
    rows = [dict(date=f"2026-11-0{d}", tmax=t, tmin=15, rain_mm=r) for d, t, r in
            [(1, 38, 0), (2, 41, 0), (3, 39, 70), (4, 30, 20), (5, 38, 0)]]
    alerts = weather_alerts(rows, resolve_crop("tomato"))      # tomato heat limit 35 C
    heat = [a for a in alerts if a["type"] == "heat"]
    assert len(heat) == 2                                       # 1-3 Nov and 5 Nov (4 Nov is cooler)
    assert heat[0]["date"] == "2026-11-01" and heat[0]["date_end"] == "2026-11-03" and heat[0]["value"] == 41
    assert "2026-11-01 to 2026-11-03" in heat[0]["en"] and "से" in heat[0]["hi"]
    assert heat[1]["date"] == heat[1]["date_end"] == "2026-11-05" and "to" not in heat[1]["en"].split(":")[0]
    assert [a["type"] for a in alerts if "rain" in a["type"]] == ["heavy_rain", "rain"]
