"""Shelf-life (spoilage) model: how much sellable quality is left after harvest,
transport and waiting, given the *real forecast temperatures* on the way.

Model
-----
Quality decays exponentially:  q(t) = exp(-D),  dD/dt = k(T)
with an Arrhenius-style Q10 temperature dependence:
    k(T) = k_ref * Q10 ** ((T - T_ref) / 10)
and k_ref chosen so that quality reaches 70 % after ``shelf.hours`` hours at 20 C.
Rule of thumb built in: spoilage speeds up about 2-3x for every +10 C.

Quality maps to a price multiplier (grade discount); below 60 % the lot is
treated as unsellable.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Callable

from .crops import SHELF_REF_QUALITY, SHELF_REF_TEMP_C

# Temperature inside the vehicle relative to outside air (assumptions, editable).
VEHICLES: dict[str, dict[str, float]] = {
    "open": {"offset_c": 0.0},      # open truck / tempo, ventilated
    "covered": {"offset_c": 4.0},   # closed body, heat builds up
    "reefer": {"fixed_c": 10.0},    # refrigerated, set-point
}

MIN_SELLABLE_QUALITY = 0.60
FULL_PRICE_QUALITY = 0.90


def decay_rate(crop: dict[str, Any], temp_c: float) -> float:
    """Damage per hour at ``temp_c``."""
    shelf = crop["shelf"]
    k_ref = -math.log(SHELF_REF_QUALITY) / shelf["hours"]
    return k_ref * shelf["q10"] ** ((temp_c - SHELF_REF_TEMP_C) / 10.0)


def price_multiplier(quality: float) -> float:
    """1.0 at >=90 % quality, falling linearly to 0.5 at 60 %, 0 (unsellable) below."""
    if quality < MIN_SELLABLE_QUALITY:
        return 0.0
    if quality >= FULL_PRICE_QUALITY:
        return 1.0
    return 0.5 + 0.5 * (quality - MIN_SELLABLE_QUALITY) / (FULL_PRICE_QUALITY - MIN_SELLABLE_QUALITY)


def simulate_quality(
    crop: dict[str, Any], *,
    depart: datetime,
    journey_h: float,
    temp_origin: Callable[[datetime], float],
    temp_dest: Callable[[datetime], float],
    hold_before_h: float = 3.0,
    wait_after_h: float = 4.0,
    vehicle: str = "open",
    reefer_c: float | None = None,
    step_h: float = 0.25,
) -> dict[str, Any]:
    """Integrate spoilage over: harvest -> loading wait (origin air) -> journey
    (temperature blends from origin to destination) -> waiting at the mandi."""
    veh = VEHICLES.get(vehicle, VEHICLES["open"])
    fixed = reefer_c if (vehicle == "reefer" and reefer_c is not None) else veh.get("fixed_c")
    offset = veh.get("offset_c", 0.0)

    damage = 0.0
    temp_hours = 0.0
    total_h = 0.0

    def run(duration: float, temp_fn: Callable[[float], float]) -> None:
        nonlocal damage, temp_hours, total_h
        steps = max(1, int(math.ceil(duration / step_h))) if duration > 0 else 0
        for i in range(steps):
            dt = duration / steps
            t = (i + 0.5) * dt
            temp = temp_fn(t)
            damage += decay_rate(crop, temp) * dt
            temp_hours += temp * dt
            total_h += dt

    # 1) waiting at origin before loading (shade, outside air)
    start = depart - timedelta(hours=hold_before_h)
    run(hold_before_h, lambda t: temp_origin(start + timedelta(hours=t)))

    # 2) the journey
    def journey_temp(t: float) -> float:
        when = depart + timedelta(hours=t)
        progress = t / journey_h if journey_h > 0 else 1.0
        ambient = temp_origin(when) * (1 - progress) + temp_dest(when) * progress
        return fixed if fixed is not None else ambient + offset

    run(journey_h, journey_temp)

    # 3) waiting at the destination market before the sale
    arrive = depart + timedelta(hours=journey_h)
    run(wait_after_h, lambda t: temp_dest(arrive + timedelta(hours=t)))

    quality = math.exp(-damage)
    return {
        "quality": quality,
        "price_multiplier": price_multiplier(quality),
        "sellable": quality >= MIN_SELLABLE_QUALITY,
        "total_hours": total_h,
        "avg_temp_c": (temp_hours / total_h) if total_h else None,
    }
