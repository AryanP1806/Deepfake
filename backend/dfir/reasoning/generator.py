"""AI generator attribution: "which family of generator probably made this?" — answered only as far as evidence goes.

Evidence kinds, from strongest to weakest, and how reliable each really is:

  signed        a VALID, signed C2PA manifest naming the generating tool. Cryptographic: proves who signed and that the
                bytes are unchanged since — not that the signer is honest.
  declared      statements written INTO the file: generation parameters (Stable Diffusion/ComfyUI/NovelAI/…), software
                fields, IPTC "trainedAlgorithmicMedia", an unvalidated C2PA manifest. Natural photos essentially never
                carry them, but anyone can add or remove them.
  watermark     a decoded open invisible watermark (the Stable Diffusion reference pipeline). Fragile (destroyed by
                resizing/compression) and the public reference payload can be embedded by anyone.
  classifier    a vendor classifier's guess of the generating family (Hive "source" classes). Statistical and closed.
  inherited     another, verified copy of the same media carried one of the above before the signal was stripped.

Absence of every signal means NOTHING about whether media is real: most generators sign nothing, and every platform
strips metadata. Proprietary watermarks (SynthID, Meta) have no public detector and are listed as "not checked".
"""
from __future__ import annotations

import re

from ..core.claims import Claim, Status

# canonical family  <-  regex over free text (software fields, C2PA agents, parameter chunks)
FAMILY_PATTERNS: list[tuple[str, str]] = [
    ("Stable Diffusion family", r"stable[ -]?diffusion|\bsdxl\b|\bsd ?(1|2|3)\b|automatic1111|a1111|invokeai|fooocus|stability ai|dreamstudio|deepfloyd"),
    ("ComfyUI workflow (a pipeline tool: the underlying model is not identified)", r"comfy ?ui"),
    ("Midjourney", r"midjourney|\bmj\b"),
    ("OpenAI (DALL·E / GPT-Image / Sora)", r"dall[-·. ]?e|openai|gpt[- ]?image|gpt-?4o|chatgpt|\bsora\b"),
    ("Microsoft Designer / Bing Image Creator (OpenAI models)", r"bing image creator|microsoft designer|\bdesigner\b|copilot"),
    ("Adobe Firefly", r"firefly"),
    ("Google (Imagen / Gemini / Veo)", r"imagen|gemini|\bveo\b|nano ?banana|google ai|synthid"),
    ("FLUX (Black Forest Labs)", r"\bflux"),
    ("Leonardo.Ai", r"leonardo"), ("Ideogram", r"ideogram"), ("NovelAI", r"novelai"), ("Runway", r"runway"),
    ("Kling", r"kling"), ("Pika", r"\bpika\b"), ("Luma", r"\bluma\b"), ("Hailuo / MiniMax", r"hailuo|minimax"),
    ("xAI (Grok / Aurora)", r"grok|aurora"), ("Krea", r"\bkrea\b"), ("Playground", r"playground"),
    ("Alibaba (Qwen / Wan / Z-Image)", r"qwen|\bwan\b|z-?image"), ("ByteDance (Seedream / Seedance)", r"seedream|seedance|doubao"),
    ("Tencent (Hunyuan)", r"hunyuan"), ("Recraft", r"recraft"),
]
GENERIC_FAMILIES = {"declared AI source"}

# Hive "source" classes -> canonical family (classes that name no vendor map to a generic bucket)
HIVE_FAMILY = {
    "midjourney": "Midjourney", "dalle": "OpenAI (DALL·E / GPT-Image / Sora)", "4o": "OpenAI (DALL·E / GPT-Image / Sora)",
    "gptimage1_5": "OpenAI (DALL·E / GPT-Image / Sora)", "gptimage2": "OpenAI (DALL·E / GPT-Image / Sora)",
    "sora": "OpenAI (DALL·E / GPT-Image / Sora)", "sora2": "OpenAI (DALL·E / GPT-Image / Sora)",
    "bingimagecreator": "Microsoft Designer / Bing Image Creator (OpenAI models)",
    "stablediffusion": "Stable Diffusion family", "stablediffusionxl": "Stable Diffusion family", "sdxlinpaint": "Stable Diffusion family",
    "stablediffusioninpaint": "Stable Diffusion family", "stablecascade": "Stable Diffusion family", "deepfloyd": "Stable Diffusion family",
    "flux": "FLUX (Black Forest Labs)", "flux2": "FLUX (Black Forest Labs)", "flux3": "FLUX (Black Forest Labs)",
    "imagen": "Google (Imagen / Gemini / Veo)", "imagen4": "Google (Imagen / Gemini / Veo)", "gemini": "Google (Imagen / Gemini / Veo)",
    "gemini3": "Google (Imagen / Gemini / Veo)", "veo3": "Google (Imagen / Gemini / Veo)",
    "adobefirefly": "Adobe Firefly", "leonardo": "Leonardo.Ai", "ideogram": "Ideogram", "runway": "Runway", "kling": "Kling",
    "pika": "Pika", "luma": "Luma", "hailuo": "Hailuo / MiniMax", "grok": "xAI (Grok / Aurora)", "grokimagine": "xAI (Grok / Aurora)",
    "krea": "Krea", "recraft": "Recraft", "qwen": "Alibaba (Qwen / Wan / Z-Image)", "wan": "Alibaba (Qwen / Wan / Z-Image)",
    "zimage": "Alibaba (Qwen / Wan / Z-Image)", "seedream": "ByteDance (Seedream / Seedance)", "seedance": "ByteDance (Seedream / Seedance)",
    "hunyuan": "Tencent (Hunyuan)", "gan": "Older GAN-style generator (e.g. StyleGAN-type faces)",
}
UNSPECIFIC = {"other_image_generators": "A generator the classifier does not name"}

GROUP_OF = {"signed": "provenance", "declared": "embedded", "watermark": "watermark", "classifier": "classifier", "inherited": "inherited"}
RELIABILITY = {
    "signed": ("Cryptographically signed", "Proves who signed the record and that the file is unchanged since. The signer's statements are only as honest as the signer."),
    "declared": ("Written in the file", "Natural photos almost never carry this, but anyone can add, copy or remove it."),
    "watermark": ("Decoded invisible watermark", "Fragile (resizing and compression destroy it) and the public reference payload can be embedded by anyone."),
    "classifier": ("Statistical guess by a closed model", "A probability from a commercial classifier. Not independently reproducible; it can be wrong, and new generators are often missed."),
    "inherited": ("Found on another verified copy", "An earlier copy of the same media carried this signal before it was stripped. Rests on the copy match being right."),
}
NOT_CHECKED = [
    {"name": "Google SynthID / Meta / TikTok invisible watermarks", "why": "No public detector exists. Use the vendor's own verification tool where one is offered."},
    {"name": "Generator-specific model fingerprints (spectral/architecture traces)", "why": "No validated open detector is bundled; the only model-specific signal used is Hive's source classifier, when Hive ran."},
    {"name": "Prompt / account / usage records", "why": "Held by the generator's provider; obtainable only through a lawful request."},
]


def canon(text: str | None) -> str | None:
    if not text:
        return None
    t = str(text).lower()
    for fam, pat in FAMILY_PATTERNS:
        if re.search(pat, t):
            return fam
    return None


def _atoms(claims: list[Claim]) -> tuple[list[dict], dict]:
    """Turn claims into evidence atoms {family, kind, text, claim_id, detail}. Also returns what was checked."""
    atoms: list[dict] = []
    checked = {"c2pa": "not found", "embedded": "not found", "watermark": "not found", "classifier": "did not run", "software": "not found"}
    for c in claims:
        d = c.data or {}
        fam_raw = d.get("generator_family")
        if c.category == "c2pa":
            if c.statement.startswith("C2PA manifest present"):
                valid = c.status == Status.VERIFIED
                checked["c2pa"] = "valid signature" if valid else "present but did not validate"
                gen = d.get("generator") or ""
                f = canon(gen)
                if f:
                    atoms.append({"family": f, "kind": "signed" if valid else "declared", "claim_id": c.id,
                                  "text": f"C2PA manifest names the claim generator “{gen}” (signed by {d.get('issuer') or 'unknown'}; validation: {d.get('state')})"})
            elif d.get("digitalSourceType"):
                valid = c.status == Status.VERIFIED
                f = canon(fam_raw) or canon(d.get("softwareAgent"))
                if f:
                    atoms.append({"family": f, "kind": "signed" if valid else "declared", "claim_id": c.id,
                                  "text": f"C2PA action declares AI-generated content, software agent “{d.get('softwareAgent') or fam_raw}”"})
                else:
                    atoms.append({"family": "an AI generator (not named)", "kind": "signed" if valid else "declared", "claim_id": c.id,
                                  "text": "C2PA declares the content AI-generated but does not name which tool"})
        elif c.category == "provenance" and (fam_raw or d.get("digitalSourceType")):
            checked["embedded"] = "found"
            f = canon(fam_raw) or ("an AI generator (not named)" if d.get("digitalSourceType") else None)
            if f:
                atoms.append({"family": f, "kind": "declared", "claim_id": c.id, "text": c.statement})
        elif c.category == "watermark" and fam_raw:
            checked["watermark"] = "decoded"
            atoms.append({"family": canon(fam_raw) or "Stable Diffusion family", "kind": "watermark", "claim_id": c.id, "text": c.statement})
        elif c.category == "watermark" and "No open" in c.statement:
            checked["watermark"] = "none found (open watermarks only)"
        elif c.category == "metadata" and d.get("field") and d.get("value") and GENERATOR_FIELD.search(str(d["value"])):
            checked["software"] = "names a tool"
            f = canon(d["value"])
            if f:
                atoms.append({"family": f, "kind": "declared", "claim_id": c.id, "text": f"{d['field']} field says “{d['value']}”"})
        elif c.category == "detector" and d.get("vendor") == "Hive":
            checked["classifier"] = "ran (Hive)"
            for cls, p in (d.get("source_probs") or {}).items():
                fam = HIVE_FAMILY.get(cls)
                if fam and p >= 0.30:
                    atoms.append({"family": fam, "kind": "classifier", "claim_id": c.id, "prob": p,
                                  "text": f"Hive's source classifier puts {p:.0%} on “{cls}”"})
                elif cls in UNSPECIFIC and p >= 0.5:
                    atoms.append({"family": UNSPECIFIC[cls], "kind": "classifier", "claim_id": c.id, "prob": p,
                                  "text": f"Hive says it is AI-generated by a generator it does not name ({p:.0%})"})
        elif c.category == "family_provenance" and fam_raw:
            atoms.append({"family": canon(fam_raw) or str(fam_raw), "kind": "inherited", "claim_id": c.id,
                          "text": c.statement[:240]})
    return atoms, checked


GENERATOR_FIELD = re.compile("|".join(p for _, p in FAMILY_PATTERNS), re.I)


def _tier(groups: set[str], kinds: set[str]) -> str:
    if "signed" in kinds or len(groups) >= 3:
        return "established"
    if len(groups) >= 2 or groups & {"embedded", "watermark", "provenance"}:
        return "supported"
    return "suggested"


TIER_TEXT = {
    "established": "established — a signed record, or three independent kinds of evidence agree",
    "supported": "supported — written into the file or independently corroborated, but forgeable",
    "suggested": "suggested only — a single statistical or inherited signal",
}


def generator_attribution(claims: list[Claim], p_ai: float | None = None) -> dict:
    atoms, checked = _atoms(claims)
    named = [a for a in atoms if a["family"] not in ("an AI generator (not named)",) and a["family"] not in UNSPECIFIC.values()
             and not a["family"].startswith("Older GAN")]
    by_fam: dict[str, list[dict]] = {}
    for a in atoms:
        by_fam.setdefault(a["family"], []).append(a)
    cands = []
    for fam, items in by_fam.items():
        kinds = {i["kind"] for i in items}
        groups = {GROUP_OF[k] for k in kinds}
        cands.append({"family": fam, "tier": _tier(groups, kinds), "kinds": sorted(kinds), "evidence": [
            {**i, "label": RELIABILITY[i["kind"]][0], "reliability": RELIABILITY[i["kind"]][1]} for i in items],
            "named": fam in {n["family"] for n in named}})
    order = {"established": 0, "supported": 1, "suggested": 2}
    cands.sort(key=lambda c: (order[c["tier"]], -len(c["evidence"])))

    ai_declared = any(c.data.get("digitalSourceType") for c in claims if c.category in ("c2pa", "provenance"))
    named_c = [c for c in cands if c["named"]]
    top = named_c[0] if named_c else None
    conflict = None
    if len(named_c) >= 2 and order[named_c[0]["tier"]] == order[named_c[1]["tier"]]:
        # two families supported to the same degree: say so rather than pick one
        conflict = [c["family"] for c in named_c if order[c["tier"]] == order[named_c[0]["tier"]]]
        # a ComfyUI workflow + a model family are not in conflict: the tool runs the model
        if set(conflict) <= {n["family"] for n in named_c} and any(f.startswith("ComfyUI") for f in conflict) and len(conflict) == 2:
            conflict = None
    if conflict:
        possible, headline = None, "The evidence points to more than one generator family — no single family can be named"
    elif top:
        possible = top["family"]
        headline = f"Possible generator family: {top['family']} — {TIER_TEXT[top['tier']]}"
    elif cands:
        possible, headline = None, "The media appears to be AI-generated, but no specific generator can be named"
    else:
        possible, headline = None, "No generator could be attributed"

    cautions = [
        "A generator family is a lead about HOW the media was made. It does not show who made it or who posted it.",
        "No watermark, metadata or classifier hit does not mean the media is real: most generators sign nothing and every "
        "platform strips metadata.",
    ]
    if conflict:
        cautions.insert(0, "Conflicting signals: " + " vs ".join(conflict) + ". One of them may be forged, mislabelled or wrong.")
    if top and top["tier"] == "suggested":
        cautions.insert(0, "Only a single statistical signal supports this. Treat it as a pointer for further checks, not as a finding.")
    if p_ai is not None and p_ai < 0.35 and (top or ai_declared):
        cautions.insert(0, "The detectors do NOT think this image is AI-generated, yet a signal names a generator: the metadata may "
                           "have been copied onto a real photo, or the detectors missed it. Do not treat either as settled.")
    next_steps = []
    if not cands:
        next_steps += ["Look for an earlier copy of the media: the earliest copy is the most likely to still carry a manifest or metadata.",
                       "If Hive has not run, run it: it is the only source classifier available."]
    else:
        next_steps += ["Find the earliest copy of this media (Where it appeared): provenance data survives best there.",
                       "If the signal is only declared, test it: re-generate with the stated model/seed where the model is available."]
        if any(a["kind"] == "classifier" for a in atoms):
            next_steps.append("Ask the generator's provider (through the police) whether the media was created on their service.")

    return {"possible_family": possible, "headline": headline, "tier": top["tier"] if (top and not conflict) else None,
            "conflict": conflict, "candidates": cands, "checked": checked, "not_checked": NOT_CHECKED, "cautions": cautions,
            "next_steps": next_steps, "reliability_scale": {k: {"label": v[0], "meaning": v[1]} for k, v in RELIABILITY.items()}}
