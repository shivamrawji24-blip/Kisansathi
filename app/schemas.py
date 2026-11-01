"""Request models (pydantic v2). Validation errors become HTTP 422 with details."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

Lang = Literal["en", "hi"]


class Location(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: Optional[str] = None
    lat: Optional[float] = Field(None, ge=-90, le=90)
    lon: Optional[float] = Field(None, ge=-180, le=180)
    state: Optional[str] = None
    district: Optional[str] = None


class ProfitInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    crop: str
    area_acres: float = Field(gt=0, le=1000)
    state: Optional[str] = None
    district: Optional[str] = None
    lat: Optional[float] = Field(None, ge=-90, le=90)
    lon: Optional[float] = Field(None, ge=-180, le=180)

    # --- growing conditions
    soil_fertility: Literal["low", "medium", "high"] = "medium"
    irrigation: Literal["rainfed", "canal", "tubewell", "drip"] = "tubewell"
    seed_quality: Literal["farmer_saved", "certified", "hybrid"] = "certified"
    sowing_delay_days: int = Field(0, ge=0, le=60)
    pest_management: Literal["none", "basic", "ipm"] = "basic"

    # --- fertiliser (kg of nutrient per acre; None -> crop's recommended dose)
    fertilizer_n_kg_acre: Optional[float] = Field(None, ge=0, le=600)
    fertilizer_p2o5_kg_acre: Optional[float] = Field(None, ge=0, le=600)
    fertilizer_k2o_kg_acre: Optional[float] = Field(None, ge=0, le=600)
    manure_t_acre: float = Field(0, ge=0, le=40)  # farmyard manure, tonnes/acre

    # --- costs (INR per acre unless noted)
    seed_cost_acre: Optional[float] = Field(None, ge=0)
    fertilizer_cost_acre: Optional[float] = Field(None, ge=0)  # None -> estimated from nutrient kg
    pesticide_cost_acre: float = Field(0, ge=0)
    labour_days_acre: float = Field(0, ge=0)
    wage_per_day: float = Field(0, ge=0)
    machinery_cost_acre: float = Field(0, ge=0)
    irrigation_cost_acre: float = Field(0, ge=0)
    land_rent_acre: float = Field(0, ge=0)
    other_cost_acre: float = Field(0, ge=0)

    # --- selling
    price_per_q: Optional[float] = Field(None, gt=0)  # None -> live mandi price
    transport_per_q: float = Field(0, ge=0)
    commission_pct: float = Field(0, ge=0, le=15)
    post_harvest_loss_pct: Optional[float] = Field(None, ge=0, le=60)
    msp_per_q: Optional[float] = Field(None, gt=0)


class CompareInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    crop: str
    quantity_q: float = Field(gt=0, le=100000)
    origin_place: Optional[str] = None
    lat: Optional[float] = Field(None, ge=-90, le=90)
    lon: Optional[float] = Field(None, ge=-180, le=180)
    state: Optional[str] = None
    district: Optional[str] = None
    vehicle: Literal["open", "covered", "reefer"] = "open"
    cost_per_km: float = Field(20, ge=0)
    handling_per_q: float = Field(0, ge=0)
    commission_pct: float = Field(0, ge=0, le=15)
    hold_before_h: float = Field(3, ge=0, le=72)
    wait_after_h: float = Field(4, ge=0, le=72)
    max_distance_km: float = Field(300, gt=0, le=3000)
    top_n: int = Field(6, ge=1, le=10)
    markets: Optional[list[str]] = None


class ChatTurn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatInput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    message: str = Field(min_length=1, max_length=2000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)
    lang: Lang = "en"
    location: Optional[Location] = None


class OutageInput(BaseModel):
    sources: list[str] = Field(default_factory=list)
