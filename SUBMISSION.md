# SIH submission draft (edit before submitting)

Fill the bracketed parts after you have run `python scripts/check_apis.py`, deployed the app and recorded the video.

## 1. Problem statement
**Target issue.** Small farmers lose income in three connected places: they cannot judge weather risk for their own village, they pick a selling market by headline price (ignoring transport, fees and spoilage in the heat), and they do not know their likely profit before they spend on inputs. Post-harvest loss is large for perishables such as tomato and leafy greens, and rises with temperature.
**Target audience.** Small and marginal farmers (Hindi and regional-language speakers, often phone-first), trader groups and FPOs, and block-level agriculture officers who advise them.

## 2. Solution description
**Overview.** KisanSathi is a voice-first assistant. An AI agent calls live-data tools (weather, Agmarknet mandi prices, OSRM routes, a profit predictor and a cited advisory search) and answers in Hindi or English. Two decision engines do the heavy lifting: a *where-to-sell* ranker that nets out transport, fees and temperature-driven spoilage across departure times, and a *profit predictor* that turns the farmer's own inputs (area, fertiliser, soil, irrigation, seed, sowing delay, costs) into an expected profit with a cautious-to-optimistic range.
**Technical architecture.** Python (Starlette, Pydantic) API; tool-calling agent loop with retry on tool errors, a numeric self-check that rejects figures not found in tool output, and an automatic fallback to a rule-based agent; Q10 spoilage model on forecast temperatures; Mitscherlich-style diminishing-returns nutrient response with P10/P50/P90 uncertainty; BM25 retrieval over bilingual advisory notes with source citations; real-data cache that serves the last genuine response flagged as stale and never invents numbers. Data: Open-Meteo, OSRM, data.gov.in Agmarknet. Front end: dependency-free HTML/CSS/JS with Web Speech API voice input and read-aloud.

## 3. Prototype link
[deployed URL]

## 4. Demo video (2–3 min, live, unedited)
[video URL] — follow the demo script at the end of README.md.

## 5. GitHub repository
[repo URL]

## 6. Pitch deck (5–7 slides)
1. Title and team
2. The problem: three blind spots (weather risk, market choice, profit before spending)
3. The solution and key features (voice, Hindi/English, where-to-sell, profit predictor)
4. Architecture and what is original (spoilage-aware ranking, grounded agent, honest data fallback)
5. Live demo summary and robustness (outage fallback, unknown crop, missing data)
6. Roadmap: more crops and languages (via Bhashini), ISRO Bhuvan / Sentinel NDVI field stress, WhatsApp bot, cold-storage and FPO integration, soil health card input

## Before you submit: checklist from the rules
- [ ] `check_apis.py` is green on the network you will demo from, and `DATA_GOV_API_KEY` is set on the deployed app
- [ ] Crop parameters in `app/crops.py` verified against ICAR/NHB and cited in the README
- [ ] Real advisories added to `knowledge/` (or the notes are described honestly as starter notes)
- [ ] Every teammate can explain: data flow, the spoilage model, the profit model, the agent loop and the self-check
- [ ] Demo is live, not pre-recorded
