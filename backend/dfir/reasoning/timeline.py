"""Crime-scene-style incident timeline. Every event states its time source, precision and status."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlmodel import Session, select

from ..core.custody import CustodyLog
from ..core.db import case_dir, load_claims
from ..core.models import Alert, Case, Location, Media, TimelineNote
from ..pipeline import load_genealogy

LANES = ["provenance", "publication", "archive", "investigation", "reporting", "monitoring"]


def _dt(v) -> datetime | None:
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = str(v).strip()
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt)  # naive: timezone unknown
        except ValueError:
            pass
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d
    except ValueError:
        return None


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def build_timeline(s: Session, case_id: str) -> dict:
    case = s.get(Case, case_id)
    media = {m.id: m for m in s.exec(select(Media).where(Media.case_id == case_id)).all()}
    locs = s.exec(select(Location).where(Location.case_id == case_id)).all()
    loc_by_id = {l.id: l for l in locs}
    ev: list[dict] = []

    def add(at, title, lane, status, *, source, precision="exact", detail="", refs=None, end=None, kind=""):
        d = _dt(at)
        ev.append({"at": _iso(d) if d else None, "end": _iso(_dt(end)) if end else None, "title": title, "lane": lane,
                   "status": status, "time_source": source, "precision": precision if d is None or d.tzinfo else
                   "local time (timezone not recorded)", "detail": detail, "refs": refs or {}, "kind": kind})

    for mid, m in media.items():
        for c in load_claims(s, case_id, "decomposition", mid):
            if c.category == "metadata" and c.data.get("datetime"):
                add(c.data["datetime"], f"Metadata timestamp in “{m.label}”", "provenance", "Inferred",
                    source="EXIF DateTimeOriginal/DateTime (editable)", detail=c.statement,
                    refs={"claim": c.id, "media": mid}, kind="metadata_time")
            if c.category == "metadata" and c.data.get("creation_time"):
                add(c.data["creation_time"], f"Container creation_time in “{m.label}”", "provenance", "Inferred",
                    source="container creation_time (set by last muxer)", detail=c.statement,
                    refs={"claim": c.id, "media": mid}, kind="metadata_time")
            if c.category == "c2pa" and c.data.get("signed_at"):
                add(c.data["signed_at"], f"C2PA manifest signed ({c.data.get('generator') or 'unknown tool'})",
                    "provenance", c.status.value, source="C2PA signature time", refs={"claim": c.id, "media": mid},
                    kind="c2pa")
            if c.category == "c2pa" and c.data.get("when"):
                add(c.data["when"], f"C2PA action: {c.data.get('action')}", "provenance", c.status.value,
                    source="C2PA action assertion", refs={"claim": c.id, "media": mid}, kind="c2pa")

    for loc in locs:
        label = f"{loc.platform}: {('@' + loc.account_handle) if loc.account_handle else loc.domain}"
        if loc.published_at:
            from_page = "metadata" in (loc.published_source or "")
            add(loc.published_at, f"Published — {label}", "publication", "Verified" if from_page else "Inferred",
                source=loc.published_source or "unknown", detail=loc.caption[:200],
                refs={"location": loc.id}, kind="published")
        if loc.first_captured_at:
            add(loc.first_captured_at, f"Captured by investigator — {label} ({loc.state})", "investigation", "Verified",
                source="system clock at capture", refs={"location": loc.id}, kind="captured")
        if loc.state == "removed" and loc.last_checked_at:
            add(loc.last_checked_at, f"Observed removed/unavailable — {label}", "monitoring", "Verified",
                source="capture result", refs={"location": loc.id}, kind="removed")

    enr = case_dir(case_id) / "enrichment.json"
    if enr.exists():
        data = json.loads(enr.read_text(encoding="utf-8"))
        for url, snaps in data.get("wayback", {}).items():
            if isinstance(snaps, list) and snaps:
                add(snaps[0]["timestamp"], f"Earliest Internet Archive snapshot — {url[:60]}", "archive", "Verified",
                    source="Internet Archive CDX", refs={"url": url}, kind="archive")

    for n in s.exec(select(TimelineNote).where(TimelineNote.case_id == case_id)).all():
        add(n.at, n.title, "reporting" if n.kind != "user_note" else "investigation", "Verified",
            source="investigator log (user-attested)", detail=n.detail, refs={"note": n.id}, kind=n.kind)
    for a in s.exec(select(Alert).where(Alert.case_id == case_id)).all():
        add(a.created_at, a.message, "monitoring", "Verified", source="monitoring check", refs={"alert": a.id},
            kind=f"alert_{a.kind}")
    add(case.created_at, "Case opened", "investigation", "Verified", source="system clock", kind="case_opened")
    for e in CustodyLog(case_dir(case_id) / "custody_log.jsonl").entries()[:400]:
        if e["action"] == "vault.store" and e["details"].get("kind") == "original":
            add(e["time"]["utc"], f"Original evidence file preserved ({e['details'].get('filename')})", "investigation",
                "Verified", source="custody ledger", refs={"custody_seq": e["seq"]}, kind="preserved")

    # Unknown: true creation time precedes the earliest evidence
    dated = sorted([e for e in ev if e["at"] and e["lane"] in ("provenance", "publication", "archive")],
                   key=lambda e: _sort_key(e["at"]))
    earliest = dated[0]["at"] if dated else None
    ev.append({"at": None, "end": earliest, "title": "True creation time of the media", "lane": "provenance",
               "status": "Unknown", "time_source": "not established", "precision": "unknown",
               "detail": "Before the earliest evidence shown. Creation time, creator and the first upload remain "
                         "unproven unless platform records or the source file are obtained.", "refs": {},
               "kind": "unknown_creation"})
    gen = load_genealogy(case_id)
    for fam in gen.get("families", []):
        if len(fam["members"]) > 1 and fam.get("earliest_observed"):
            m = media.get(fam["earliest_observed"])
            if m and m.location_id and m.location_id in loc_by_id:
                l = loc_by_id[m.location_id]
                t = l.published_at or l.first_captured_at
                add(t, f"Earliest DISCOVERED appearance of this media family ({l.platform})", "publication", "Inferred",
                    source="earliest dated copy among those discovered", detail="Earliest discovered ≠ original.",
                    refs={"location": l.id, "media": m.id}, kind="earliest_discovered")

    conflicts = []
    pubs = sorted([e for e in ev if e["kind"] == "published" and e["at"]], key=lambda e: _sort_key(e["at"]))
    for e in ev:
        if e["kind"] == "metadata_time" and e["at"] and pubs and _sort_key(e["at"]) > _sort_key(pubs[0]["at"]):
            conflicts.append({"title": "Metadata time is later than the first known publication",
                              "detail": f"{e['title']} ({e['at']}) vs {pubs[0]['title']} ({pubs[0]['at']}). The metadata "
                                        "was likely rewritten by re-saving, or the device clock was wrong.",
                              "refs": [e["refs"], pubs[0]["refs"]]})
    ev.sort(key=lambda e: _sort_key(e["at"] or e.get("end") or "0000"))
    return {"events": ev, "lanes": LANES, "conflicts": conflicts}


def _sort_key(v: str) -> str:
    d = _dt(v)
    if d is None:
        return "0000"
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).isoformat()
