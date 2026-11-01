"""HTTP JSON fetcher with a *real-data* cache.

Behaviour (this is what powers the "stale data" label in the UI):
  1. fresh cache entry (younger than ``ttl``)  -> use it
  2. otherwise call the live API (2 attempts)  -> store the response, use it
  3. live call failed but an older real response exists -> return it flagged ``stale``
  4. nothing at all -> raise ``DataUnavailable`` (we never invent numbers)

Only genuine API responses are ever written to the cache.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable

from .config import get_settings

MAX_ENTRIES = 300
_lock = threading.RLock()
_outage: set[str] = set()  # demo switch: simulate an API being down


class DataUnavailable(Exception):
    def __init__(self, source: str, detail: str):
        self.source = source
        self.detail = detail
        super().__init__(f"{source}: {detail}")


def set_outage(sources: list[str]) -> list[str]:
    with _lock:
        _outage.clear()
        _outage.update(s for s in sources if s)
        return sorted(_outage)


def get_outage() -> list[str]:
    with _lock:
        return sorted(_outage)


def _cache_key(url: str, params: dict[str, Any] | None) -> str:
    clean = {k: v for k, v in (params or {}).items() if k.lower() not in {"api-key", "api_key", "key"}}
    raw = url + "?" + json.dumps(clean, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()


def _load(path) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path, store: dict[str, Any]) -> None:
    if len(store) > MAX_ENTRIES:
        keep = sorted(store.items(), key=lambda kv: kv[1].get("ts", 0), reverse=True)[:MAX_ENTRIES]
        store = dict(keep)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(store, fh, ensure_ascii=False)
    os.replace(tmp, path)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def _meta(source: str, ts: float, *, stale: bool, from_cache: bool, error: str | None = None) -> dict[str, Any]:
    return {
        "source": source,
        "fetched_at": _iso(ts),
        "age_s": int(max(0, time.time() - ts)),
        "stale": stale,
        "from_cache": from_cache,
        "error": error,
    }


def http_get_json(url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None,
                  timeout: float | None = None) -> Any:
    full = url
    if params:
        full += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(full, headers={"Accept": "application/json",
                                                "User-Agent": "KisanSathi/1.0 (hackathon project)",
                                                **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout or get_settings().http_timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post_json(url: str, body: dict[str, Any], headers: dict[str, str], timeout: float = 60) -> Any:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "KisanSathi/1.0", **headers},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_json(url: str, params: dict[str, Any] | None = None, *, source: str, ttl: float = 600,
               headers: dict[str, str] | None = None,
               transform: Callable[[Any], Any] | None = None) -> tuple[Any, dict[str, Any]]:
    settings = get_settings()
    key = _cache_key(url, params)
    now = time.time()
    with _lock:
        entry = _load(settings.cache_path).get(key)
    if entry and now - entry["ts"] < ttl and source not in _outage:
        return entry["data"], _meta(source, entry["ts"], stale=False, from_cache=True)

    error: str | None = None
    if source in _outage:
        error = "simulated outage (demo switch)"
    else:
        for attempt in range(2):
            try:
                raw = http_get_json(url, params, headers)
                data = transform(raw) if transform else raw
                with _lock:
                    store = _load(settings.cache_path)
                    store[key] = {"ts": now, "data": data, "source": source}
                    _save(settings.cache_path, store)
                return data, _meta(source, now, stale=False, from_cache=False)
            except urllib.error.HTTPError as exc:  # 4xx will not fix itself (except 429)
                error = f"HTTP {exc.code}"
                if exc.code != 429 and 400 <= exc.code < 500:
                    break
            except DataUnavailable:
                raise
            except Exception as exc:  # network, timeout, bad JSON ...
                error = f"{type(exc).__name__}: {exc}"
            time.sleep(0.3)

    if entry:
        return entry["data"], _meta(source, entry["ts"], stale=True, from_cache=True, error=error)
    raise DataUnavailable(source, error or "unknown error")
