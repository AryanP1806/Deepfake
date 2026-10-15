"""Aggregate decomposition claims into an indicator level with a grounded explanation.

There is deliberately no single probability. The level reflects how many *independent*
lines of evidence point the same way and whether any contradict.
"""
from __future__ import annotations

from ..core.claims import STATUS_RANK, Claim, Polarity, Status, Strength

LEVELS = ("Strong", "Moderate", "Weak", "Inconclusive")


def _producer(c: Claim) -> str:
    return c.produced_by.split("@")[0]


def explain(claims: list[Claim], not_run: list[dict] | None = None) -> dict:
    pro = [c for c in claims if c.polarity == Polarity.SUPPORTS_MANIPULATION]
    con = [c for c in claims if c.polarity == Polarity.SUPPORTS_AUTHENTIC]
    provenance_hits = [c for c in pro if c.status == Status.VERIFIED and c.strength == Strength.STRONG
                       and c.category in ("c2pa", "provenance", "watermark")]
    family_hits = [c for c in pro if c.category == "family_provenance" and c.strength == Strength.STRONG]
    detector_pro = [c for c in pro if c.category == "detector" and c.strength in (Strength.MODERATE, Strength.STRONG)]
    detector_con = [c for c in con if c.category == "detector"]
    forensic_pro = [c for c in pro if c.category not in ("detector", "c2pa", "provenance", "watermark",
                                                         "family_provenance")]
    producers_pro = {_producer(c) for c in pro}

    reasons: list[dict] = []
    if provenance_hits:
        level = "Strong"
        reasons.append({"text": "Provenance data embedded in the file declares or proves AI generation.",
                        "claims": [c.id for c in provenance_hits]})
    elif family_hits:
        level = "Strong"
        reasons.append({"text": "This file is a verified copy of media whose earliest discovered version carries embedded "
                                "AI-generation provenance (the provenance was stripped from this copy during re-sharing).",
                        "claims": [c.id for c in family_hits]})
    elif detector_pro and detector_con:
        level = "Inconclusive"
        reasons.append({"text": "Independent detectors disagree — one flags the media, another does not.",
                        "claims": [c.id for c in detector_pro + detector_con]})
    elif len({_producer(c) for c in detector_pro}) >= 2:
        level = "Strong"
        reasons.append({"text": "Two or more independent detector families flag the media.",
                        "claims": [c.id for c in detector_pro]})
    elif detector_pro and forensic_pro:
        level = "Moderate"
        reasons.append({"text": "A learned detector flags the media and independent pixel-forensic indicators point "
                                "the same way.", "claims": [c.id for c in detector_pro + forensic_pro]})
    elif detector_pro:
        level = "Weak"
        reasons.append({"text": "A single learned detector flags the media; no independent line of evidence "
                                "corroborates it yet.", "claims": [c.id for c in detector_pro]})
    elif len(producers_pro) >= 2:
        level = "Weak"
        reasons.append({"text": "Several weak forensic indicators point toward manipulation, but none is conclusive.",
                        "claims": [c.id for c in pro]})
    else:
        level = "Inconclusive"
        reasons.append({"text": "No line of evidence clearly indicates manipulation. This is NOT a finding of "
                                "authenticity — available tools have known blind spots.", "claims": []})

    if con and level != "Inconclusive":
        reasons.append({"text": "Counter-evidence exists and must be weighed.", "claims": [c.id for c in con]})

    families: dict[str, dict] = {}
    for c in claims:
        fam = c.data.get("generator_family") if c.data else None
        if fam:
            f = families.setdefault(str(fam), {"family": str(fam), "status": c.status.value, "claims": []})
            f["claims"].append(c.id)
            if STATUS_RANK[c.status] > STATUS_RANK[Status(f["status"])]:
                f["status"] = c.status.value

    headline = {
        "Strong": "Strong manipulation/AI-generation indicators",
        "Moderate": "Moderate manipulation indicators",
        "Weak": "Weak manipulation indicators",
        "Inconclusive": "Inconclusive — no reliable indicator either way",
    }[level]
    return {
        "level": level,
        "headline": headline,
        "reasons": reasons,
        "supporting": [c.id for c in pro],
        "counter": [c.id for c in con],
        "unknowns": [c.id for c in claims if c.status == Status.UNKNOWN],
        "generator_families": list(families.values()),
        "not_checked": not_run or [],
        "caveat": "Detector outputs are evidence, not verdicts. Absence of metadata, watermarks or detector hits "
                  "does not establish authenticity.",
    }
