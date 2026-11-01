"""Profit predictor: yield model x live price band x farmer's own costs.

Yield model (all factors multiply the crop's *potential* yield, which assumes the
recommended nutrient dose, adequate water, good seed, on-time sowing and crop
protection):

  * nutrients   - diminishing-returns (Mitscherlich-type) response per nutrient,
                  starting from what the soil already supplies; a plateau around
                  the recommended dose and a mild penalty for heavy over-use.
                  Combined as N^0.5 * P^0.25 * K^0.25. Farmyard manure adds a
                  small first-season nutrient credit.
  * irrigation  - crop water-sensitivity x irrigation reliability
  * seed        - farmer-saved / certified / hybrid
  * timing      - yield lost per day of late sowing
  * protection  - none / basic / IPM

Uncertainty: lognormal spread (P10/P50/P90) that is wider for rain-fed fields and
when the live forecast shows weather alerts. All coefficients are explicit,
editable assumptions listed in the response - this is a decision aid, not a
guarantee.
"""
from __future__ import annotations

import math
from typing import Any

from .crops import resolve_crop
from .fmt import inr
from .schemas import ProfitInput

# soil supply: fraction of potential yield obtainable with zero fertiliser (N, P, K)
SOIL_SUPPLY = {"low": (0.45, 0.60, 0.65), "medium": (0.55, 0.70, 0.75), "high": (0.65, 0.80, 0.85)}
NUTRIENT_WEIGHTS = (0.5, 0.25, 0.25)
RESPONSE_K = 2.0  # curvature of the diminishing-returns curve
OVERUSE_START = 1.5  # x recommended dose beyond which yield starts to fall
OVERUSE_PENALTY = 0.12  # yield fraction lost per +100 % over OVERUSE_START
MANURE_CREDIT_KG_PER_T = (1.5, 1.0, 2.5)  # available N, P2O5, K2O per tonne FYM (first season)
FERT_RATE_INR_PER_KG = (14.0, 50.0, 45.0)  # assumed N, P2O5, K2O price; override with actual spend
IRRIGATION_EFF = {"rainfed": 0.65, "canal": 0.90, "tubewell": 0.95, "drip": 1.0}
SEED_FACTOR = {"farmer_saved": 0.92, "certified": 1.0}  # 'hybrid' -> 1 + crop.hybrid_gain
PEST_FACTOR = {"none": 0.85, "basic": 0.95, "ipm": 1.0}
Z90 = 1.2816  # normal quantile for P10/P90


def nutrient_response(applied: float, recommended: float, soil_supply: float) -> float:
    """Relative yield (0-1) from one nutrient."""
    if recommended <= 0:
        return 1.0
    x = max(0.0, applied) / recommended
    if x <= 1.0:
        return soil_supply + (1 - soil_supply) * (1 - math.exp(-RESPONSE_K * x)) / (1 - math.exp(-RESPONSE_K))
    return max(0.6, 1.0 - OVERUSE_PENALTY * max(0.0, x - OVERUSE_START))


def _yield_factors(inp: ProfitInput, crop: dict[str, Any], n: float, p: float, k: float) -> dict[str, float]:
    rec = crop["rec"]
    sup = SOIL_SUPPLY[inp.soil_fertility]
    credit = [inp.manure_t_acre * c for c in MANURE_CREDIT_KG_PER_T]
    fn = nutrient_response(n + credit[0], rec["n"], sup[0])
    fp = nutrient_response(p + credit[1], rec["p"], sup[1])
    fk = nutrient_response(k + credit[2], rec["k"], sup[2])
    nutrient = fn ** NUTRIENT_WEIGHTS[0] * fp ** NUTRIENT_WEIGHTS[1] * fk ** NUTRIENT_WEIGHTS[2]
    irrigation = 1 - crop["water_sensitivity"] * (1 - IRRIGATION_EFF[inp.irrigation])
    seed = (1 + crop["hybrid_gain"]) if inp.seed_quality == "hybrid" else SEED_FACTOR[inp.seed_quality]
    timing = max(0.6, 1 - crop["late_sowing_loss"] * inp.sowing_delay_days)
    pest = PEST_FACTOR[inp.pest_management]
    return {"n": fn, "p": fp, "k": fk, "nutrient": nutrient, "irrigation": irrigation, "seed": seed,
            "timing": timing, "protection": pest,
            "total": nutrient * irrigation * seed * timing * pest}


def _profit(y_acre: float, price: float, area: float, loss_pct: float, fixed_total: float,
            commission_pct: float, transport_per_q: float) -> dict[str, float]:
    q = y_acre * (1 - loss_pct / 100.0) * area
    gross = q * price
    commission = gross * commission_pct / 100.0
    transport = q * transport_per_q
    return {"quintals": q, "gross": gross, "commission": commission, "transport": transport,
            "profit": gross - commission - transport - fixed_total}


def compute_profit(inp: ProfitInput, price: dict[str, Any] | None = None,
                   weather_alerts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Pure function: all external data (price band, alerts) is passed in."""
    crop = resolve_crop(inp.crop)
    rec = crop["rec"]
    alerts = weather_alerts or []
    defaulted: list[str] = []

    def pick(value: float | None, default: float, label: str) -> float:
        if value is None:
            defaulted.append(label)
            return default
        return value

    n = pick(inp.fertilizer_n_kg_acre, rec["n"], "fertilizer_n")
    p = pick(inp.fertilizer_p2o5_kg_acre, rec["p"], "fertilizer_p2o5")
    k = pick(inp.fertilizer_k2o_kg_acre, rec["k"], "fertilizer_k2o")
    seed_cost = pick(inp.seed_cost_acre, crop["seed_cost"], "seed_cost")
    loss_pct = pick(inp.post_harvest_loss_pct, crop["post_harvest_loss"], "post_harvest_loss")

    f = _yield_factors(inp, crop, n, p, k)
    potential = crop["potential_yield"]
    y50 = potential * f["total"]
    sigma = 0.10 + (0.08 if inp.irrigation == "rainfed" else 0.0) + 0.03 * min(3, len(alerts))
    y10, y90 = y50 * math.exp(-Z90 * sigma), y50 * math.exp(Z90 * sigma)

    fert_cost_estimated = inp.fertilizer_cost_acre is None
    fert_cost = (n * FERT_RATE_INR_PER_KG[0] + p * FERT_RATE_INR_PER_KG[1] + k * FERT_RATE_INR_PER_KG[2]
                 if fert_cost_estimated else inp.fertilizer_cost_acre)
    labour = inp.labour_days_acre * inp.wage_per_day
    cost_acre = {
        "seed": seed_cost, "fertilizer": fert_cost, "pesticide": inp.pesticide_cost_acre, "labour": labour,
        "machinery": inp.machinery_cost_acre, "irrigation": inp.irrigation_cost_acre,
        "land_rent": inp.land_rent_acre, "other": inp.other_cost_acre,
    }
    fixed_acre = sum(cost_acre.values())
    area = inp.area_acres
    fixed_total = fixed_acre * area

    # ---- price band
    if inp.price_per_q is not None:
        band = {"low": inp.price_per_q * 0.85, "mid": inp.price_per_q, "high": inp.price_per_q * 1.15,
                "source": "farmer", "note": "±15% band around your price"}
    elif price and price.get("band"):
        b = price["band"]
        band = {"low": b["low"], "mid": b["mid"], "high": b["high"], "source": "agmarknet",
                "as_of": b.get("as_of"), "n_markets": b.get("n_markets")}
    else:
        band = None

    result: dict[str, Any] = {
        "crop": crop["id"], "area_acres": area,
        "yield": {
            "potential_per_acre": potential,
            "per_acre": {"p10": y10, "p50": y50, "p90": y90},
            "total_q": {"p10": y10 * area, "p50": y50 * area, "p90": y90 * area},
            "post_harvest_loss_pct": loss_pct, "factors": f, "sigma": sigma,
        },
        "nutrients": [
            {"nutrient": nm, "applied": a, "recommended": r, "ratio": (a / r if r else None), "response": resp,
             "status": "low" if a < 0.75 * r else ("high" if a > 1.25 * r else "ok")}
            for nm, a, r, resp in (("N", n, rec["n"], f["n"]), ("P2O5", p, rec["p"], f["p"]), ("K2O", k, rec["k"], f["k"]))
        ],
        "costs": {
            "per_acre": cost_acre, "fixed_per_acre": fixed_acre, "fixed_total": fixed_total,
            "fertilizer_estimated": fert_cost_estimated,
        },
        "price": band,
        "needs_price": band is None,
        "inputs_defaulted": defaulted,
    }
    advice: list[dict[str, str]] = []

    if band is not None:
        grid = []
        for ylab, y in (("p10", y10), ("p50", y50), ("p90", y90)):
            row = {"yield": ylab}
            for plab, pr in (("low", band["low"]), ("mid", band["mid"]), ("high", band["high"])):
                row[plab] = _profit(y, pr, area, loss_pct, fixed_total, inp.commission_pct, inp.transport_per_q)["profit"]
            grid.append(row)
        exp = _profit(y50, band["mid"], area, loss_pct, fixed_total, inp.commission_pct, inp.transport_per_q)
        cautious = _profit(y10, band["low"], area, loss_pct, fixed_total, inp.commission_pct, inp.transport_per_q)
        optimistic = _profit(y90, band["high"], area, loss_pct, fixed_total, inp.commission_pct, inp.transport_per_q)
        total_cost = fixed_total + exp["commission"] + exp["transport"]
        q_exp = exp["quintals"]
        denom = 1 - inp.commission_pct / 100.0
        breakeven_price = ((fixed_total / q_exp) + inp.transport_per_q) / denom if q_exp > 0 and denom > 0 else None
        net_price = band["mid"] * denom - inp.transport_per_q
        breakeven_yield = ((fixed_acre / net_price) / (1 - loss_pct / 100.0)) if net_price > 0 else None
        # what would the recommended dose have given? (is the farmer under/over-spending?)
        fr = _yield_factors(inp, crop, rec["n"], rec["p"], rec["k"])
        y_rec = potential * fr["total"]
        rec_fert_cost = rec["n"] * FERT_RATE_INR_PER_KG[0] + rec["p"] * FERT_RATE_INR_PER_KG[1] + rec["k"] * FERT_RATE_INR_PER_KG[2]
        rec_fixed_total = (fixed_acre - fert_cost + rec_fert_cost) * area
        at_rec = _profit(y_rec, band["mid"], area, loss_pct, rec_fixed_total, inp.commission_pct, inp.transport_per_q)
        result.update({
            "revenue": {"cautious": cautious["gross"], "expected": exp["gross"], "optimistic": optimistic["gross"]},
            "profit": {"cautious": cautious["profit"], "expected": exp["profit"], "optimistic": optimistic["profit"],
                       "per_acre_expected": exp["profit"] / area},
            "marketable_q": {"cautious": cautious["quintals"], "expected": exp["quintals"], "optimistic": optimistic["quintals"]},
            "total_cost": total_cost,
            "roi_pct": (exp["profit"] / total_cost * 100.0) if total_cost > 0 else None,
            "breakeven_price": breakeven_price, "breakeven_yield_per_acre": breakeven_yield,
            "scenario_grid": grid,
            "vs_recommended_dose": {"profit_at_recommended": at_rec["profit"], "profit_as_entered": exp["profit"],
                                    "difference": exp["profit"] - at_rec["profit"]},
        })
        if inp.msp_per_q:
            msp = _profit(y50, inp.msp_per_q, area, loss_pct, fixed_total, inp.commission_pct, inp.transport_per_q)
            result["msp_scenario"] = {"price": inp.msp_per_q, "profit": msp["profit"]}
            advice.append({"severity": "info",
                           "en": f"At the MSP you entered ({inr(inp.msp_per_q)}/q) expected profit would be {inr(msp['profit'])}, versus {inr(exp['profit'])} at the current market price.",
                           "hi": f"आपके दिए MSP ({inr(inp.msp_per_q)}/क्विंटल) पर अनुमानित मुनाफा {inr(msp['profit'])} होगा, जबकि मौजूदा बाज़ार भाव पर {inr(exp['profit'])}।"})
        _price_advice(advice, result, band, exp["profit"], breakeven_price, cautious["profit"])
    else:
        advice.append({"severity": "warn",
                       "en": "Live mandi price is not available right now. Enter your expected price (₹ per quintal) to get the profit estimate.",
                       "hi": "अभी लाइव मंडी भाव उपलब्ध नहीं है। मुनाफे का अनुमान पाने के लिए अपना अपेक्षित भाव (₹ प्रति क्विंटल) डालिए।"})

    _agronomy_advice(advice, inp, crop, f, result["nutrients"], alerts, fert_cost_estimated, result)
    result["advice"] = advice
    result["weather_alerts"] = alerts
    result["assumptions"] = _assumptions(crop, fert_cost_estimated, sigma)
    return result


def _price_advice(advice, result, band, exp_profit, breakeven_price, cautious_profit):
    src_en = "farmer-entered price" if band["source"] == "farmer" else f"live mandi prices ({band.get('n_markets', '?')} markets, as of {band.get('as_of')})"
    src_hi = "आपका दिया भाव" if band["source"] == "farmer" else f"लाइव मंडी भाव ({band.get('n_markets', '?')} मंडियाँ, {band.get('as_of')} तक)"
    if exp_profit < 0:
        advice.append({"severity": "bad",
                       "en": f"Expected loss of {inr(-exp_profit)} using {src_en}. You need about {inr(breakeven_price)}/q to break even — consider storage, a better market, or cutting costs.",
                       "hi": f"{src_hi} के आधार पर अनुमानित घाटा {inr(-exp_profit)}। बराबरी के लिए लगभग {inr(breakeven_price)}/क्विंटल चाहिए — भंडारण, बेहतर मंडी या लागत घटाने पर विचार करें।"})
    else:
        advice.append({"severity": "good",
                       "en": f"Expected profit {inr(exp_profit)} using {src_en}; break-even price is about {inr(breakeven_price)}/q.",
                       "hi": f"{src_hi} के आधार पर अनुमानित मुनाफा {inr(exp_profit)}; बराबरी का भाव लगभग {inr(breakeven_price)}/क्विंटल।"})
        if cautious_profit < 0:
            advice.append({"severity": "warn",
                           "en": f"In the cautious case (low yield and low price) you could lose {inr(-cautious_profit)} — see the scenario table.",
                           "hi": f"सतर्क स्थिति (कम उपज और कम भाव) में {inr(-cautious_profit)} का घाटा हो सकता है — परिदृश्य तालिका देखें।"})


def _agronomy_advice(advice, inp, crop, f, nutrients, alerts, fert_est, result):
    for nu in nutrients:
        if nu["status"] == "low":
            loss = (1 - nu["response"]) * 100
            advice.append({"severity": "warn",
                           "en": f"{nu['nutrient']} applied ({nu['applied']:g} kg/acre) is well below the recommended {nu['recommended']:g}; this alone may cost roughly {loss:.0f}% of yield. Get a soil test before changing the dose.",
                           "hi": f"{nu['nutrient']} ({nu['applied']:g} किग्रा/एकड़) अनुशंसित {nu['recommended']:g} से काफी कम है; इससे अकेले लगभग {loss:.0f}% उपज घट सकती है। मात्रा बदलने से पहले मिट्टी की जाँच कराइए।"})
        elif nu["status"] == "high":
            advice.append({"severity": "warn",
                           "en": f"{nu['nutrient']} applied ({nu['applied']:g} kg/acre) is more than 25% above the recommended {nu['recommended']:g}: extra cost with little or no extra yield. Split doses and test the soil.",
                           "hi": f"{nu['nutrient']} ({nu['applied']:g} किग्रा/एकड़) अनुशंसित {nu['recommended']:g} से 25% से ज़्यादा है: खर्च बढ़ता है, उपज लगभग नहीं बढ़ती। मात्रा बाँटकर दीजिए और मिट्टी की जाँच कराइए।"})
    vr = result.get("vs_recommended_dose")
    if vr and abs(vr["difference"]) > max(2000.0, 0.03 * abs(vr["profit_as_entered"])) and any(n["status"] != "ok" for n in nutrients):
        better = vr["difference"] < 0
        advice.append({"severity": "info",
                       "en": f"With the recommended fertiliser dose the model gives {inr(vr['profit_at_recommended'])} profit ({'higher' if better else 'lower'} than your {inr(vr['profit_as_entered'])}).",
                       "hi": f"अनुशंसित खाद मात्रा पर मॉडल {inr(vr['profit_at_recommended'])} मुनाफा देता है (आपके {inr(vr['profit_as_entered'])} से {'ज़्यादा' if better else 'कम'})।"})
    if inp.irrigation == "rainfed" and crop["water_sensitivity"] >= 0.5:
        advice.append({"severity": "warn",
                       "en": f"{crop['en']} is fairly water-sensitive; rain-fed fields carry a wider yield range. One life-saving irrigation at the critical stage, or mulching, is usually worth the cost.",
                       "hi": f"{crop['hi']} पानी के प्रति संवेदनशील है; वर्षा-आधारित खेत में उपज का उतार-चढ़ाव ज़्यादा रहता है। क्रांतिक अवस्था पर एक जीवन-रक्षक सिंचाई या मल्चिंग आमतौर पर फायदेमंद है।"})
    if inp.sowing_delay_days >= 7:
        loss = (1 - f["timing"]) * 100
        advice.append({"severity": "warn",
                       "en": f"Sowing {inp.sowing_delay_days} days late costs roughly {loss:.0f}% yield. Use a short-duration/timely-sown variety if you can.",
                       "hi": f"{inp.sowing_delay_days} दिन देर से बुवाई से लगभग {loss:.0f}% उपज घटती है। हो सके तो कम अवधि की/समय पर बोई जाने वाली किस्म लें।"})
    if inp.pest_management == "none":
        advice.append({"severity": "warn",
                       "en": "No crop protection assumed: the model cuts yield by about 15%. Scout weekly and ask your local KVK for need-based advice.",
                       "hi": "फसल सुरक्षा नहीं मानी गई: मॉडल उपज लगभग 15% घटाता है। साप्ताहिक निरीक्षण कीजिए और अपने KVK से ज़रूरत-आधारित सलाह लीजिए।"})
    if inp.labour_days_acre * inp.wage_per_day + inp.machinery_cost_acre + inp.irrigation_cost_acre + inp.land_rent_acre == 0:
        advice.append({"severity": "warn",
                       "en": "Labour, machinery, irrigation and rent are all zero, so profit may be overstated. Enter them for a realistic number.",
                       "hi": "मज़दूरी, मशीनरी, सिंचाई और किराया सब शून्य हैं, इसलिए मुनाफा ज़्यादा दिख सकता है। सही आँकड़े के लिए इन्हें भरिए।"})
    for a in alerts[:3]:
        advice.append({"severity": "warn", "en": a["en"], "hi": a["hi"]})


def _assumptions(crop, fert_est, sigma):
    out = [
        {"en": f"Potential yield for {crop['en']} is assumed {crop['potential_yield']:g} q/acre at the recommended dose with good management (approximate; verify with ICAR/KVK).",
         "hi": f"{crop['hi']} की संभावित उपज अनुशंसित मात्रा और अच्छे प्रबंधन पर {crop['potential_yield']:g} क्विंटल/एकड़ मानी गई है (अनुमानित; ICAR/KVK से जाँचें)।"},
        {"en": f"Yield range (P10-P90) uses a spread of ±{sigma * Z90 * 100:.0f}%; it widens for rain-fed fields and weather alerts.",
         "hi": f"उपज सीमा (P10-P90) में ±{sigma * Z90 * 100:.0f}% का फैलाव है; वर्षा-आधारित खेत और मौसम चेतावनी में यह बढ़ता है।"},
        {"en": "Price band = median min/modal/max across nearby live mandi reports (or ±15% around your own price).",
         "hi": "भाव सीमा = आस-पास की लाइव मंडी रिपोर्टों का माध्य न्यूनतम/मोडल/अधिकतम (या आपके भाव के ±15%)।"},
    ]
    if fert_est:
        n, p, k = FERT_RATE_INR_PER_KG
        out.append({"en": f"Fertiliser cost is estimated at ₹{n:g}/kg N, ₹{p:g}/kg P2O5, ₹{k:g}/kg K2O — enter your actual spend to override.",
                    "hi": f"खाद की लागत ₹{n:g}/किग्रा N, ₹{p:g}/किग्रा P2O5, ₹{k:g}/किग्रा K2O मानकर निकाली गई है — असली खर्च डालकर बदलिए।"})
    return out
