"""Persistent data model (SQLite via SQLModel)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def JSONField(default: Any = None):  # noqa: N802
    factory = (lambda: {}) if default is None else (lambda: type(default)(default))
    return Field(default_factory=factory, sa_column=Column(JSON))


class Case(SQLModel, table=True):
    id: str = Field(default_factory=lambda: _id("case"), primary_key=True)
    title: str
    created_at: datetime = Field(default_factory=_now)
    # ncii | sextortion | impersonation_fraud | political_defamation | voice_clone_scam | other
    incident_type: str = "other"
    # {"minors_possible": bool, "intimate": bool}
    safety: dict = JSONField({})
    description: str = ""
    # Complainant details stay local. Never sent anywhere without consent.
    complainant: dict = JSONField({})
    status: str = "open"
    is_demo: bool = False


class Media(SQLModel, table=True):
    id: str = Field(default_factory=lambda: _id("med"), primary_key=True)
    case_id: str = Field(index=True)
    sha256: str = Field(index=True)
    md5: str = ""
    modality: str  # image | video | audio
    role: str = "primary"  # primary | discovered | reference
    filename: str = ""
    mime: str = ""
    size: int = 0
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[float] = None
    location_id: Optional[str] = None  # Location it was captured from
    label: str = ""
    fingerprints: dict = JSONField({})
    created_at: datetime = Field(default_factory=_now)


class ClaimRow(SQLModel, table=True):
    id: str = Field(primary_key=True)
    case_id: str = Field(index=True)
    media_id: Optional[str] = Field(default=None, index=True)
    stage: str = Field(index=True)
    category: str = ""
    status: str = ""
    body: dict = JSONField({})


class Artifact(SQLModel, table=True):
    id: str = Field(default_factory=lambda: _id("art"), primary_key=True)
    case_id: str = Field(index=True)
    media_id: Optional[str] = Field(default=None, index=True)
    sha256: str
    kind: str  # ela | noise | fft | faces | frames | spectrogram | transcript | ocr | thumb | diff
    label: str = ""
    analyzer: str = ""
    mime: str = "image/png"
    data: dict = JSONField({})


class AnalysisRun(SQLModel, table=True):
    id: str = Field(default_factory=lambda: _id("run"), primary_key=True)
    case_id: str = Field(index=True)
    media_id: Optional[str] = Field(default=None, index=True)
    component: str
    version: str = ""
    outcome: str  # ok | not_run | error
    reason: str = ""
    how_to_enable: str = ""
    started_at: datetime = Field(default_factory=_now)
    finished_at: Optional[datetime] = None


class Location(SQLModel, table=True):
    """A place on the web where the media (or a related copy) was observed."""

    id: str = Field(default_factory=lambda: _id("loc"), primary_key=True)
    case_id: str = Field(index=True)
    url: str
    final_url: str = ""
    domain: str = ""
    platform: str = ""
    account_handle: str = ""
    account_display: str = ""
    caption: str = ""
    published_at: Optional[datetime] = None
    published_source: str = ""  # where the publish time came from (og meta, page text, user note)
    first_captured_at: Optional[datetime] = None
    last_checked_at: Optional[datetime] = None
    state: str = "pending"  # pending | live | removed | changed | blocked | error | restricted
    http_status: Optional[int] = None
    screenshot_sha: str = ""
    html_sha: str = ""
    headers: dict = JSONField({})
    page_meta: dict = JSONField({})
    content_fingerprint: str = ""  # sha256 of normalised visible text, for change detection
    reach: dict = JSONField({})  # public counts captured from page (views/likes) if shown
    media_ids: list = JSONField([])  # all media items captured at this location
    discovered_via: str = "manual"  # manual | serpapi | tineye | wayback | monitor | ...
    notes: str = ""
    added_at: datetime = Field(default_factory=_now)


class MatchEdge(SQLModel, table=True):
    id: str = Field(default_factory=lambda: _id("mtc"), primary_key=True)
    case_id: str = Field(index=True)
    a_media: str
    b_media: str
    scores: dict = JSONField({})
    transforms: dict = JSONField({})
    tier: str = ""  # hash | embedding | geometric | temporal
    verified: bool = False


class TimelineNote(SQLModel, table=True):
    """User-logged events (e.g. complaint submitted, ack number received, platform reply)."""

    id: str = Field(default_factory=lambda: _id("evt"), primary_key=True)
    case_id: str = Field(index=True)
    at: datetime
    title: str
    detail: str = ""
    kind: str = "user_note"  # user_note | report_submitted | platform_response | removal_confirmed
    data: dict = JSONField({})


class Consent(SQLModel, table=True):
    id: str = Field(default_factory=lambda: _id("cns"), primary_key=True)
    case_id: Optional[str] = Field(default=None, index=True)
    action: str  # e.g. "api_detect:hive", "wayback_save", "llm_narrative", "rfc3161"
    scope: str = "once"  # once | case
    granted_at: datetime = Field(default_factory=_now)
    used: bool = False
    detail: dict = JSONField({})


class Job(SQLModel, table=True):
    id: str = Field(default_factory=lambda: _id("job"), primary_key=True)
    case_id: Optional[str] = Field(default=None, index=True)
    target: Optional[str] = None  # e.g. the media id being analysed
    started_at: Optional[datetime] = None
    kind: str
    status: str = "queued"  # queued | running | done | error
    progress: float = 0.0
    message: str = ""
    error: str = ""
    created_at: datetime = Field(default_factory=_now)
    finished_at: Optional[datetime] = None


class Complaint(SQLModel, table=True):
    case_id: str = Field(primary_key=True)
    state: str = "ready"  # ready | edited | submitted
    fields: dict = JSONField({})
    ack_number: str = ""
    submitted_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=_now)


class MonitorConfig(SQLModel, table=True):
    case_id: str = Field(primary_key=True)
    enabled: bool = False
    interval_minutes: int = 360
    last_run_at: Optional[datetime] = None


class Alert(SQLModel, table=True):
    id: str = Field(default_factory=lambda: _id("alr"), primary_key=True)
    case_id: str = Field(index=True)
    kind: str  # removed | changed | restored | new_copy | new_snapshot | error
    message: str
    created_at: datetime = Field(default_factory=_now)
    seen: bool = False
    data: dict = JSONField({})
