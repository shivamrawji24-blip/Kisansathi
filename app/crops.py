"""Crop parameter table used by the profit predictor and the shelf-life model.

IMPORTANT - these are *model parameters*, not live data. They are rounded,
general-agronomy starting estimates (potential yield under good management,
recommended nutrient dose, storage behaviour). Before you submit or deploy,
replace/verify them with figures from ICAR / your state agriculture university /
NHB publications and cite them in the README. Live data (weather, prices,
routes) never comes from this file.

Units: yield in quintal (q = 100 kg) per acre; nutrients in kg per acre;
money in INR.
"""
from __future__ import annotations

from typing import Any

CROPS: dict[str, dict[str, Any]] = {
    "wheat": dict(
        en="Wheat", hi="गेहूं", category="grain",
        aliases=["gehu", "gehun", "गेहूँ", "गेहूं", "गेंहू"],
        agmarknet=["Wheat"],
        potential_yield=20.0, rec=dict(n=50, p=24, k=16), seed_cost=2000,
        water_sensitivity=0.5, late_sowing_loss=0.010, hybrid_gain=0.0,
        heat_c=35, cold_c=2, rain_sensitive=True, post_harvest_loss=3.0,
        shelf=dict(hours=4000, q10=2.0),
    ),
    "rice": dict(
        en="Rice (Paddy)", hi="धान", category="grain",
        aliases=["paddy", "dhan", "chawal", "धान", "चावल"],
        agmarknet=["Paddy(Dhan)(Common)", "Rice"],
        potential_yield=25.0, rec=dict(n=50, p=24, k=24), seed_cost=1000,
        water_sensitivity=0.7, late_sowing_loss=0.005, hybrid_gain=0.06,
        heat_c=36, cold_c=15, rain_sensitive=True, post_harvest_loss=4.0,
        shelf=dict(hours=4000, q10=2.0),
    ),
    "maize": dict(
        en="Maize", hi="मक्का", category="grain",
        aliases=["makka", "corn", "मक्का", "मकई"],
        agmarknet=["Maize"],
        potential_yield=24.0, rec=dict(n=60, p=24, k=16), seed_cost=2400,
        water_sensitivity=0.6, late_sowing_loss=0.008, hybrid_gain=0.08,
        heat_c=35, cold_c=8, rain_sensitive=True, post_harvest_loss=4.0,
        shelf=dict(hours=3000, q10=2.0),
    ),
    "mustard": dict(
        en="Mustard", hi="सरसों", category="oilseed",
        aliases=["sarson", "rapeseed", "सरसों", "सरसो"],
        agmarknet=["Mustard", "Mustard Seed"],
        potential_yield=8.0, rec=dict(n=32, p=16, k=8), seed_cost=300,
        water_sensitivity=0.4, late_sowing_loss=0.010, hybrid_gain=0.0,
        heat_c=33, cold_c=2, rain_sensitive=True, post_harvest_loss=3.0,
        shelf=dict(hours=4000, q10=2.0),
    ),
    "soybean": dict(
        en="Soybean", hi="सोयाबीन", category="oilseed",
        aliases=["soyabean", "soya", "सोयाबीन"],
        agmarknet=["Soyabean", "Soybean"],
        potential_yield=6.0, rec=dict(n=8, p=24, k=16), seed_cost=2000,
        water_sensitivity=0.5, late_sowing_loss=0.008, hybrid_gain=0.0,
        heat_c=35, cold_c=8, rain_sensitive=True, post_harvest_loss=4.0,
        shelf=dict(hours=3000, q10=2.0),
    ),
    "chickpea": dict(
        en="Chickpea (Chana)", hi="चना", category="pulse",
        aliases=["chana", "gram", "bengal gram", "चना"],
        agmarknet=["Bengal Gram(Gram)(Whole)", "Gram Raw(Chholia)"],
        potential_yield=6.0, rec=dict(n=8, p=16, k=8), seed_cost=2500,
        water_sensitivity=0.3, late_sowing_loss=0.010, hybrid_gain=0.0,
        heat_c=33, cold_c=2, rain_sensitive=True, post_harvest_loss=3.0,
        shelf=dict(hours=4000, q10=2.0),
    ),
    "cotton": dict(
        en="Cotton (Kapas)", hi="कपास", category="fibre",
        aliases=["kapas", "कपास"],
        agmarknet=["Cotton"],
        potential_yield=8.0, rec=dict(n=48, p=24, k=24), seed_cost=1800,
        water_sensitivity=0.6, late_sowing_loss=0.008, hybrid_gain=0.05,
        heat_c=40, cold_c=12, rain_sensitive=True, post_harvest_loss=3.0,
        shelf=dict(hours=3000, q10=2.0),
    ),
    "tomato": dict(
        en="Tomato", hi="टमाटर", category="vegetable",
        aliases=["tamatar", "टमाटर"],
        agmarknet=["Tomato"],
        potential_yield=120.0, rec=dict(n=60, p=32, k=32), seed_cost=4000,
        water_sensitivity=0.8, late_sowing_loss=0.007, hybrid_gain=0.10,
        heat_c=35, cold_c=8, rain_sensitive=True, post_harvest_loss=12.0,
        shelf=dict(hours=120, q10=2.5),
    ),
    "potato": dict(
        en="Potato", hi="आलू", category="vegetable",
        aliases=["aloo", "alu", "आलू"],
        agmarknet=["Potato"],
        potential_yield=100.0, rec=dict(n=60, p=32, k=40), seed_cost=20000,
        water_sensitivity=0.8, late_sowing_loss=0.010, hybrid_gain=0.0,
        heat_c=32, cold_c=2, rain_sensitive=True, post_harvest_loss=6.0,
        shelf=dict(hours=900, q10=2.0),
    ),
    "onion": dict(
        en="Onion", hi="प्याज", category="vegetable",
        aliases=["pyaz", "pyaaz", "प्याज", "प्याज़"],
        agmarknet=["Onion"],
        potential_yield=100.0, rec=dict(n=40, p=20, k=20), seed_cost=5000,
        water_sensitivity=0.8, late_sowing_loss=0.007, hybrid_gain=0.0,
        heat_c=38, cold_c=0, rain_sensitive=True, post_harvest_loss=8.0,
        shelf=dict(hours=1200, q10=2.0),
    ),
    "banana": dict(
        en="Banana", hi="केला", category="fruit",
        aliases=["kela", "केला"],
        agmarknet=["Banana"],
        potential_yield=200.0, rec=dict(n=120, p=40, k=160), seed_cost=18000,
        water_sensitivity=0.9, late_sowing_loss=0.005, hybrid_gain=0.0,
        heat_c=38, cold_c=12, rain_sensitive=False, post_harvest_loss=10.0,
        shelf=dict(hours=220, q10=2.5),
    ),
    "spinach": dict(
        en="Spinach (Palak)", hi="पालक", category="leafy",
        aliases=["palak", "पालक"],
        agmarknet=["Spinach"],
        potential_yield=40.0, rec=dict(n=32, p=16, k=16), seed_cost=1000,
        water_sensitivity=0.7, late_sowing_loss=0.007, hybrid_gain=0.0,
        heat_c=32, cold_c=-2, rain_sensitive=True, post_harvest_loss=15.0,
        shelf=dict(hours=55, q10=2.8),
    ),
}

for _cid, _c in CROPS.items():
    _c["id"] = _cid

# Reference temperature / quality threshold the shelf-life hours refer to:
# "hours until quality falls to 70 % when kept at 20 C".
SHELF_REF_TEMP_C = 20.0
SHELF_REF_QUALITY = 0.70


class UnknownCrop(ValueError):
    def __init__(self, name: str):
        self.name = name
        self.supported = sorted(CROPS)
        super().__init__(f"Unknown crop: {name!r}")


def resolve_crop(name: str) -> dict[str, Any]:
    """Match an id, English name, Hindi name or alias. Never guesses."""
    if not name or not str(name).strip():
        raise UnknownCrop(str(name))
    needle = str(name).strip().lower()
    for crop in CROPS.values():
        names = {crop["id"], crop["en"].lower(), crop["hi"], *[a.lower() for a in crop["aliases"]]}
        # "Rice (Paddy)" should also answer to "rice" and "paddy"
        names.update(p.strip(" ()").lower() for p in crop["en"].replace("(", " ").split())
        if needle in names:
            return crop
    raise UnknownCrop(name)


def find_crop_in_text(text: str) -> dict[str, Any] | None:
    """Find the first crop mentioned in free text (English or Hindi)."""
    low = text.lower()
    best: tuple[int, dict[str, Any]] | None = None
    for crop in CROPS.values():
        terms = {crop["id"], crop["hi"], *[a.lower() for a in crop["aliases"]]}
        for term in terms:
            idx = low.find(term)
            if idx >= 0 and (best is None or idx < best[0]):
                best = (idx, crop)
    return best[1] if best else None


def public_crop(crop: dict[str, Any]) -> dict[str, Any]:
    """Subset of the crop table that is safe/useful to send to the browser."""
    return {
        "id": crop["id"], "en": crop["en"], "hi": crop["hi"], "category": crop["category"],
        "potential_yield": crop["potential_yield"], "rec": crop["rec"],
        "seed_cost": crop["seed_cost"], "post_harvest_loss": crop["post_harvest_loss"],
        "shelf_hours_at_20c": crop["shelf"]["hours"],
    }
