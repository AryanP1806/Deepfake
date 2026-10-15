"""Runtime configuration.

Keys for optional external services are read from environment variables or from
``data/settings.json``. A missing key never raises — adapters report ``NotRun`` instead.
"""
from __future__ import annotations

import json
import os
import platform
import socket
from functools import lru_cache
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_ROOT.parent
DATA_DIR = Path(os.environ.get("DFIR_DATA_DIR", PROJECT_ROOT / "data"))
MODELS_DIR = Path(os.environ.get("DFIR_MODELS_DIR", PROJECT_ROOT / "models"))
SETTINGS_FILE = DATA_DIR / "settings.json"

APP_NAME = "DFIR Deepfake Investigation & Response System"
APP_VERSION = "0.1.0"

# Every optional external key the system knows about. Value = human description.
KNOWN_KEYS: dict[str, str] = {
    "HIVE_SECRET_KEY": "Hive V3 secret key (Bearer token) - AI-generated & deepfake detection",
    "HIVE_SECRET_KEY_2": "Extra Hive V3 secret key, used if the first fails (more: HIVE_SECRET_KEY_3 ... _20)",
    "HIVE_ACCESS_KEY_ID": "Hive V3 access key id (optional, informational)",
    "HIVE_REGION": "Hive V3 region: sf1 (default) or va1",
    "HIVE_V3_URL": "Override the Hive V3 endpoint URL (optional)",
    "HIVE_AUTO_UPLOAD": "1 (default) = send media to Hive automatically as the primary detector; 0 = ask per case",
    "HIVE_API_KEY": "Hive legacy V2 project key (Token auth)",
    "REALITY_DEFENDER_API_KEY": "Reality Defender deepfake detection API",
    "SIGHTENGINE_API_USER": "Sightengine API user",
    "SIGHTENGINE_API_SECRET": "Sightengine API secret",
    "SERPAPI_KEY": "SerpAPI (Google Lens / web search)",
    "TINEYE_API_KEY": "TinEye reverse image search API",
    "BING_VISUAL_SEARCH_KEY": "Bing Visual Search API",
    "YOUTUBE_API_KEY": "YouTube Data API v3",
    "REDDIT_CLIENT_ID": "Reddit API client id",
    "REDDIT_CLIENT_SECRET": "Reddit API client secret",
    "ANTHROPIC_API_KEY": "Claude API for grounded narrative drafting",
}


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "cases").mkdir(exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)


def _load_settings_file() -> dict:
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _load_dotenv() -> dict:
    """Read KEY=VALUE lines from backend/.env and project-root .env (never committed)."""
    out: dict[str, str] = {}
    if os.environ.get("DFIR_NO_DOTENV") == "1":  # tests: never pick up the user's real API keys
        return out
    for p in (PROJECT_ROOT / ".env", BACKEND_ROOT / ".env"):
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip().removeprefix("export ").strip()] = v.strip().strip('"').strip("'")
    return out


def get_key(name: str) -> str | None:
    val = os.environ.get(name) or _load_dotenv().get(name) or _load_settings_file().get("keys", {}).get(name)
    return val or None


def set_key(name: str, value: str | None) -> None:
    if name not in KNOWN_KEYS:
        raise KeyError(name)
    ensure_dirs()
    data = _load_settings_file()
    keys = data.setdefault("keys", {})
    if value:
        keys[name] = value
    else:
        keys.pop(name, None)
    SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def key_status() -> dict[str, dict]:
    return {k: {"description": d, "configured": bool(get_key(k))} for k, d in KNOWN_KEYS.items()}


@lru_cache
def host_info() -> dict:
    return {
        "hostname": socket.gethostname(),
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "python": platform.python_version(),
        "app": f"{APP_NAME} {APP_VERSION}",
    }
