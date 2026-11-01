"""Runtime configuration: environment variables plus an optional .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path | None = None) -> None:
    """Tiny .env loader (no extra dependency). Real env vars win over the file."""
    path = path or BASE_DIR / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    # --- LLM (optional: the app falls back to a rule-based agent without one)
    anthropic_api_key: str
    anthropic_model: str
    anthropic_base_url: str
    llm_api_key: str  # any OpenAI-compatible provider
    llm_base_url: str
    llm_model: str
    # --- open data
    data_gov_api_key: str
    weather_url: str
    geocode_url: str
    osrm_url: str
    mandi_url: str
    # --- runtime
    cache_path: Path
    http_timeout: float
    debug_endpoints: bool

    @property
    def llm_provider(self) -> str:
        if self.anthropic_api_key:
            return "anthropic"
        if self.llm_api_key and self.llm_model:
            return "openai"
        return "none"


def get_settings() -> Settings:
    """Build settings from the environment on every call (keeps tests simple)."""
    return Settings(
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        anthropic_model=_env("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
        anthropic_base_url=_env("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
        llm_api_key=_env("LLM_API_KEY"),
        llm_base_url=_env("LLM_BASE_URL", "https://api.openai.com/v1"),
        llm_model=_env("LLM_MODEL"),
        data_gov_api_key=_env("DATA_GOV_API_KEY"),
        weather_url=_env("WEATHER_URL", "https://api.open-meteo.com/v1/forecast"),
        geocode_url=_env("GEOCODE_URL", "https://geocoding-api.open-meteo.com/v1/search"),
        osrm_url=_env("OSRM_URL", "https://router.project-osrm.org"),
        mandi_url=_env(
            "MANDI_URL",
            "https://api.data.gov.in/resource/9ef84268-d588-465a-a308-a864a43d0070",
        ),
        cache_path=Path(_env("CACHE_PATH", str(BASE_DIR / "data" / "cache.json"))),
        http_timeout=float(_env("HTTP_TIMEOUT", "12")),
        debug_endpoints=_env("DEBUG_ENDPOINTS", "1") not in ("0", "false", "no"),
    )


load_dotenv()
