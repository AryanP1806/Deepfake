from __future__ import annotations

import json
from collections import Counter

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..core import jobs
from ..core.db import case_dir, get_session, load_claims
from ..core.models import Location, MatchEdge, Media
from ..graph.analytics import analyse
from ..graph.build import build_graph, graph_json
from ..pipeline import load_genealogy, run_genealogy
from ..propagation.adapters.public_sources import DomainIntel, WaybackCDX
from ..propagation.adapters.search import ADAPTERS
from ..propagation.intake import add_locations, capture_locations, run_adapter_search
from ..reasoning.attribution import build_leads
from ..reasoning.gaps import analyse_gaps
from ..reasoning.narrative import build_narrative
from ..reasoning.next_action import next_action
from ..reasoning.timeline import build_timeline
from .cases import _case, thumbs

router = APIRouter(prefix="/api")


@router.get("/cases/{case_id}/overview")
def overview(case_id: str, s: Session = Depends(get_session)):
    _case(s, case_id)
    return {"narrative": build_narrative(s, case_id), "next_action": next_action(s, case_id)}


@router.get("/cases/{case_id}/genealogy")
def genealogy(case_id: str, s: Session = Depends(get_session)):
    _case(s, case_id)
    th = thumbs(s, case_id)
    media = s.exec(select(Media).where(Media.case_id == case_id)).all()
    locs = {l.id: l for l in s.exec(select(Location).where(Location.case_id == case_id)).all()}
    edges = {e.id: e for e in s.exec(select(MatchEdge).where(MatchEdge.case_id == case_id)).all()}
    tree = load_genealogy(case_id)
    for e in tree.get("edges", []):
        me = edges.get(e.get("match_id"))
        if me:
            e["geometry"] = me.transforms.get("geometry")
            e["scores"] = me.scores
            e["a_media"] = me.a_media
    nodes = []
    for m in media:
        mlocs = sorted([l for l in locs.values() if m.id in (l.media_ids or []) or l.id == m.location_id],
                       key=lambda l: (l.published_at or l.first_captured_at or l.added_at).isoformat())
        loc = mlocs[0] if mlocs else None
        nodes.append({"id": m.id, "label": m.label, "role": m.role, "modality": m.modality, "thumb": th.get(m.id),
                      "width": m.width, "height": m.height, "mime": m.mime, "size": m.size, "sha256": m.sha256,
                      "location": {"url": loc.url, "platform": loc.platform, "handle": loc.account_handle,
                                   "published_at": loc.published_at.isoformat() if loc.published_at else None,
                                   "captured_at": loc.first_captured_at.isoformat() if loc.first_captured_at else None,
                                   "state": loc.state} if loc else None,
                      "other_locations": [{"url": l.url, "platform": l.platform, "handle": l.account_handle,
                                           "state": l.state} for l in mlocs[1:]]})
    claims = [c.model_dump(mode="json") for c in load_claims(s, case_id, "genealogy")]
    return {"nodes": nodes, "tree": tree, "claims": claims}


@router.post("/cases/{case_id}/genealogy/run")
def genealogy_run(case_id: str, s: Session = Depends(get_session)):
    _case(s, case_id)
    return jobs.submit("genealogy", case_id, lambda p: run_genealogy(case_id, p))


class LocIn(BaseModel):
    url: str
    handle: str = ""
    platform: str = ""
    caption: str = ""
    notes: str = ""
    published_at: str | None = None


@router.get("/cases/{case_id}/locations")
def locations(case_id: str, s: Session = Depends(get_session)):
    _case(s, case_id)
    media = s.exec(select(Media).where(Media.case_id == case_id)).all()
    th = thumbs(s, case_id)
    out = []
    for l in s.exec(select(Location).where(Location.case_id == case_id)).all():
        d = l.model_dump(mode="json")
        d["media"] = [{"id": m.id, "thumb": th.get(m.id), "label": m.label, "modality": m.modality}
                      for m in media if m.id in (l.media_ids or []) or m.location_id == l.id]
        out.append(d)
    out.sort(key=lambda d: d.get("published_at") or d.get("first_captured_at") or d["added_at"])
    return out


@router.post("/cases/{case_id}/locations")
def add_locs(case_id: str, items: list[LocIn], capture: bool = True, s: Session = Depends(get_session)):
    _case(s, case_id)
    locs = add_locations(s, case_id, [i.model_dump() for i in items])
    job = None
    if capture and locs:
        ids = [l.id for l in locs]
        job = jobs.submit("capture", case_id, lambda p: capture_locations(case_id, ids, p))
    return {"added": [l.model_dump(mode="json") for l in locs], "job": job.model_dump(mode="json") if job else None}


@router.post("/cases/{case_id}/capture")
def capture(case_id: str, location_ids: list[str] | None = Body(default=None), s: Session = Depends(get_session)):
    _case(s, case_id)
    return jobs.submit("capture", case_id, lambda p: capture_locations(case_id, location_ids, p))


@router.get("/cases/{case_id}/propagation")
def propagation(case_id: str, s: Session = Depends(get_session)):
    _case(s, case_id)
    locs = locations(case_id, s)
    p = case_dir(case_id) / "enrichment.json"
    enr = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"wayback": {}, "domains": {}}
    by_platform = Counter(l["platform"] for l in locs)
    tree = load_genealogy(case_id)
    earliest = {f.get("earliest_observed") for f in tree.get("families", [])}
    roots = {f.get("root") for f in tree.get("families", [])}
    for l in locs:
        l["is_earliest_discovered"] = any(m["id"] in earliest for m in l["media"])
        l["is_inferred_root"] = any(m["id"] in roots for m in l["media"])
    return {"locations": locs, "enrichment": enr, "platforms": by_platform,
            "claims": [c.model_dump(mode="json") for c in load_claims(s, case_id, "propagation") +
                       load_claims(s, case_id, "propagation_enrich")],
            "free_sources": [WaybackCDX().describe(), DomainIntel().describe()],
            "adapters": [a.describe() for a in ADAPTERS]}


class SearchIn(BaseModel):
    query: str | None = None
    media_id: str | None = None
    channel: str | None = None


@router.post("/cases/{case_id}/adapters/{name}/search")
def adapter_search(case_id: str, name: str, body: SearchIn, s: Session = Depends(get_session)):
    _case(s, case_id)
    try:
        return run_adapter_search(case_id, name, query=body.query, media_id=body.media_id, channel=body.channel)
    except KeyError as e:
        raise HTTPException(404, f"unknown adapter {name}") from e


@router.get("/cases/{case_id}/graph")
def graph(case_id: str, s: Session = Depends(get_session)):
    _case(s, case_id)
    G = build_graph(s, case_id)
    data = graph_json(G)
    an = analyse(G, load_genealogy(case_id))
    th = thumbs(s, case_id)
    for n in data["nodes"]:
        if n.get("type") == "media":
            n["thumb"] = th.get(n["id"])
        n["centrality"] = an["centrality"].get(n["id"], 0)
    return {**data, "analytics": an}


@router.get("/cases/{case_id}/timeline")
def timeline(case_id: str, s: Session = Depends(get_session)):
    _case(s, case_id)
    return build_timeline(s, case_id)


@router.get("/cases/{case_id}/leads")
def leads(case_id: str, s: Session = Depends(get_session)):
    _case(s, case_id)
    return build_leads(s, case_id)


@router.get("/cases/{case_id}/gaps")
def gaps(case_id: str, s: Session = Depends(get_session)):
    _case(s, case_id)
    return analyse_gaps(s, case_id)


@router.get("/cases/{case_id}/next-action")
def next_act(case_id: str, s: Session = Depends(get_session)):
    _case(s, case_id)
    return next_action(s, case_id)


@router.get("/cases/{case_id}/claims")
def claims(case_id: str, stage: str | None = None, s: Session = Depends(get_session)):
    _case(s, case_id)
    return [c.model_dump(mode="json") for c in load_claims(s, case_id, stage)]


@router.get("/cases/{case_id}/claims/{claim_id}")
def claim(case_id: str, claim_id: str, s: Session = Depends(get_session)):
    for c in load_claims(s, case_id):
        if c.id == claim_id:
            return c.model_dump(mode="json")
    raise HTTPException(404)
