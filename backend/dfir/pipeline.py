"""Orchestration: ingest → decompose → genealogy. Called by API jobs, monitoring and the demo."""
from __future__ import annotations

import itertools
import json
from datetime import datetime, timezone
from typing import Callable

from sqlmodel import Session, select

from .analyzers.base import Ctx, run_analyzers
from .analyzers.explain import explain
from .analyzers.verdict import build_verdict
from .analyzers.media_io import load_image, sample_frames, sniff_modality, thumbnail, video_info
from .core.claims import Claim, Polarity, Status, Strength
from .core.db import case_dir, delete_claims, load_claims, save_claims, session, vault_for
from .core.models import AnalysisRun, Artifact, Case, ClaimRow, Location, MatchEdge, Media
from .genealogy import match as gm
from .genealogy.fingerprint import fingerprint_media, representative_image
from .genealogy.transforms import infer
from .genealogy.tree import build_tree

Progress = Callable[[float, str], None]


def _noop(_f: float, _m: str) -> None:
    pass


# --------------------------------------------------------------------------- ingest
def ingest_media(s: Session, case_id: str, data: bytes, filename: str, *, role: str = "primary",
                 location_id: str | None = None, acquisition: dict | None = None, actor: str = "investigator",
                 label: str = "") -> Media:
    modality, mime = sniff_modality(data, filename)
    if modality == "unknown":
        raise ValueError(f"Unsupported media type for {filename}")
    v = vault_for(case_id)
    rec = v.store(data, kind="original" if role == "primary" else "download", filename=filename, mime=mime,
                  acquisition=acquisition or {"method": "user_upload", "original_filename": filename}, actor=actor)
    existing = s.exec(select(Media).where(Media.case_id == case_id, Media.sha256 == rec["sha256"])).first()
    if existing:
        if location_id and not existing.location_id:
            existing.location_id = location_id
            s.add(existing)
            s.commit()
        return existing
    m = Media(case_id=case_id, sha256=rec["sha256"], md5=rec["md5"], modality=modality, role=role, filename=filename,
              mime=mime, size=len(data), location_id=location_id, label=label or filename)
    path = v.path(rec["sha256"])
    try:
        if modality == "image":
            im = load_image(path)
            m.width, m.height = im.size
            thumb = thumbnail(im)
        elif modality == "video":
            vi = video_info(path)
            m.width, m.height, m.duration = vi["width"], vi["height"], vi["duration"]
            fr = sample_frames(path, max_frames=1)
            thumb = thumbnail(fr[0][1]) if fr else None
        else:
            from .analyzers.media_io import ffprobe
            m.duration = float(ffprobe(path).get("format", {}).get("duration") or 0)
            thumb = None
    except Exception:  # noqa: BLE001
        thumb = None
    s.add(m)
    s.commit()
    if thumb is not None:
        from .analyzers.media_io import png_bytes
        trec = v.store(png_bytes(thumb), kind="artifact", filename=f"{m.id}_thumb.png", mime="image/png",
                       acquisition={"derived_from": m.sha256, "analysis": "thumbnail"})
        s.add(Artifact(case_id=case_id, media_id=m.id, sha256=trec["sha256"], kind="thumb", label="Thumbnail"))
        s.commit()
    return m


# --------------------------------------------------------------------------- decomposition
def analyze_media(case_id: str, media_id: str, progress: Progress = _noop, only: set[str] | None = None) -> dict:
    with session() as s:
        case = s.get(Case, case_id)
        media = s.get(Media, media_id)
        v = vault_for(case_id)
        ctx = Ctx(case_id=case_id, media=media, path=v.path(media.sha256), vault=v, safety=case.safety or {})
        if only is None:
            delete_claims(s, case_id, "decomposition", media_id)
            for a in s.exec(select(Artifact).where(Artifact.media_id == media_id, Artifact.kind != "thumb")).all():
                s.delete(a)
            for r in s.exec(select(AnalysisRun).where(AnalysisRun.media_id == media_id)).all():
                s.delete(r)
            s.commit()
        else:  # re-running selected analyzers: replace only their previous output
            for row in s.exec(select(ClaimRow).where(ClaimRow.media_id == media_id, ClaimRow.stage == "decomposition")).all():
                if row.body.get("produced_by", "").split("@")[0] in only:
                    s.delete(row)
            for a in s.exec(select(Artifact).where(Artifact.media_id == media_id, Artifact.analyzer.in_(only))).all():
                s.delete(a)
            for r in s.exec(select(AnalysisRun).where(AnalysisRun.media_id == media_id, AnalysisRun.component.in_(only))).all():
                s.delete(r)
            s.commit()
        runs = run_analyzers(s, ctx, progress=lambda f, m: progress(f * 0.85, m), only=only)
        progress(0.9, "Fingerprinting for genealogy")
        media.fingerprints = fingerprint_media(ctx.path, media.modality)
        ents = ctx.shared.get("ocr_entities")
        if ents is not None or "ocr_lines" in ctx.shared:
            media.fingerprints["ocr"] = [l["text"] for l in ctx.shared.get("ocr_lines", [])]
        jq = next((c.data.get("quality_estimate") for c in ctx.claims if c.category == "encoding"
                   and "quality_estimate" in c.data), None)
        if jq:
            media.fingerprints["jpeg_q"] = jq
        for key in ("qtable_hash", "encoder_signature"):
            if ctx.shared.get(key):
                media.fingerprints[key] = ctx.shared[key]
        md = ctx.shared.get("metadata_details") or {}
        exif = md.get("exif") or {}
        if exif.get("Make") or exif.get("Model"):
            media.fingerprints["device"] = {"make": exif.get("Make"), "model": exif.get("Model"),
                                            "serial": exif.get("BodySerialNumber")}
        s.add(media)
        s.commit()
        not_run = [{"component": r.component, "reason": r.reason, "how_to_enable": r.how_to_enable, "outcome": r.outcome}
                   for r in runs if r.outcome != "ok"]
        with session() as s2:
            summary = media_explanation(s2, case_id, media_id)
        (case_dir(case_id) / f"explain_{media_id}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        progress(1.0, "Decomposition complete")
        return summary


def media_explanation(s: Session, case_id: str, media_id: str) -> dict:
    claims = load_claims(s, case_id, "decomposition", media_id) +         [c for c in load_claims(s, case_id, "genealogy", media_id) if c.category == "family_provenance"]
    runs = s.exec(select(AnalysisRun).where(AnalysisRun.media_id == media_id)).all()
    not_run = [{"component": r.component, "reason": r.reason, "how_to_enable": r.how_to_enable, "outcome": r.outcome}
               for r in runs if r.outcome != "ok"]
    ex = explain(claims, not_run)
    ex["verdict"] = build_verdict(claims, not_run)
    return ex


# --------------------------------------------------------------------------- genealogy
def media_locations(s: Session, m: Media) -> list[Location]:
    locs = s.exec(select(Location).where(Location.case_id == m.case_id)).all()
    return [l for l in locs if m.id in (l.media_ids or []) or l.id == m.location_id]


def _media_time(s: Session, m: Media) -> str | None:
    """Earliest time this media is known to have existed: publication/capture of a location carrying it, or —
    for files supplied by the complainant — when they were preserved (an upper bound on its existence)."""
    times = [(l.published_at or l.first_captured_at) for l in media_locations(s, m)]
    times = [t.replace(tzinfo=t.tzinfo or timezone.utc) for t in times if t]
    if times:
        return min(times).astimezone(timezone.utc).isoformat()
    rec = vault_for(m.case_id).record(m.sha256) or {}
    return (rec.get("stored_at") or {}).get("utc")


def _meta(s: Session, m: Media) -> dict:
    fp = m.fingerprints or {}
    return {"mime": m.mime, "modality": m.modality, "sha256": m.sha256, "jpeg_q": fp.get("jpeg_q"),
            "ocr": fp.get("ocr", []), "ocr_extra": bool(fp.get("ocr")), "time": _media_time(s, m),
            "screen_like": m.modality == "video" and bool(fp.get("ocr"))}


def _embedding_cos(a_id: str, b_id: str, imgs: dict) -> float | None:
    """CLIP cosine similarity between two media, embeddings cached per media for the genealogy run."""
    try:
        from .analyzers.clip_backend import clip_status, embed
        if not clip_status()[0]:
            return None
        for mid in (a_id, b_id):
            if f"emb:{mid}" not in imgs:
                imgs[f"emb:{mid}"] = embed([imgs[mid]])[0]
        return float(imgs[f"emb:{a_id}"] @ imgs[f"emb:{b_id}"])
    except Exception:  # noqa: BLE001
        return None


def compare_pair(s: Session, case_id: str, a: Media, b: Media, imgs: dict, gate_cos: float | None = None) -> MatchEdge | None:
    fa, fb = a.fingerprints or {}, b.fingerprints or {}
    v = vault_for(case_id)
    res = None
    temporal = None
    if a.modality in ("image", "video") and b.modality in ("image", "video"):
        for m in (a, b):
            if m.id not in imgs:
                imgs[m.id] = representative_image(v.path(m.sha256), m.modality)
        ia, ib = imgs[a.id], imgs[b.id]
        if a.modality == "video" and b.modality == "video":
            temporal = gm.compare_frame_sequences(fa["video"]["frames"], fb["video"]["frames"])
        if ia is not None and ib is not None:
            ra = fa.get("image") or fa.get("video", {}).get("rep")
            rb = fb.get("image") or fb.get("video", {}).get("rep")
            if ra and rb:
                res = gm.compare_images(ia, ib, ra, rb, _embedding_cos(a.id, b.id, imgs), gate_cos)
            # image vs video: does the still appear among the video's frames?
            if (res is None or not res.matched) and {a.modality, b.modality} == {"image", "video"}:
                img_m, vid_m = (a, b) if a.modality == "image" else (b, a)
                img_fp = (img_m.fingerprints or {}).get("image", {})
                best = min((min(gm.hamming_hex(img_fp["pdq"], f["pdq"]), gm.hamming_hex(img_fp["pdq_mirror"], f["pdq"]))
                            for f in vid_m.fingerprints["video"]["frames"]), default=999)
                if best <= gm.PDQ_LOOSE:
                    res = gm.MatchResult(True, "hash", False, {"pdq_best_frame": best})
        if temporal and temporal["frac_b"] >= 0.3:
            if res is None or not res.matched:
                res = gm.MatchResult(True, "temporal", temporal["offset_consistency"] >= 0.6, {}, None)
            res.temporal = temporal
    if "audio" in fa and "audio" in fb:
        au = gm.compare_audio(fa["audio"].get("landmarks", []), fb["audio"].get("landmarks", []))
        if au and au["aligned_hashes"] >= 20:
            if res is None or not res.matched:
                res = gm.MatchResult(True, "audio", au["aligned_hashes"] >= 60, {}, None)
            res.scores["audio"] = au
        elif res and res.matched and a.modality == "video" and b.modality == "video" and au is not None:
            res.scores["audio_mismatch"] = True
    if res is None or not res.matched:
        return None
    ma, mb = _meta(s, a), _meta(s, b)
    tr = infer(ma, mb, res, imgs.get(a.id), imgs.get(b.id))
    if res.temporal:
        if res.temporal["frac_b"] < 0.85:
            tr["transforms_b_from_a"].append({"op": "partial_reuse_or_trim", "matched_frac": round(res.temporal["frac_b"], 2),
                                              "offset_s": res.temporal["offset_s"]})
        if res.temporal.get("mirrored"):
            tr["transforms_b_from_a"].append({"op": "mirror", "evidence": "frame sequence matches mirrored"})
    if res.scores.get("audio_mismatch"):
        tr["transforms_b_from_a"].append({"op": "audio_replaced", "evidence": "frames match but audio fingerprints do not"})
        tr["transforms_a_from_b"].append({"op": "audio_replaced", "evidence": "frames match but audio fingerprints do not"})
    g = res.geometry
    edge = MatchEdge(case_id=case_id, a_media=a.id, b_media=b.id, tier=res.tier, verified=res.verified,
                     scores={**res.scores, **({"temporal": res.temporal} if res.temporal else {})},
                     transforms={**tr, "geometry": g.__dict__ if g else None})
    return edge


def _extend_edges(s: Session, case_id: str, media: list[Media], new_ids: set[str], imgs: dict, progress: Progress) -> None:
    """Incremental matching for newly discovered media: compare each new file with the suspect media and copies
    already verified (growing outward), instead of with every other file. Cost grows with the number of new files,
    not with the square of all files."""
    order = {m.id: i for i, m in enumerate(media)}
    by_id = {m.id: m for m in media}
    adj: dict[str, set[str]] = {}
    for e in s.exec(select(MatchEdge).where(MatchEdge.case_id == case_id, MatchEdge.verified == True)).all():  # noqa: E712
        adj.setdefault(e.a_media, set()).add(e.b_media)
        adj.setdefault(e.b_media, set()).add(e.a_media)
    comp = [m.id for m in media if m.role == "primary"]  # (a page may re-serve the suspect file itself: still the start)
    seen = set(comp)
    stack = list(comp)
    while stack:
        for n in adj.get(stack.pop(), ()):
            if n not in seen and n in by_id:
                seen.add(n)
                comp.append(n)
                stack.append(n)
    remaining = [i for i in new_ids if i in by_id and i not in seen]
    done = 0
    changed = True
    while changed and remaining:
        changed = False
        for rid in list(remaining):
            done += 1
            progress(min(0.95, done / max(1, len(new_ids) * 2)), f"Comparing {by_id[rid].label[:30]}")
            for cid in comp[:12]:
                a, b = sorted((by_id[cid], by_id[rid]), key=lambda m: order[m.id])
                e = compare_pair(s, case_id, a, b, imgs, gate_cos=0.72)
                if e:
                    s.add(e)
                    s.commit()
                    if e.verified:
                        comp.append(rid)
                        remaining.remove(rid)
                        changed = True
                        break


def run_genealogy(case_id: str, progress: Progress = _noop, new_media_ids: list[str] | None = None) -> dict:
    """Rebuild the copy relationships and family tree.

    ``new_media_ids=None``: compare every pair (small cases, after re-analysis).
    ``new_media_ids=[...]``: keep existing matches and only match those files (used by copy discovery)."""
    with session() as s:
        media = s.exec(select(Media).where(Media.case_id == case_id)).all()
        media = [m for m in media if m.fingerprints]
        imgs: dict = {}
        if new_media_ids is None:
            for e in s.exec(select(MatchEdge).where(MatchEdge.case_id == case_id)).all():
                s.delete(e)
            delete_claims(s, case_id, "genealogy")
            s.commit()
            for i, (a, b) in enumerate(itertools.combinations(media, 2)):
                progress(i / max(1, len(media) * (len(media) - 1) // 2), f"Comparing {a.label[:20]} ↔ {b.label[:20]}")
                e = compare_pair(s, case_id, a, b, imgs)
                if e:
                    s.add(e)
                    s.commit()
        else:
            delete_claims(s, case_id, "genealogy")
            s.commit()
            _extend_edges(s, case_id, media, set(new_media_ids), imgs, progress)
        edges = s.exec(select(MatchEdge).where(MatchEdge.case_id == case_id)).all()
        nodes = [{"id": m.id, "time": _media_time(s, m)} for m in media]
        tree = build_tree(nodes, [{"id": e.id, "a": e.a_media, "b": e.b_media, "verified": e.verified, "tier": e.tier,
                                   "direction_score": e.transforms.get("direction_score", 0),
                                   "transforms_b_from_a": e.transforms.get("transforms_b_from_a", []),
                                   "transforms_a_from_b": e.transforms.get("transforms_a_from_b", []),
                                   "notes": e.transforms.get("notes", []),
                                   "geometry": e.transforms.get("geometry")} for e in edges])
        claims = []
        by_id = {m.id: m for m in media}
        for e in edges:
            a, b = by_id[e.a_media], by_id[e.b_media]
            ops = sorted({t["op"] for t in e.transforms.get("transforms_b_from_a", []) + e.transforms.get("transforms_a_from_b", [])})
            claims.append(Claim(
                case_id=case_id, media_id=b.id, stage="genealogy", category="match",
                statement=f"“{b.label}” and “{a.label}” are the same underlying media"
                          + (f" (differences: {', '.join(ops)})" if ops else ""),
                status=Status.VERIFIED if e.verified else Status.LEAD,
                strength=Strength.STRONG if e.verified else Strength.WEAK,
                why=("Geometric verification aligned hundreds of local features under a single transform — this "
                     "does not happen between unrelated images." if e.tier == "geometric" else
                     f"Matched by {e.tier} similarity."),
                limitations="Shows shared visual content, not who made either copy. Two independently shot photos of "
                            "the same scene can partially match; check the overlay." if e.verified else
                            "Similarity-only match; requires visual confirmation.",
                evidence_refs=[a.sha256, b.sha256], produced_by="genealogy.match@1.0",
                data={"edge_id": e.id, "tier": e.tier, "scores": e.scores}))
        for fam in tree["families"]:
            if len(fam["members"]) > 1:
                root = by_id[fam["root"]]
                claims.append(Claim(
                    case_id=case_id, media_id=root.id, stage="genealogy", category="origin",
                    statement=f"Within this family of {len(fam['members'])} copies, “{root.label}” is the inferred "
                              "closest-to-source copy among those discovered",
                    status=Status.INFERRED, strength=Strength.WEAK,
                    why="It has the least degradation and the most content, and/or is the earliest observed, compared "
                        "with the other copies.",
                    limitations="This is NOT the proven original. The true first upload or source file may never have "
                                "been discovered, may be private, or may have been deleted.",
                    what_would_confirm="The uploader's original file with intact metadata/C2PA, or platform upload records "
                                       "(obtainable only through lawful requests by police).",
                    evidence_refs=[root.sha256], produced_by="genealogy.tree@1.0", data={"family": fam}))
        # provenance found on one verified copy informs every member of the family (Inferred)
        for fam in tree["families"]:
            if len(fam["members"]) < 2:
                continue
            members = set(fam["members"])
            prov = [c for mid in members for c in load_claims(s, case_id, "decomposition", mid)
                    if c.status == Status.VERIFIED and c.strength == Strength.STRONG
                    and c.polarity == Polarity.SUPPORTS_MANIPULATION and c.category in ("c2pa", "provenance", "watermark")]
            verified_links = {(e.a_media, e.b_media) for e in edges if e.verified}
            for mid in members:
                for pc in prov:
                    if pc.media_id == mid:
                        continue
                    src = by_id[pc.media_id]
                    linked = _verified_path(verified_links, mid, pc.media_id)
                    claims.append(Claim(
                        case_id=case_id, media_id=mid, stage="genealogy", category="family_provenance",
                        statement=f"A copy of the same underlying media (“{src.label}”) carries verified generation "
                                  f"evidence: {pc.statement}",
                        status=Status.INFERRED, strength=Strength.STRONG if linked else Strength.MODERATE,
                        polarity=Polarity.SUPPORTS_MANIPULATION,
                        why="This file was matched to that copy" + (" through geometrically verified links" if linked
                                                                     else " by similarity") +
                            ", and that copy carries generator provenance. Provenance metadata is usually stripped "
                            "when media is re-shared, so it survives on few copies.",
                        limitations="Rests on two links: the match (shared content) and the provenance claim on the "
                                    "other copy. Generator metadata can in principle be added to an image deliberately.",
                        evidence_refs=[src.sha256, by_id[mid].sha256], produced_by="genealogy.family@1.0",
                        sources=[pc.produced_by, "genealogy.match"],
                        data={"source_media": src.id, "source_claim": pc.id,
                              "generator_family": pc.data.get("generator_family"), "verified_path": linked}))
        save_claims(s, claims)
        s.commit()
        (case_dir(case_id) / "genealogy.json").write_text(json.dumps(tree, indent=2, default=str), encoding="utf-8")
        progress(1.0, "Genealogy complete")
        return tree


def _verified_path(links: set[tuple[str, str]], a: str, b: str) -> bool:
    import networkx as nx
    g = nx.Graph()
    g.add_edges_from(links)
    return a in g and b in g and nx.has_path(g, a, b)


def load_genealogy(case_id: str) -> dict:
    p = case_dir(case_id) / "genealogy.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"families": [], "edges": []}


def parse_dt(v) -> datetime | None:
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
