"""The spread map: where related media appeared, how each version differs, and how sure each link is.

Every link carries an explicit confidence so the UI never presents a guess as a fact:

  * media ↔ media   "same content"   Verified  = geometric match (hundreds of aligned features)
                                     Lead      = looks similar by hash/embedding only
                    direction arrow  Inferred  = clear asymmetry (e.g. one is a crop/resize of the other)
                                     none      = order cannot be told from the files
  * media → location "appeared at"   Captured  = we hold the bytes + hash of what that URL served
  * location          candidate      Lead      = found by search/links, nothing captured that matches yet
"""
from __future__ import annotations

from sqlmodel import Session, select

from ..core.db import vault_for
from ..core.models import Artifact, Location, MatchEdge, Media
from .discover import public_media_url, relations, reverse_search_links

OP_TEXT = {
    "mirror": "Mirrored left-to-right",
    "re-encode": "Saved again (same picture, different file)",
    "screen_capture": "Re-recorded from a screen",
    "caption": "Caption or banner added",
    "border": "Border or bars added",
}


def _pct_kept(region) -> str:
    try:
        x0, y0, x1, y1 = region
        return f"{max(0, min(1, (x1 - x0) * (y1 - y0))) * 100:.0f}%"
    except (TypeError, ValueError):
        return ""


def describe_ops(ops: list[dict]) -> list[str]:
    out = []
    for t in ops:
        op = t.get("op", "")
        if op == "crop" or op == "crop+reframe":
            k = _pct_kept(t.get("kept_region_of_a") or t.get("kept_region_of_b"))
            out.append("Cropped" + (f" (keeps about {k} of the picture)" if k else ""))
        elif op == "rotate":
            out.append(f"Rotated {t.get('degrees')}°")
        elif op == "downscale":
            out.append(f"Resized smaller (×{t.get('factor')})")
        elif op == "localized_change":
            n = len(t.get("regions_in_a") or [])
            out.append(f"Part of the picture differs ({n} area{'s' if n != 1 else ''}) — possible edit")
        elif op == "text_overlay_added":
            out.append("Text added: “" + " / ".join(t.get("text") or [])[:60] + "”")
        elif op == "format_change":
            out.append(f"Different file format ({(t.get('from') or '?').split('/')[-1]} → {(t.get('to') or '?').split('/')[-1]})")
        elif op == "recompress":
            out.append(f"Re-compressed (JPEG quality ≈ {t.get('quality')})")
        else:
            out.append(OP_TEXT.get(op, op.replace("_", " ").capitalize()))
    return list(dict.fromkeys(out))


def _kb(n: int) -> str:
    return f"{n / 1e6:.1f} MB" if n > 1e6 else f"{max(1, round(n / 1e3))} KB"


def _fmt(m: Media) -> str:
    return (m.mime or "").split("/")[-1].upper()


def _version_diff(a: Media, b: Media) -> list[str]:
    out = []
    if a.width and b.width and (a.width, a.height) != (b.width, b.height):
        out.append(f"Size {a.width}×{a.height} → {b.width}×{b.height}")
    if a.size and b.size and abs(a.size - b.size) > 0.05 * max(a.size, b.size):
        out.append(f"File {_kb(a.size)} → {_kb(b.size)}")
    if a.mime != b.mime:
        out.append(f"Format {_fmt(a)} → {_fmt(b)}")
    return out


def build_spread(s: Session, case_id: str) -> dict:
    from ..pipeline import _media_time, media_explanation
    media = s.exec(select(Media).where(Media.case_id == case_id).order_by(Media.created_at)).all()
    locs = s.exec(select(Location).where(Location.case_id == case_id)).all()
    edges = s.exec(select(MatchEdge).where(MatchEdge.case_id == case_id)).all()
    thumbs = {a.media_id: a.sha256 for a in s.exec(select(Artifact).where(Artifact.case_id == case_id, Artifact.kind == "thumb")).all()}
    rel = relations(s, case_id)
    by_id = {m.id: m for m in media}
    loc_of_media: dict[str, list[str]] = {}
    for l in locs:
        for mid in {*(l.media_ids or []), *[m.id for m in media if m.location_id == l.id]}:
            loc_of_media.setdefault(mid, []).append(l.id)

    mnodes = []
    for m in media:
        v = None
        if m.fingerprints:
            try:
                ex = media_explanation(s, case_id, m.id)["verdict"]
                if ex.get("available"):
                    v = {"label": ex["label"], "headline": ex["headline"], "p_fake": ex["p_fake"], "engine": ex.get("engine")}
            except Exception:  # noqa: BLE001
                v = None
        mnodes.append({"id": m.id, "label": m.label, "role": m.role, "modality": m.modality, "thumb": thumbs.get(m.id),
                       "sha256": m.sha256, "width": m.width, "height": m.height, "size": m.size, "mime": m.mime,
                       "time": _media_time(s, m), "locations": loc_of_media.get(m.id, []), "verdict": v,
                       "analysed": bool(v)})

    medges = []
    for e in edges:
        a, b = by_id.get(e.a_media), by_id.get(e.b_media)
        if not a or not b:
            continue
        ds = float(e.transforms.get("direction_score", 0) or 0)
        direction = "a_to_b" if ds >= 1.5 else "b_to_a" if ds <= -1.5 else None
        ops = e.transforms.get("transforms_b_from_a" if ds >= 0 else "transforms_a_from_b", [])
        src, dst = (a, b) if ds >= 0 else (b, a)
        medges.append({
            "id": e.id, "a": e.a_media, "b": e.b_media, "tier": e.tier,
            "status": "Verified" if e.verified else "Lead", "verified": e.verified,
            "direction": direction, "direction_status": "Inferred" if direction else None,
            "scores": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in (e.scores or {}).items()
                       if isinstance(v, (int, float))},
            "differences": describe_ops(ops) + _version_diff(src, dst),
            "from": src.id, "to": dst.id,
            "notes": (e.transforms.get("notes") or [])[:3],
            "meaning": ("Same picture content — geometric match found." if e.verified else
                        "Looks similar, but the match could not be verified. Check by eye before treating as a copy."),
        })

    lnodes = []
    for l in locs:
        r = rel.get(l.id, {})
        lnodes.append({
            "id": l.id, "url": l.url, "platform": l.platform, "domain": l.domain, "handle": l.account_handle,
            "state": l.state, "published_at": l.published_at.isoformat() if l.published_at else None,
            "first_captured_at": l.first_captured_at.isoformat() if l.first_captured_at else None,
            "published_source": l.published_source, "discovered_via": l.discovered_via, "depth": l.depth,
            "found_from": l.found_from, "seed": l.seed, "dismissed": l.dismissed, "screenshot_sha": l.screenshot_sha,
            "relation": r.get("status", "pending"), "relation_reason": r.get("reason", ""),
            "media_ids": r.get("media_ids", []), "caption": (l.caption or "")[:160],
        })

    # same-content groups = connected components over VERIFIED edges
    parent = {m.id: m.id for m in media}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for e in edges:
        if e.verified and e.a_media in parent and e.b_media in parent:
            parent[find(e.a_media)] = find(e.b_media)
    groups: dict[str, list[str]] = {}
    for m in media:
        groups.setdefault(find(m.id), []).append(m.id)
    clusters = []
    for ids in groups.values():
        if len(ids) < 2 and not any(by_id[i].role == "primary" for i in ids):
            continue
        timed = sorted((n for n in mnodes if n["id"] in ids and n["time"]), key=lambda n: n["time"])
        largest = max((by_id[i] for i in ids), key=lambda m: (m.width or 0) * (m.height or 0))
        clusters.append({"media_ids": ids, "has_primary": any(by_id[i].role == "primary" for i in ids),
                         "earliest_found": timed[0]["id"] if timed else None,
                         "largest_found": largest.id,
                         "note": "“Earliest found” is the earliest copy THIS search discovered — not proof of the original."})

    primary = next((m for m in media if m.role == "primary"), None)
    public = public_media_url(case_id, primary) if primary else None
    if not public:
        seed = next((l for l in locs if l.seed), None)
        if seed and (seed.page_meta or {}).get("direct_media"):
            public = seed.final_url or seed.url
    counts: dict[str, int] = {}
    for l in lnodes:
        counts[l["relation"]] = counts.get(l["relation"], 0) + 1
    return {
        "media": mnodes, "locations": lnodes, "media_edges": medges, "clusters": clusters,
        "counts": counts, "search_links": reverse_search_links(public), "search_links_prefilled": bool(public),
        "primary_media": primary.id if primary else None,
        "legend": {
            "Verified": "Geometric match: hundreds of image features line up under one transform.",
            "Lead": "Looks similar (hash / embedding) but not verified. Treat as a possibility only.",
            "Inferred": "Direction suggested by clear differences (e.g. one is a crop of the other). Not proof of who made which.",
            "Captured": "We downloaded the media from that page and stored its hash.",
        },
        "coverage": ("This map shows only what the search could see on the public web as a logged-out visitor. "
                     "It cannot see private chats, closed groups, deleted posts or logged-in content, so absence from "
                     "the map does not mean absence online."),
    }
