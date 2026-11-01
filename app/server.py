"""HTTP API + static website (Starlette). Run: ``python -m app`` or
``uvicorn app.server:app``. Interactive field names are documented in README."""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Awaitable, Callable

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import __version__, services
from .agent import FarmerAgent
from .config import BASE_DIR, get_settings
from .crops import CROPS, UnknownCrop, public_crop
from .errors import MissingInput
from .http_cache import DataUnavailable, get_outage, set_outage
from .schemas import ChatInput, CompareInput, OutageInput, ProfitInput

log = logging.getLogger("kisansathi")
STATIC_DIR = BASE_DIR / "static"


def _err(status: int, code: str, en: str, hi: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"error": code, "message": {"en": en, "hi": hi}, **extra}, status_code=status)


def api(fn: Callable[[Request], Awaitable[Response]]):
    """Turn domain errors into clean, bilingual JSON error responses."""
    @wraps(fn)
    async def wrapper(request: Request) -> Response:
        try:
            return await fn(request)
        except ValidationError as exc:
            return _err(422, "invalid_input", "Some inputs are invalid.", "कुछ इनपुट सही नहीं हैं।",
                        details=json.loads(exc.json(include_url=False)))
        except UnknownCrop as exc:
            names_en = ", ".join(CROPS[c]["en"] for c in exc.supported)
            names_hi = ", ".join(CROPS[c]["hi"] for c in exc.supported)
            return _err(422, "unknown_crop", f"Unknown crop '{exc.name}'. Supported: {names_en}.",
                        f"'{exc.name}' फसल उपलब्ध नहीं है। उपलब्ध: {names_hi}।", supported=exc.supported)
        except MissingInput as exc:
            return _err(422, "missing_input", exc.hint_en, exc.hint_hi, field=exc.field)
        except DataUnavailable as exc:
            return _err(503, "data_unavailable",
                        f"Live data source '{exc.source}' is unavailable and no saved copy exists: {exc.detail}",
                        f"लाइव डेटा स्रोत '{exc.source}' अभी उपलब्ध नहीं है और कोई सहेजी हुई प्रति नहीं है: {exc.detail}",
                        source=exc.source, detail=exc.detail)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return _err(400, "bad_json", "Request body is not valid JSON.", "रिक्वेस्ट बॉडी मान्य JSON नहीं है।")
        except Exception:  # pragma: no cover
            log.exception("unhandled error in %s", fn.__name__)
            return _err(500, "internal_error", "Something went wrong on the server.", "सर्वर पर कुछ गड़बड़ हुई।")
    return wrapper


# --- tiny in-memory rate limiter (protects LLM credits on a public deploy)
_hits: dict[str, deque] = defaultdict(deque)
_hits_lock = threading.Lock()


def rate_limited(request: Request, limit: int = 30, window: float = 60.0) -> bool:
    ip = (request.client.host if request.client else "?")
    now = time.time()
    with _hits_lock:
        q = _hits[ip]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return True
        q.append(now)
    return False


def _f(request: Request, name: str) -> float | None:
    v = request.query_params.get(name)
    return float(v) if v not in (None, "") else None


def create_app(agent: FarmerAgent | None = None) -> Starlette:
    the_agent = agent or FarmerAgent()

    @api
    async def health(request: Request) -> Response:
        s = get_settings()
        return JSONResponse({
            "status": "ok", "version": __version__, "agent_mode": the_agent.mode,
            "mandi_key_configured": bool(s.data_gov_api_key), "simulated_outages": get_outage(),
            "debug_endpoints": s.debug_endpoints,
            "crops": len(CROPS), "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })

    @api
    async def crops(request: Request) -> Response:
        return JSONResponse({"crops": [public_crop(c) for c in CROPS.values()]})

    @api
    async def geocode(request: Request) -> Response:
        q = request.query_params.get("q", "")
        lang = "hi" if request.query_params.get("lang") == "hi" else "en"
        return JSONResponse(await run_in_threadpool(services.geocode_service, q, lang))

    @api
    async def weather(request: Request) -> Response:
        lat, lon = _f(request, "lat"), _f(request, "lon")
        if lat is None or lon is None:
            raise MissingInput("location", "Pick a location first.", "पहले लोकेशन चुनिए।")
        out = await run_in_threadpool(services.weather_service, lat, lon, request.query_params.get("crop") or None)
        return JSONResponse(out)

    @api
    async def mandi(request: Request) -> Response:
        p = request.query_params
        out = await run_in_threadpool(services.mandi_service, p.get("crop", ""), p.get("state") or None,
                                      p.get("district") or None)
        return JSONResponse(out)

    @api
    async def compare(request: Request) -> Response:
        inp = CompareInput(**(await request.json()))
        lang = "hi" if request.query_params.get("lang") == "hi" else "en"
        return JSONResponse(await run_in_threadpool(services.compare_service, inp, lang))

    @api
    async def profit(request: Request) -> Response:
        inp = ProfitInput(**(await request.json()))
        return JSONResponse(await run_in_threadpool(services.profit_service, inp))

    @api
    async def chat(request: Request) -> Response:
        if rate_limited(request):
            return _err(429, "rate_limited", "Too many requests - please wait a minute.",
                        "बहुत ज़्यादा अनुरोध - कृपया एक मिनट रुकिए।")
        inp = ChatInput(**(await request.json()))
        history = [t.model_dump() for t in inp.history]
        loc = inp.location.model_dump() if inp.location else None
        out = await run_in_threadpool(the_agent.run, inp.message, history, inp.lang, loc)
        return JSONResponse(out)

    @api
    async def outage(request: Request) -> Response:
        if not get_settings().debug_endpoints:
            return _err(404, "not_found", "Not found.", "नहीं मिला।")
        if request.method == "POST":
            body = OutageInput(**(await request.json()))
            return JSONResponse({"simulated_outages": set_outage(body.sources)})
        return JSONResponse({"simulated_outages": get_outage()})

    routes = [
        Route("/api/health", health), Route("/api/crops", crops), Route("/api/geocode", geocode),
        Route("/api/weather", weather), Route("/api/mandi", mandi),
        Route("/api/compare", compare, methods=["POST"]), Route("/api/profit", profit, methods=["POST"]),
        Route("/api/chat", chat, methods=["POST"]),
        Route("/api/debug/outage", outage, methods=["GET", "POST"]),
        Mount("/", app=StaticFiles(directory=str(STATIC_DIR), html=True), name="static"),
    ]
    app = Starlette(routes=routes, middleware=[Middleware(GZipMiddleware, minimum_size=1024)])
    app.state.agent = the_agent
    return app


app = create_app()
