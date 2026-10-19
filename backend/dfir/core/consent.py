"""Per-action consent records.

Any outward-facing action (sending media to a commercial API, asking Wayback to archive
a URL, calling an LLM with case data, requesting a trusted timestamp) requires a consent
record. ``require`` raises ``ConsentRequired`` so the API can return 428 and the UI can ask.
"""
from __future__ import annotations

from sqlmodel import Session, select

from .models import Consent

OUTWARD_ACTIONS = {
    "api_detect:hive": "Upload this media to Hive's servers for AI-generation detection",
    "api_detect:reality_defender": "Upload this media to Reality Defender's servers for deepfake detection",
    "api_detect:sightengine": "Upload this media to Sightengine's servers for AI-generation detection",
    "search:serpapi": "Send this image to SerpAPI / Google Lens for reverse search",
    "search:tineye": "Send this image to TinEye for reverse search",
    "search:duckduckgo": "Send a caption / title phrase (text only, no image) to DuckDuckGo to look for copies",
    "search:bing": "Send this image to Bing Visual Search",
    "wayback_save": "Ask the Internet Archive to publicly archive this URL (the archive becomes public)",
    "llm_narrative": "Send case claims (not media) to the Claude API to draft narrative text",
    "rfc3161": "Send the package hash (not content) to FreeTSA for a trusted timestamp",
}


class ConsentRequired(Exception):
    def __init__(self, action: str, case_id: str | None):
        self.action = action
        self.case_id = case_id
        super().__init__(f"Consent required for {action}: {OUTWARD_ACTIONS.get(action, action)}")


def grant(s: Session, action: str, case_id: str | None, scope: str = "once", detail: dict | None = None) -> Consent:
    if action not in OUTWARD_ACTIONS:
        raise ValueError(f"Unknown outward action {action}")
    c = Consent(action=action, case_id=case_id, scope=scope, detail=detail or {})
    s.add(c)
    s.commit()
    return c


def has(s: Session, action: str, case_id: str | None) -> bool:
    q = select(Consent).where(Consent.action == action, Consent.case_id == case_id, Consent.used == False)  # noqa: E712
    if s.exec(q).first():
        return True
    q2 = select(Consent).where(Consent.action == action, Consent.case_id == case_id, Consent.scope == "case")
    return s.exec(q2).first() is not None


def require(s: Session, action: str, case_id: str | None) -> None:
    """Consume a one-shot consent or check case-scoped consent; raise if none."""
    q = select(Consent).where(Consent.action == action, Consent.case_id == case_id)
    rows = s.exec(q).all()
    for c in rows:
        if c.scope == "case":
            return
    for c in rows:
        if not c.used:
            c.used = True
            s.add(c)
            s.commit()
            return
    raise ConsentRequired(action, case_id)
