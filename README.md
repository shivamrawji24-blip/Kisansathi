# KisanSathi (किसान साथी)

A voice-first farming assistant for small and marginal farmers in India. It combines **live weather, live mandi prices, road routes and a spoilage model** to answer the questions that decide a farmer's income:

- **Where should I sell?** Ranks nearby mandis by what the farmer actually keeps *after* transport, fees and produce spoilage in the forecast heat, and suggests the best departure time.
- **What will I earn?** A profit predictor that takes quantity (area), fertiliser, soil, irrigation, seed, sowing delay, crop protection and all the farmer's costs, and returns expected profit with a cautious-to-optimistic range, break-even price and what-if table.
- **What should I do?** An AI agent that answers in Hindi or English by calling those tools, with source citations and a number self-check.

Hackathon track: *Climate, Resource Infrastructure & Global Supply Resilience.*

## Features

| Feature | Where |
|---|---|
| Hindi / English toggle (whole UI, all server advice, voice, read-aloud) | header buttons, `static/i18n.js`, bilingual API responses |
| Voice to text (Web Speech API) and read-aloud answers | mic button on **Ask**, mic on place search |
| AI agent with tool calling, retries, self-check, fallback | `app/agent.py`, `app/tools.py`, `app/llm.py` |
| Rule-based fallback agent (works with no AI key) | `app/rule_agent.py` |
| Profit predictor, many farmer inputs | `app/profit.py`, **Profit** tab |
| Where-to-sell engine with spoilage model | `app/market_compare.py`, `app/shelf_life.py`, **Where to sell** tab |
| Weather alerts per crop (heat, cold, rain) | `app/datasources.py`, **Weather and prices** tab |
| Advisory retrieval (BM25 over bilingual notes, cited) | `app/rag.py`, `knowledge/*.md` |
| Stale-data fallback and honest errors | `app/http_cache.py` |

## Architecture

```
 Browser (HTML/CSS/JS, no build step)                      Live open data
 ┌──────────────────────────────────────┐          ┌──────────────────────────────┐
 │ Ask (mic / text)  Where to sell      │          │ Open-Meteo  geocoding+forecast│
 │ Profit            Weather & prices   │          │ OSRM        road routes       │
 │ Hindi/English  ·  Web Speech API     │          │ data.gov.in Agmarknet prices  │
 └───────────────┬──────────────────────┘          └───────────────▲──────────────┘
                 │ JSON                                             │ HTTPS
 ┌───────────────▼────────────────────── Starlette API (app/server.py) ─────────────┐
 │  /api/chat ─► FarmerAgent ──► LLM (Anthropic or any OpenAI-compatible)            │
 │                  │   ▲            plan ─► tool call ─► observe ─► … ─► answer      │
 │                  │   └─ self-check: every ₹/%/kg figure must appear in a tool     │
 │                  │      result or in the farmer's message, else it is corrected   │
 │                  ▼   (LLM down or no key ─► rule-based agent, same tools)         │
 │      tools.py: get_weather · get_mandi_prices · compare_mandis ·                  │
 │                estimate_profit · search_advisory                                  │
 │                  │                                                                │
 │   services.py ─► market_compare.py + shelf_life.py │ profit.py │ rag.py           │
 │                  │                                                                │
 │   datasources.py ─► http_cache.py ──► live API  ─┐                               │
 │                       fresh cache ◄──────────────┤ success: store real response  │
 │                       stale copy (flagged) ◄─────┘ failure: serve last REAL copy  │
 │                       nothing saved ─► honest error (never invented numbers)     │
 └───────────────────────────────────────────────────────────────────────────────────┘
```

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # then fill in the keys (below)
python scripts/check_apis.py                           # confirms every live API works from YOUR network
python -m app                                          # open http://localhost:8000
```

Keys (all free except the optional LLM):

1. **`DATA_GOV_API_KEY`** (required for live mandi prices): sign up at <https://data.gov.in>, open *My Account* and copy the API key.
2. **`ANTHROPIC_API_KEY`** *or* **`LLM_API_KEY` + `LLM_MODEL` + `LLM_BASE_URL`** (optional): enables the full tool-calling agent. Without it, the rule-based agent answers using the same live tools.

Open-Meteo and OSRM need no key.

**Voice input** uses the browser's Web Speech API: it works in Chrome and Edge (desktop and Android) and needs an internet connection. In other browsers the mic explains that it is unsupported and the text box still works. Read-aloud uses the browser's installed voices; Hindi needs a Hindi voice on the device.

### Docker / deploy

```bash
docker build -t kisansathi . && docker run -p 8000:8000 --env-file .env kisansathi
```
`render.yaml` is included for Render.com. Add the keys as secret env vars. Set `DEBUG_ENDPOINTS=1` if you want judges to see the outage-simulation tool.

## How the numbers are produced

**Where to sell** (`market_compare.py`, `shelf_life.py`)
1. Latest price per market in your state from Agmarknet (the newest report per market wins).
2. Markets are located by geocoding the market name (or its district), then routed with OSRM. If routing fails, a straight-line estimate × 1.35 is used and flagged.
3. For each market and each of 12 departure slots in the next 24 h, spoilage is integrated hour by hour over: waiting before loading → journey (temperature blends from origin to destination forecast, +4 °C for a covered truck, fixed 10 °C for a reefer) → waiting at the mandi.
4. Quality `q = exp(−damage)`, with damage rate following a **Q10 rule** (spoilage ×2 to ×3 per +10 °C). Quality maps to a price multiplier: 1.0 at ≥90 %, falling to 0.5 at 60 %, unsellable below.
5. `net = price × quantity × multiplier − commission − km × cost_per_km − handling`. The best departure slot is chosen per market, and markets are ranked by net.

**Profit predictor** (`profit.py`)
- Yield = potential yield × nutrient response × irrigation × seed × sowing date × crop protection.
- Nutrient response is a diminishing-returns curve per nutrient (N, P, K) that starts from what the soil already supplies, plateaus at the recommended dose and falls for heavy over-use. Farmyard manure adds a first-season credit.
- A P10/P50/P90 yield range (wider for rain-fed fields and weather alerts) × a live price band (median min/modal/max across nearby markets, or ±15 % around the farmer's own price) gives the cautious / expected / optimistic profit and the 3×3 what-if table.
- Fields the farmer leaves blank fall back to the recommended dose and crop defaults, and the response lists exactly which ones (`inputs_defaulted`).

## Honest limitations (read before you present)

- **Crop parameters are approximate.** `app/crops.py` holds potential yields, recommended doses, shelf-life hours and heat/cold thresholds as rounded general-agronomy starting values. They are *model parameters*, not live data. **Replace or verify them with ICAR / state agricultural university / NHB figures for your demo state and cite them**, because judges may ask. Fertiliser prices (₹/kg of N, P₂O₅, K₂O) are also assumptions in `profit.py`; the farmer's own spend overrides them.
- **The advisory notes in `knowledge/` are starter text**, not official advisories. Add real ICAR / KVK / state advisories in the same file layout (they are indexed automatically) and keep the `source:` line accurate so citations are true.
- **Mandi coordinates come from geocoding market names**, which is occasionally the district centre (reported as lower confidence) or a wrong match. Agmarknet does not publish coordinates.
- **Coverage depends on Agmarknet.** Some crops/markets have no recent reports; the app says so instead of guessing. Check which crops report daily prices in your demo state first.
- **The profit and spoilage models are decision aids**, not guarantees. The UI shows ranges, assumptions and sources for that reason.
- Supported crops: wheat, rice, maize, mustard, soybean, chickpea, cotton, tomato, potato, onion, banana, spinach. Add more in `app/crops.py`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q          # 74 tests, ~25 s
```

The tests run the app against a **local fake upstream server** (`tests/conftest.py`) that speaks the same JSON as Open-Meteo, OSRM and data.gov.in, so the real HTTP, caching, stale-fallback, API and agent-loop code is exercised. Those synthetic payloads exist **only inside the tests**; the application itself contains no mock or hard-coded data. Live APIs were not reachable from the environment where this project was written, so run `python scripts/check_apis.py` yourself before the demo.

## Project layout

```
app/        server.py (API + static site) · agent.py · tools.py · llm.py · rule_agent.py
            services.py · market_compare.py · shelf_life.py · profit.py · rag.py
            datasources.py · http_cache.py · crops.py · schemas.py · config.py
static/     index.html · style.css · app.js · i18n.js
knowledge/  bilingual advisory notes (indexed automatically)
scripts/    check_apis.py
tests/      74 tests
```

## API (all responses include `sources` with freshness)

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | status, agent mode, key configured |
| `GET /api/crops` | supported crops with recommended doses |
| `GET /api/geocode?q=` | place search (Hindi or English) |
| `GET /api/weather?lat=&lon=&crop=` | 7-day forecast + merged crop alerts |
| `GET /api/mandi?crop=&state=&district=` | latest live prices per market + band |
| `POST /api/compare` | where-to-sell ranking |
| `POST /api/profit` | profit prediction |
| `POST /api/chat` | agent: `{message, history, lang, location}` → answer, tool trace, sources, citations |
| `POST /api/debug/outage` | demo: simulate a source being down (`DEBUG_ENDPOINTS=0` disables) |

Errors are JSON with `message.en` and `message.hi`: `422` invalid or missing input, `503` a live source is down and nothing is saved, `429` rate-limited chat.

## Suggested 2–3 minute live demo

1. Open the app, pick a real village, ask by voice in Hindi: *"मेरे पास 40 क्विंटल टमाटर है, कहाँ बेचूँ?"* and show the tool trace under **How this answer was made**.
2. **Where to sell:** point out that the highest-priced mandi is not the winner once spoilage and transport are counted; change the vehicle to refrigerated and watch the ranking move.
3. **Profit:** reduce nitrogen and watch the model flag it with the yield and profit impact.
4. **Weather and prices:** show the merged heat or rain alert.
5. **Resilience:** open *Demo tools*, switch off a source, show the *saved copy* banner; enter an unknown crop or no location and show the specific error message.
6. Toggle Hindi/English.
#   K i s a n s a t h i  
 