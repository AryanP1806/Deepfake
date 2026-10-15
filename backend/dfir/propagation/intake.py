"""URL intake, capture orchestration, archive/domain enrichment and adapter-driven discovery."""
from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import urlparse

from sqlmodel import Session, select

from ..core import consent
from ..core.claims import Claim, NotRun, Status, Strength
from ..core.db import case_dir, delete_claims, save_claims, session, vault_for
from ..core.models import Location, Media
from .adapters.public_sources import DomainIntel, WaybackCDX
from .adapters.search import get_adapter
from .capture import parse_time, run_capture
from .platforms import classify


def normalise_url(u: str) -> str:
    u = u.strip()
    if not u:
        return ""
    if not urlparse(u).scheme:
        u = "https://" + u
    return u


def add_locations(s: Session, case_id: str, items: list[dict], discovered_via: str = "manual") -> list[Location]:
    existing = {l.url for l in s.exec(select(Location).where(Location.case_id == case_id)).all()}
    out = []
    for it in items:
        url = normalise_url(it.get("url", ""))
        if not url or url in existing:
            continue
        cls = classify(url)
        loc = Location(case_id=case_id, url=url, domain=cls["domain"], platform=it.get("platform") or cls["platform"],
                       account_handle=(it.get("handle") or cls["handle"] or "").lstrip("@"),
                       caption=it.get("caption", ""), notes=it.get("notes", ""), discovered_via=discovered_via,
                       published_at=parse_time(it.get("published_at")),
                       published_source="investigator note" if it.get("published_at") else "")
        s.add(loc)
        existing.add(url)
        out.append(loc)
        vault_for(case_id).custody.append("location.added", actor="investigator" if discovered_via == "manual" else
                                          f"adapter:{discovered_via}", details={"url": url, "location_id": loc.id})
    s.commit()
    return out


def capture_locations(case_id: str, location_ids: list[str] | None = None, progress=lambda f, m: None,
                      genealogy: bool = True) -> list[dict]:
    from ..pipeline import run_genealogy
    results = []
    with session() as s:
        q = select(Location).where(Location.case_id == case_id)
        locs = [l for l in s.exec(q).all() if (location_ids is None and l.state == "pending") or
                (location_ids is not None and l.id in location_ids)]
        for i, loc in enumerate(locs):
            progress(i / max(1, len(locs)) * 0.8, f"Capturing {loc.url[:60]}")
            results.append(run_capture(s, case_id, loc))
    progress(0.82, "Archive & domain enrichment")
    enrich(case_id)
    if genealogy:
        progress(0.9, "Updating media genealogy")
        run_genealogy(case_id)
    progress(1.0, "Capture complete")
    return results


def enrich(case_id: str) -> dict:
    """Wayback snapshots per URL and RDAP/DNS per domain. Results cached to case dir + claims."""
    wb, di = WaybackCDX(), DomainIntel()
    out = {"wayback": {}, "domains": {}}
    path = case_dir(case_id) / "enrichment.json"
    if path.exists():
        out = json.loads(path.read_text(encoding="utf-8"))
    claims: list[Claim] = []
    with session() as s:
        locs = s.exec(select(Location).where(Location.case_id == case_id)).all()
        for loc in locs:
            if loc.url not in out["wayback"]:
                r = wb.snapshots(loc.url)
                out["wayback"][loc.url] = r.model_dump() if isinstance(r, NotRun) else r
            snaps = out["wayback"][loc.url]
            if isinstance(snaps, list) and snaps:
                first = snaps[0]
                claims.append(Claim(case_id=case_id, stage="propagation", category="archive",
                                    statement=f"Internet Archive holds {len(snaps)} snapshot(s) of {loc.url}; earliest "
                                              f"{first['timestamp']}",
                                    status=Status.VERIFIED, strength=Strength.STRONG,
                                    why="Independent third-party archive record of when the URL was crawled.",
                                    limitations="Shows the URL existed by that time; the snapshot may not include the media.",
                                    sources=["internet_archive"], produced_by="wayback_cdx@1.0",
                                    data={"location_id": loc.id, "snapshots": snaps[:10]}))
            if loc.domain and loc.domain not in out["domains"]:
                r = di.lookup(loc.domain)
                out["domains"][loc.domain] = r.model_dump() if isinstance(r, NotRun) else r
        for dom, info in out["domains"].items():
            if "rdap" in info:
                rd = info["rdap"]
                claims.append(Claim(case_id=case_id, stage="propagation", category="domain",
                                    statement=f"Domain {info.get('registrable_domain')} registered {rd.get('registered') or 'date not public'}"
                                              f" via {rd.get('registrar') or 'unknown registrar'}"
                                              + (f"; abuse contact {rd['abuse_contact']}" if rd.get("abuse_contact") else ""),
                                    status=Status.VERIFIED, strength=Strength.MODERATE,
                                    why="Public RDAP registration record.",
                                    limitations="Registrant identity is usually redacted; a registrar can disclose it "
                                                "only through its legal process.",
                                    produced_by="rdap@1.0", sources=["rdap"], data={"domain": dom, **info}))
        delete_claims(s, case_id, "propagation_enrich")
        for c in claims:
            c.stage = "propagation_enrich"
        save_claims(s, claims)
        s.commit()
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    return out


def run_adapter_search(case_id: str, adapter_name: str, *, query: str | None = None, media_id: str | None = None,
                       channel: str | None = None) -> dict:
    a = get_adapter(adapter_name)
    nr = a.available()
    if nr:
        return {"not_run": nr.model_dump()}
    with session() as s:
        if a.consent_action:
            consent.require(s, a.consent_action, case_id)
        cands = []
        if a.kind == "text":
            cands = a.search_text(query or "")
        elif a.kind == "channel":
            cands = a.list_posts(channel or "")
        elif a.kind == "image":
            m = s.get(Media, media_id)
            v = vault_for(case_id)
            if hasattr(a, "search_image_bytes"):
                cands = a.search_image_bytes(v.path(m.sha256).read_bytes(), m.filename or "image.jpg")
            else:
                rec = v.record(m.sha256) or {}
                public = (rec.get("acquisition") or {}).get("media_url")
                if not public:
                    return {"not_run": {"component": a.name, "reason": "This adapter needs a public image URL; "
                                        "capture a public post of this media first."}}
                cands = a.search_image_url(public)
            v.custody.append("external.search", tool=a.name, item_sha256=m.sha256, details={"adapter": a.name})
        locs = add_locations(s, case_id, [{"url": c.url, "notes": f"{c.source}: {c.title} {c.snippet}".strip()[:500]}
                                          for c in cands], discovered_via=a.name)
    return {"candidates": len(cands), "new_locations": [l.id for l in locs],
            "note": "Candidates are pending capture; they are not evidence until captured and matched."}


def location_summary(loc: Location, media: list[Media]) -> dict:
    d = loc.model_dump(mode="json")
    d["media"] = [{"id": m.id, "sha256": m.sha256, "modality": m.modality, "label": m.label} for m in media]
    return d


def first_seen(loc: Location) -> datetime | None:
    return loc.published_at or loc.first_captured_at
