"""Case narrative: What happened → Why we believe it → Where it spread → Who/what is connected →
What is proven → What is only a lead → What is missing → What to do next.

Template-based and fully grounded: each sentence lists the claim/record ids it rests on."""
from __future__ import annotations

from collections import Counter

from sqlmodel import Session, select

from ..core.claims import sanitize_text
from ..core.db import load_claims
from ..core.models import Case, Location, Media
from ..pipeline import load_genealogy, media_explanation
from .attribution import build_leads
from .gaps import analyse_gaps
from .next_action import next_action


def _s(text: str, refs: list[str] | None = None) -> dict:
    return {"text": sanitize_text(text), "refs": refs or []}


def build_narrative(s: Session, case_id: str) -> dict:
    case = s.get(Case, case_id)
    media = s.exec(select(Media).where(Media.case_id == case_id)).all()
    primary = [m for m in media if m.role == "primary"]
    locs = s.exec(select(Location).where(Location.case_id == case_id, Location.dismissed == False)).all()  # noqa: E712
    gen = load_genealogy(case_id)
    all_claims = load_claims(s, case_id)

    what = []
    why = []
    for m in primary:
        ex = media_explanation(s, case_id, m.id)
        v = ex.get("verdict") or {}
        if v.get("available"):
            what.append(_s(f"“{m.label}”: {v['summary']} (confidence: {v['confidence']}).", [m.id]))
            for r in v["reasons"][:3]:
                why.append(_s(r))
        else:
            what.append(_s(f"The submitted {m.modality} “{m.label}” shows {ex['headline'][:1].lower() + ex['headline'][1:]}.", [m.id]))
            for r in ex["reasons"]:
                why.append(_s(r["text"], r["claims"]))
        for fam in ex["generator_families"]:
            why.append(_s(f"Provenance/generator signal: {fam['family']} ({fam['status']}).", fam["claims"]))
        if ex["not_checked"]:
            why.append(_s("Not checked (tools unavailable — not a negative result): "
                          + ", ".join(n["component"] for n in ex["not_checked"]) + "."))
    fams = [f for f in gen.get("families", []) if len(f["members"]) > 1]
    copies = sum(len(f["members"]) for f in fams)
    plats = Counter(l.platform for l in locs)
    spread = []
    if locs:
        spread.append(_s(f"{len(locs)} location(s) recorded across {len(plats)} platform/site type(s): "
                         + ", ".join(f"{p} ({n})" for p, n in plats.most_common()) + ".", [l.id for l in locs]))
        states = Counter(l.state for l in locs)
        spread.append(_s("Current state: " + ", ".join(f"{n} {st}" for st, n in states.items()) + "."))
    if fams:
        spread.append(_s(f"{copies} media files form {len(fams)} family(ies) of the same underlying media, including "
                         "transformed copies (crops, mirrors, captions, re-encodes, screen recordings).",
                         [m for f in fams for m in f["members"]]))
    else:
        spread.append(_s("No related copies have been established yet."))
    leads = build_leads(s, case_id)
    connected = [_s(f"{l['subject']}: {l['suggests']}", [l["node"]]) for l in leads["leads"][:4]]
    proven = [_s(c.statement, [c.id]) for c in all_claims
              if c.status.value in ("Verified", "Corroborated") and c.category in
              ("capture", "account", "match", "c2pa", "provenance", "watermark", "archive", "time")][:12]
    seen: set[str] = set()
    lead_items = []
    for c in all_claims:
        if c.status.value == "Lead" and c.statement not in seen:
            seen.add(c.statement)
            lead_items.append(_s(c.statement, [c.id]))
    lead_items = lead_items[:10]
    gaps = analyse_gaps(s, case_id)
    missing = [_s(f"{g['question']} — next: {g['next_evidence']}", [g["id"]]) for g in gaps[:5]]
    na = next_action(s, case_id)
    return {
        "title": case.title,
        "sections": [
            {"key": "what", "title": "What happened", "items": what or [_s("No primary media analysed yet.")]},
            {"key": "why", "title": "Why we believe it", "items": why},
            {"key": "spread", "title": "Where it spread", "items": spread},
            {"key": "connected", "title": "Who / what is connected (leads, not findings)",
             "items": connected or [_s("No accounts or signatures connected yet.")]},
            {"key": "proven", "title": "What is proven (Verified / Corroborated)", "items": proven},
            {"key": "leads", "title": "What is only a lead", "items": lead_items},
            {"key": "missing", "title": "What evidence is missing", "items": missing},
            {"key": "next", "title": "What you should do now", "items": [_s(na["primary"]["title"] + " — " + na["primary"]["why"])]},
        ],
    }
