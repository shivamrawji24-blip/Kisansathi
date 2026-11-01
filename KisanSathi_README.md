# KisanSathi 🌾

**A voice-first, bilingual AI farming assistant for Indian farmers.**

KisanSathi brings weather information, mandi prices, crop guidance and farm-profit estimates into one simple interface. It is designed to help farmers compare selling options and understand the assumptions behind an estimate—not to replace advice from local agricultural experts.

> **Project track:** Climate, Resource Infrastructure & Global Supply Resilience  
> **Application:** Python + Starlette API with a vanilla HTML, CSS and JavaScript frontend.

---

## Contents

- [What KisanSathi does](#what-kisansathi-does)
- [Key features](#key-features)
- [How it works](#how-it-works)
- [Technology stack](#technology-stack)
- [Getting started](#getting-started)
- [Environment variables and API keys](#environment-variables-and-api-keys)
- [Run with Docker](#run-with-docker)
- [Deploy on Render](#deploy-on-render)
- [API reference](#api-reference)
- [Run tests](#run-tests)
- [Project structure](#project-structure)
- [Data, assumptions and limitations](#data-assumptions-and-limitations)
- [Demo walkthrough](#demo-walkthrough)
- [Contributing](#contributing)
- [License](#license)

## What KisanSathi does

KisanSathi focuses on practical questions that affect a farmer's decisions:

1. **What should I do?** Ask farming questions in Hindi or English and receive an answer supported by the app's advisory knowledge and available data tools.
2. **Where should I sell?** Compare mandi options using reported prices, estimated transport costs and a produce-quality/spoilage model.
3. **How much might I earn?** Explore an estimated profit range using crop, farm-input and cost information.
4. **What is the weather outlook?** Check a forecast and crop-related weather alerts for a selected location.

## Key features

| Feature | Description |
|---|---|
| Hindi and English interface | Switch the interface and supported advice between Hindi and English. |
| Voice input | Uses the browser's Web Speech API where supported. Text input remains available. |
| Read answers aloud | Uses speech synthesis and voices installed in the browser/device. |
| AI farming assistant | An optional language-model agent can select tools, inspect results and prepare a response. |
| Rule-based fallback | The assistant can still provide supported tool-based responses when no LLM key is configured. |
| Mandi price lookup | Retrieves reported market prices from the data.gov.in Agmarknet resource. |
| Where-to-sell comparison | Estimates net returns after transport, handling and quality-loss assumptions. |
| Profit estimator | Produces cautious, expected and optimistic estimates with break-even and what-if information. |
| Weather and crop alerts | Uses Open-Meteo forecast data and crop-specific thresholds. |
| Advisory search | Retrieves relevant passages from the included bilingual knowledge notes. |
| Resilient data access | Caches successful upstream responses and can identify when older cached data is being used. |

## How it works

```text
 Farmer / browser
       |
       | HTML, CSS, JavaScript (static/)
       | JSON requests
       v
 Starlette application (app/server.py)
       |
       +---- /api/chat ------> AI agent / rule-based fallback
       |                           |
       |                           +--> weather, mandi, profit, advisory tools
       |
       +---- /api/compare ---> mandi comparison + spoilage model
       +---- /api/profit ----> farm profit estimator
       +---- /api/weather ---> Open-Meteo forecast
       +---- /api/mandi -----> data.gov.in / Agmarknet
       +---- /api/geocode --> Open-Meteo geocoding
       |
       +---- serves static/index.html, app.js, style.css and i18n.js
```

### External data services

- **Open-Meteo:** weather forecasts and place geocoding. No API key is normally needed for the configured public endpoints; review its terms for your intended use.
- **data.gov.in / Agmarknet:** reported mandi prices. A data.gov.in API key is needed for live mandi data.
- **OSRM public routing service:** road-distance and route estimates. Availability and usage limits depend on the public service; consider a suitable hosted routing provider for production-scale traffic.
- **Optional LLM provider:** Anthropic or an OpenAI-compatible API. Without a configured LLM, the rule-based assistant is used.

## Technology stack

**Frontend**
- HTML5
- CSS3
- Vanilla JavaScript
- Browser Web Speech API and Speech Synthesis API (where supported)

**Backend**
- Python 3.11+ (Python 3.12 is used in the Docker image)
- Starlette
- Uvicorn
- Pydantic

**Testing and deployment**
- pytest
- Docker
- Render Blueprint (`render.yaml`)

There is no frontend build step or Node.js dependency in this project.

## Getting started

### Requirements

- Python 3.11 or newer
- Git (optional, for cloning and deployment)
- Internet access for live weather, geocoding, routing and mandi services

### 1. Get the source code

```bash
git clone YOUR_GITHUB_REPOSITORY_URL
cd kisansathi
```

Alternatively, extract the project ZIP and open a terminal in the folder containing `requirements.txt`.

### 2. Create and activate a virtual environment

**Windows PowerShell**

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation for the current terminal, run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.venv\Scripts\Activate.ps1
```

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Configure environment variables

Copy `.env.example` to `.env` and add the API key(s) you want to use. Instructions are in [Environment variables and API keys](#environment-variables-and-api-keys).

**Windows PowerShell:**

```powershell
Copy-Item .env.example .env
```

**macOS / Linux:**

```bash
cp .env.example .env
```

### 5. Start the application

```bash
python -m app
```

Open **http://localhost:8000** in your browser. The server binds to `0.0.0.0` and uses port `8000` by default.

### 6. Check external API connectivity

```bash
python scripts/check_apis.py
```

This script helps check whether configured external services are reachable from your network. A failed external check does not necessarily mean the local application cannot start; it may mean that a provider is unavailable, a key is missing, or your network blocks the request.

## Environment variables and API keys

The application reads environment variables and can load values from a local `.env` file. Real environment variables take precedence.

| Variable | Required | Description |
|---|---:|---|
| `DATA_GOV_API_KEY` | For live mandi prices | API key from [data.gov.in](https://data.gov.in/). |
| `ANTHROPIC_API_KEY` | Optional | Enables the Anthropic-powered agent when supplied. |
| `ANTHROPIC_MODEL` | Optional | Anthropic model name; defaults to the value in `app/config.py`. |
| `ANTHROPIC_BASE_URL` | Optional | Anthropic API base URL. |
| `LLM_API_KEY` | Optional | API key for an OpenAI-compatible provider. |
| `LLM_BASE_URL` | Optional | OpenAI-compatible API base URL; defaults to `https://api.openai.com/v1`. |
| `LLM_MODEL` | Optional | Model identifier for the OpenAI-compatible provider. Required along with `LLM_API_KEY` to use this option. |
| `HOST` | Optional | Bind address; defaults to `0.0.0.0`. |
| `PORT` | Optional | Server port; defaults to `8000`. Render supplies its own port. |
| `DEBUG_ENDPOINTS` | Optional | Set to `0` to disable the public outage-simulation endpoint. Use `0` in public deployments. |
| `HTTP_TIMEOUT` | Optional | Timeout in seconds for outbound HTTP requests; defaults to `12`. |
| `CACHE_PATH` | Optional | Path to the local HTTP cache file; defaults to `data/cache.json`. |

### Getting API keys

1. **Mandi prices:** create an account at [data.gov.in](https://data.gov.in/), locate the Agmarknet resource used by the application, and obtain an API key according to the portal's instructions.
2. **AI assistant (optional):** configure either `ANTHROPIC_API_KEY`, or the trio `LLM_API_KEY`, `LLM_MODEL` and `LLM_BASE_URL` for an OpenAI-compatible provider.
3. **Weather, geocoding and routing:** the current configured public endpoints do not require an application key.

Do not commit `.env`, API keys, passwords or other secrets to GitHub. Use your hosting provider's secret/environment-variable settings for deployed applications.

## Run with Docker

Build the image from the project root:

```bash
docker build -t kisansathi .
```

Run it, passing environment variables through a local `.env` file:

```bash
docker run --rm -p 8000:8000 --env-file .env kisansathi
```

Then visit **http://localhost:8000**.

The Docker image sets `DEBUG_ENDPOINTS=0` by default.

## Deploy on Render

This repository includes a `render.yaml` Blueprint file.

### Deploy using Blueprint

1. Push the complete project folder to a GitHub repository. The repository root should contain `render.yaml`, `requirements.txt`, `app/` and `static/`.
2. Sign in at [Render](https://render.com/) using GitHub.
3. In the Render Dashboard, choose **New → Blueprint**.
4. Connect the KisanSathi GitHub repository and select the branch to deploy.
5. Review the service settings loaded from `render.yaml` and create the service.
6. Open the service's **Environment** page and enter the secret values, especially `DATA_GOV_API_KEY`. Add an LLM key only if you want the optional LLM-powered agent.
7. For a public deployment, set `DEBUG_ENDPOINTS` to `0`, save the changes and redeploy if prompted.
8. Wait for the deployment to finish, then open the `onrender.com` URL shown on the service page.

### Manual Web Service settings

If you create a Web Service instead of a Blueprint, use:

| Setting | Value |
|---|---|
| Runtime | Python 3 |
| Build command | `pip install -r requirements.txt` |
| Start command | `python -m app` |
| Root directory | Leave blank when the repository root is the KisanSathi project folder |

Add the environment variables in the Render Dashboard. Do not put secret values directly in `render.yaml` or source code.

### Verify the deployment

Replace `YOUR-APP.onrender.com` with your actual Render hostname:

```text
https://YOUR-APP.onrender.com/
https://YOUR-APP.onrender.com/api/health
https://YOUR-APP.onrender.com/api/crops
```

Render free web services may spin down after a period without traffic, so the first request after inactivity can take longer. Check Render's current plan details for up-to-date limits.

## API reference

All API routes are served by the same application origin. Responses from data-backed endpoints include source/freshness information where applicable.

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/health` | Service status, configured provider information and crop count. |
| `GET` | `/api/crops` | Supported crops and public crop parameters. |
| `GET` | `/api/geocode?q=<place>` | Search for a place. |
| `GET` | `/api/weather?lat=<lat>&lon=<lon>&crop=<crop>` | Forecast and crop-related weather alerts. |
| `GET` | `/api/mandi?crop=<crop>&state=<state>&district=<district>` | Latest available mandi price records and price band. |
| `POST` | `/api/compare` | Compare selling markets and estimated net returns. |
| `POST` | `/api/profit` | Estimate farm profit using submitted crop and input details. |
| `POST` | `/api/chat` | Ask the assistant a question. Accepts a message and optional history, language and location fields. |
| `GET` / `POST` | `/api/debug/outage` | Development/demo outage simulation; disable with `DEBUG_ENDPOINTS=0`. |

For exact request and response schemas, see `app/schemas.py`, `app/server.py` and the frontend calls in `static/app.js`.

### Example health check

```bash
curl https://YOUR-APP.onrender.com/api/health
```

### Example crops request

```bash
curl https://YOUR-APP.onrender.com/api/crops
```

## Run tests

Install development dependencies:

```bash
pip install -r requirements-dev.txt
```

Run the test suite:

```bash
pytest -q
```

The tests use controlled local test fixtures for upstream services. They validate application behavior without depending on live third-party API availability. Use `python scripts/check_apis.py` separately to check real external connectivity.

## Project structure

```text
kisansathi/
├── app/
│   ├── __main__.py          # Application entry point
│   ├── server.py            # Starlette routes and static-file serving
│   ├── config.py            # Environment-based configuration
│   ├── agent.py             # LLM-enabled agent
│   ├── rule_agent.py        # Rule-based fallback assistant
│   ├── llm.py                # LLM provider integration
│   ├── tools.py              # Tools exposed to the assistant
│   ├── services.py           # Service coordination
│   ├── datasources.py        # External data integrations
│   ├── http_cache.py         # HTTP response cache and stale fallback
│   ├── market_compare.py     # Mandi comparison logic
│   ├── shelf_life.py         # Produce quality / spoilage model
│   ├── profit.py              # Farm profit estimation
│   ├── crops.py               # Crop parameters and supported crops
│   ├── rag.py                 # Advisory retrieval
│   ├── schemas.py             # Request/response validation models
│   └── errors.py              # Application errors
├── static/
│   ├── index.html             # Main web interface
│   ├── style.css              # Styling and responsive layout
│   ├── app.js                 # Frontend behavior and API calls
│   └── i18n.js                # Interface translations
├── knowledge/                 # Starter bilingual farming knowledge notes
├── scripts/
│   └── check_apis.py          # External API connectivity check
├── tests/                     # Automated tests and fixtures
├── data/                      # Runtime cache location (local by default)
├── .env.example               # Environment variable template
├── .gitignore
├── Dockerfile
├── render.yaml                # Render Blueprint configuration
├── requirements.txt
├── requirements-dev.txt
└── README.md
```

## Data, assumptions and limitations

KisanSathi is a decision-support prototype. Users should verify important decisions with current local market information and qualified agricultural professionals.

- **Crop parameters are approximate.** Potential yields, recommended nutrient doses, shelf-life assumptions and weather thresholds are model parameters in `app/crops.py`; they are not guaranteed or universally applicable. Validate them against current ICAR, state agricultural university, KVK or other authoritative local guidance before real-world use.
- **Profit estimates are not guarantees.** Results depend on submitted inputs, assumed response curves, yield ranges and available market-price data. Missing inputs may be defaulted; inspect the response and assumptions.
- **Mandi coverage and freshness vary.** The application can only compare records returned by the configured Agmarknet resource. A crop or market may have no recent report. Always check the displayed reporting date and source.
- **Market locations may be approximate.** Market names are geocoded because the source data may not provide precise coordinates. Route distances can therefore be approximate.
- **Spoilage estimates are model-based.** Temperature exposure, vehicle assumptions, handling and waiting time influence the result. Actual quality loss varies by variety, packaging, handling and local conditions.
- **Advisory notes are starter content.** The files in `knowledge/` are project knowledge notes, not a substitute for official, current crop advisories. Add verified local material and keep source references accurate.
- **External services can be unavailable or rate-limited.** Weather, routing, geocoding, mandi and LLM services are outside this project's control. Cached data may be older than live data and should be treated accordingly.
- **Voice features are browser-dependent.** Speech recognition is not supported by every browser or device. Use the text input if the microphone feature is unavailable.

## Demo walkthrough

A short demonstration can follow this sequence:

1. Open the app and switch between Hindi and English.
2. Select a location and crop, then review the weather forecast and crop alerts.
3. Ask a farming question in Hindi or English; show the assistant's answer, sources and tool activity where available.
4. Open **Where to sell**, enter produce quantity and location, and compare the estimated net returns across markets.
5. Open **Profit**, adjust farm inputs and costs, and compare the cautious, expected and optimistic estimates.
6. Explain which values are live external data and which are model assumptions.

## Contributing

Contributions are welcome. A useful contribution should:

1. Explain the problem it addresses.
2. Keep API keys and personal data out of commits and test fixtures.
3. Add or update tests for changed behavior.
4. Cite authoritative sources when changing agronomy parameters or advisory content.
5. Update this README when setup, configuration, endpoints or deployment behavior changes.

Suggested workflow:

```bash
git checkout -b feature/your-change
# make and test your changes
git add .
git commit -m "Describe your change"
git push -u origin feature/your-change
```

## License

No license file was included with this project. Unless a license is added, the source code should be treated as **all rights reserved**; do not assume it is open source or reuse it under an open-source license. Add a `LICENSE` file if you intend to publish the project under a specific license.

---

<p align="center"><strong>KisanSathi — helping farmers make more informed decisions.</strong></p>
