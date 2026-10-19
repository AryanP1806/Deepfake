"""User interface preferences, persisted beside the case data so they survive restarts (and are the same in every browser).

The browser also keeps a copy in localStorage so the first paint already has the right theme; this file is the source of truth.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from .config import DATA_DIR

PREFS_FILE = DATA_DIR / "preferences.json"
THEMES = ("workbench", "neomorphism", "claymorphism")
MODES = ("light", "dark", "system")
MOTION = ("system", "reduce", "full")
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

DEFAULTS: dict = {"theme": "neomorphism", "mode": "light", "accent": "#2f6fe0", "motion": "system",
                  "profile": {"name": "", "role": "Investigator"}, "forensics": {"disabled_modules": []}, "updated_at": None}


def load() -> dict:
    out = json.loads(json.dumps(DEFAULTS))
    if PREFS_FILE.exists():
        try:
            data = json.loads(PREFS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                out.update({k: v for k, v in data.items() if k in DEFAULTS})
        except (OSError, json.JSONDecodeError):
            pass
    return _clean(out)


def _clean(p: dict) -> dict:
    p["theme"] = p["theme"] if p.get("theme") in THEMES else DEFAULTS["theme"]
    p["mode"] = p["mode"] if p.get("mode") in MODES else DEFAULTS["mode"]
    p["motion"] = p["motion"] if p.get("motion") in MOTION else DEFAULTS["motion"]
    p["accent"] = p["accent"] if isinstance(p.get("accent"), str) and HEX.match(p["accent"]) else DEFAULTS["accent"]
    pr = p.get("profile") if isinstance(p.get("profile"), dict) else {}
    p["profile"] = {"name": str(pr.get("name", ""))[:60].strip(), "role": (str(pr.get("role", "")) or "Investigator")[:40].strip()}
    f = p.get("forensics") if isinstance(p.get("forensics"), dict) else {}
    p["forensics"] = {"disabled_modules": [m for m in f.get("disabled_modules", []) if isinstance(m, str)][:50]}
    return p


def save(update: dict) -> dict:
    cur = load()
    for k, v in update.items():
        if k in DEFAULTS and k != "updated_at" and v is not None:
            cur[k] = v
    cur = _clean(cur)
    cur["updated_at"] = datetime.now(timezone.utc).isoformat()
    PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PREFS_FILE.write_text(json.dumps(cur, indent=2), encoding="utf-8")
    return cur
