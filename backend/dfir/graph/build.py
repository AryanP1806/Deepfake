"""Investigation graph: media ↔ copies ↔ URLs ↔ accounts ↔ usernames ↔ domains ↔ hashtags ↔
device/encoder signatures ↔ generator signals. Every edge carries a status."""
from __future__ import annotations

import re

import networkx as nx
from rapidfuzz import fuzz
from sqlmodel import Session, select

from ..core.db import load_claims
from ..core.models import Artifact, Location, Media
from ..pipeline import load_genealogy, media_explanation

HASHTAG_RE = re.compile(r"#(\w{3,40})", re.U)


def norm_handle(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (h or "").lower().lstrip("@"))


def build_graph(s: Session, case_id: str) -> nx.MultiDiGraph:
    G = nx.MultiDiGraph()
    hidden = {l.id for l in s.exec(select(Location).where(Location.case_id == case_id, Location.dismissed == True)).all()}  # noqa: E712
    media = [m for m in s.exec(select(Media).where(Media.case_id == case_id)).all() if m.location_id not in hidden]
    locs = s.exec(select(Location).where(Location.case_id == case_id, Location.dismissed == False)).all()  # noqa: E712
    thumbs = {a.media_id: a.sha256 for a in s.exec(select(Artifact).where(Artifact.case_id == case_id,
                                                                         Artifact.kind == "thumb")).all()}

    def edge(u, v, rel, status, **kw):
        G.add_edge(u, v, key=f"{rel}:{u}:{v}", rel=rel, status=status, **kw)

    for m in media:
        ex = media_explanation(s, case_id, m.id)
        G.add_node(m.id, type="media", label=m.label or m.filename, role=m.role, modality=m.modality,
                   thumb=thumbs.get(m.id), level=ex["level"], sha256=m.sha256)
        for fam in ex["generator_families"]:
            gid = f"gen:{fam['family'].lower()}"
            G.add_node(gid, type="generator", label=fam["family"])
            edge(m.id, gid, "generator_signal", fam["status"], claims=fam["claims"])
        fp = m.fingerprints or {}
        if fp.get("device") and (fp["device"].get("make") or fp["device"].get("model")):
            d = fp["device"]
            did = f"dev:{(d.get('make') or '').lower()}|{(d.get('model') or '').lower()}|{d.get('serial') or ''}"
            G.add_node(did, type="device", label=" ".join(filter(None, [d.get("make"), d.get("model"),
                                                                         f"SN {d['serial']}" if d.get("serial") else None])))
            edge(m.id, did, "metadata_names_device", "Verified",
                 note="Metadata field present; does not prove this device captured the content")
        if fp.get("encoder_signature"):
            eid = f"enc:{fp['encoder_signature']}"
            G.add_node(eid, type="encoder", label=fp["encoder_signature"][:60])
            edge(m.id, eid, "encoder_signature", "Verified")
        if fp.get("qtable_hash"):
            qid = f"qt:{fp['qtable_hash']}"
            G.add_node(qid, type="encoder", label=f"JPEG tables {fp['qtable_hash'][:8]}")
            edge(m.id, qid, "jpeg_table_family", "Verified")
        for c in load_claims(s, case_id, "decomposition", m.id):
            if c.category == "ocr" and c.data.get("handle"):
                aid = f"acct:?:{norm_handle(c.data['handle'])}"
                # OCR noise produces near-duplicate handles; reuse an existing overlay node when nearly identical
                existing = next((n for n in G.nodes if n.startswith("acct:?:") and
                                 fuzz.ratio(n.split(":", 2)[2], norm_handle(c.data["handle"])) >= 90), None)
                aid = existing or aid
                if aid not in G:
                    G.add_node(aid, type="account", label="@" + c.data["handle"], platform="(seen in media)")
                edge(m.id, aid, "handle_visible_in_media", "Lead", claims=[c.id])

    gen = load_genealogy(case_id)
    for e in gen.get("edges", []):
        if e["parent"] in G and e["child"] in G:
            edge(e["parent"], e["child"], "derived_copy", "Verified" if e["verified_match"] else "Lead",
                 direction=e["direction_confidence"], transforms=[t["op"] for t in e["transforms"]])

    for loc in locs:
        G.add_node(loc.id, type="url", label=loc.url, platform=loc.platform, state=loc.state,
                   published=loc.published_at.isoformat() if loc.published_at else None)
        if loc.domain:
            did = f"dom:{loc.domain}"
            G.add_node(did, type="domain", label=loc.domain)
            edge(loc.id, did, "hosted_on", "Verified")
        if loc.account_handle:
            h = norm_handle(loc.account_handle)
            aid = f"acct:{loc.platform}:{h}"
            G.add_node(aid, type="account", label="@" + loc.account_handle.lstrip("@"), platform=loc.platform, handle=h)
            observed = loc.state != "pending" and bool(loc.html_sha)
            edge(loc.id, aid, "posted_by", "Verified" if observed else "Inferred")
            # same handle seen in media overlay
            if f"acct:?:{h}" in G:
                edge(f"acct:?:{h}", aid, "same_handle", "Lead",
                     note="Handle overlay in media equals this account's handle")
        for tag in sorted({t.lower() for t in HASHTAG_RE.findall(loc.caption or "")}):
            tid = f"tag:{tag}"
            G.add_node(tid, type="hashtag", label="#" + tag)
            edge(loc.id, tid, "uses_hashtag", "Verified")
        for m in media:
            if m.id in (loc.media_ids or []) or m.location_id == loc.id:
                edge(m.id, loc.id, "found_at", "Verified")

    # handles read from media overlays (OCR) → link to observed accounts with near-identical handles
    observed = [(n, d) for n, d in G.nodes(data=True) if d.get("type") == "account" and d.get("handle")]
    for n, d in list(G.nodes(data=True)):
        if d.get("type") == "account" and n.startswith("acct:?:"):
            h = n.split(":", 2)[2]
            for o, od in observed:
                score = fuzz.ratio(h, od["handle"])
                if score >= 85:
                    if not G.has_edge(n, o):
                        edge(n, o, "same_handle", "Lead", similarity=score,
                             note="Handle read from the media (OCR) matches this account's handle")
                    G.nodes[n]["merged_into"] = o
    # username similarity across accounts (leads only; look-alike handles can be impersonation or coincidence)
    for i, (a, da) in enumerate(observed):
        for b, db in observed[i + 1:]:
            if da["handle"] == db["handle"] and da.get("platform") == db.get("platform"):
                continue
            score = fuzz.ratio(da["handle"], db["handle"])
            if score >= 85:
                same_platform = da.get("platform") == db.get("platform")
                edge(a, b, "similar_username", "Lead", similarity=score,
                     note=("Look-alike handle on the same platform (possible impersonation or a second account)"
                           if same_platform else "Similar usernames on different platforms can belong to different people"))
    return G


def graph_json(G: nx.MultiDiGraph) -> dict:
    return {
        "nodes": [{"id": n, **d} for n, d in G.nodes(data=True)],
        "edges": [{"id": k, "source": u, "target": v, **d} for u, v, k, d in G.edges(keys=True, data=True)],
    }
