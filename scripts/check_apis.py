"""Run this FIRST on your own machine/network: it calls every live API the app needs
and tells you what works. (A broken API during the live demo costs the most points.)

    python scripts/check_apis.py
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.config import get_settings  # noqa: E402  (also loads .env)

s = get_settings()
OK, BAD = "\033[32mOK  \033[0m", "\033[31mFAIL\033[0m"


def get(url, params):
    full = url + "?" + urllib.parse.urlencode(params)
    t0 = time.time()
    with urllib.request.urlopen(urllib.request.Request(full, headers={"User-Agent": "KisanSathi-check"}), timeout=s.http_timeout) as r:
        return json.loads(r.read()), int((time.time() - t0) * 1000)


def check(name, fn):
    try:
        detail, ms = fn()
        print(f"{OK} {name:<28} {ms:>5} ms  {detail}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"{BAD} {name:<28} {type(e).__name__}: {e}")
        return False


def geo():
    d, ms = get(s.geocode_url, {"name": "Kota", "count": 3, "countryCode": "IN", "format": "json"})
    r = d["results"][0]
    return f"{r['name']}, {r.get('admin1')} ({r['latitude']:.2f},{r['longitude']:.2f})", ms


def weather():
    d, ms = get(s.weather_url, {"latitude": 25.18, "longitude": 75.83, "daily": "temperature_2m_max,et0_fao_evapotranspiration",
                                "hourly": "temperature_2m", "forecast_days": 2, "timezone": "auto"})
    return f"tmax tomorrow {d['daily']['temperature_2m_max'][1]} C", ms


def route():
    d, ms = get(f"{s.osrm_url}/route/v1/driving/75.83,25.18;75.64,25.44", {"overview": "false"})
    return f"{d['routes'][0]['distance'] / 1000:.0f} km Kota-Bundi", ms


def mandi():
    if not s.data_gov_api_key:
        raise RuntimeError("DATA_GOV_API_KEY missing - get a free key at https://data.gov.in (My Account)")
    d, ms = get(s.mandi_url, {"api-key": s.data_gov_api_key, "format": "json", "limit": 5, "filters[commodity]": "Tomato"})
    recs = d.get("records", [])
    if not recs:
        raise RuntimeError(f"no records returned: {str(d)[:160]}")
    r = recs[0]
    return f"{len(recs)} rows, e.g. {r.get('Market') or r.get('market')} {r.get('Modal_Price') or r.get('Modal_x0020_Price')} on {r.get('Arrival_Date')}", ms


results = [check("Geocoding (Open-Meteo)", geo), check("Weather (Open-Meteo)", weather),
           check("Routing (OSRM)", route), check("Mandi prices (data.gov.in)", mandi)]
print()
print("LLM:", {"anthropic": f"Anthropic ({s.anthropic_model})", "openai": f"OpenAI-compatible ({s.llm_model})"}.get(s.llm_provider,
      "none configured -> basic rule-based agent (set ANTHROPIC_API_KEY or LLM_API_KEY for the full AI agent)"))
sys.exit(0 if all(results) else 1)
