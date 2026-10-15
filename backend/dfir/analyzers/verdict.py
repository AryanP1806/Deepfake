"""The end-user verdict: one plain answer + a percentage, built from detector scores.

Two questions are answered separately and then combined:
  * p_ai   — is the picture AI-generated?
  * p_edit — was a face swapped / part of it edited?
  p_fake = 1 − (1 − p_ai)(1 − p_edit)

PRIMARY engine — Hive (when a Hive key answered): p_ai = Hive ai_generated, p_edit = Hive deepfake.
SECONDARY engine — local detectors (always computed; they decide the verdict only when Hive did not run
or every Hive key failed): p_ai from SPAI [+ SBI] stacked & calibrated on OpenFake; p_edit from SBI's
percentile against real faces, and TruFor when it is far above what real photos produce.
Verified provenance (C2PA "AI" claim, SD parameters, decoded watermark) overrides both.

The calibration file (models/calibration.json) is produced by scripts/calibrate.py from public
data that never overlaps the user's test images. Without it, model outputs are used as-is and the
verdict says "uncalibrated".
"""
from __future__ import annotations

import json
import math
from functools import lru_cache

from ..core.claims import Claim, Polarity, Status, Strength
from ..core.config import MODELS_DIR

CALIBRATION_FILE = MODELS_DIR / "calibration.json"
HIVE_EVAL_FILE = MODELS_DIR / "hive_eval.json"  # Hive measured on held-out OpenFake test images

LABELS = [  # (min p_fake, key, headline)
    (0.85, "fake", "Fake"),
    (0.65, "likely_fake", "Likely fake"),
    (0.35, "uncertain", "Uncertain"),
    (0.15, "likely_real", "Likely real"),
    (0.0, "real", "Real"),
]


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _sig(x: float) -> float:
    return 1 / (1 + math.exp(-x))


@lru_cache(maxsize=4)
def _json_cached(path: str, mtime: float) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _load(path) -> dict | None:
    try:
        return _json_cached(str(path), path.stat().st_mtime)
    except OSError:
        return None


def calibration() -> dict | None:
    return _load(CALIBRATION_FILE)


def hive_eval() -> dict | None:
    return _load(HIVE_EVAL_FILE)


def _percentile(table: list[float], v: float) -> float:
    """Fraction of the reference distribution (table = quantiles at 0, 2, ..., 100 %) below v."""
    n = len(table) - 1
    if v <= table[0]:
        return 0.0
    if v >= table[-1]:
        return 1.0
    for i in range(n):
        lo, hi = table[i], table[i + 1]
        if lo <= v <= hi:
            frac = 0.0 if hi == lo else (v - lo) / (hi - lo)
            return (i + frac) / n
    return 1.0


def features_from_claims(claims: list[Claim]) -> dict:
    f: dict = {"provenance_proof": [], "screenshot": False, "small": False}
    for c in claims:
        d = c.data or {}
        model = str(d.get("model", ""))
        if c.category == "detector" and model == "SPAI":
            f["spai"] = d.get("score")
            f["small"] = f["small"] or bool(d.get("small_input"))
        elif c.category == "detector" and model.startswith("UniversalFakeDetect") and d.get("score") is not None:
            f["ufd"] = d.get("score")
        elif c.category == "tamper" and model == "TruFor":
            f["trufor"] = d.get("score")
            f["trufor_area"] = d.get("suspect_area")
        elif c.category == "faceswap" and str(model).startswith("SBI"):
            f["sbi"] = d.get("max_score")
            f["sbi_faces"] = len(d.get("faces", []))
        elif c.category == "detector" and d.get("vendor") == "Hive" and d.get("score") is not None:
            f["hive_ai"] = d.get("score")
            if d.get("generator_family"):
                f["hive_source"] = d["generator_family"]
        elif c.category == "faceswap" and d.get("vendor") == "Hive":
            f["hive_deepfake"] = d.get("score")
        elif c.category == "screenshot":
            f["screenshot"] = True
        if (c.status == Status.VERIFIED and c.strength == Strength.STRONG and c.polarity == Polarity.SUPPORTS_MANIPULATION
                and c.category in ("c2pa", "provenance", "watermark")):
            f["provenance_proof"].append(c.statement)
        if c.category == "family_provenance" and c.strength == Strength.STRONG:
            f["provenance_proof"].append(c.statement)
    return f


def _ai_from_features(f: dict, cal: dict | None) -> tuple[float | None, bool]:
    """Calibrated p(AI-generated) from local detectors. Returns (p, calibrated?)."""
    if f.get("spai") is None:
        return None, False
    head = (cal or {}).get("ai_head")
    if head:
        x = {"spai_logit": _logit(f["spai"]),
             "sbi_logit": _logit(f["sbi"]) if f.get("sbi") is not None else 0.0,
             "has_face": 1.0 if f.get("sbi") is not None else 0.0,
             "trufor_logit": _logit(f["trufor"]) if f.get("trufor") is not None else 0.0}
        z = head["intercept"] + sum(w * x.get(name, 0.0) for name, w in zip(head["features"], head["coef"]))
        return _sig(z), True
    return float(f["spai"]), False


def _local_heads(f: dict, cal: dict | None) -> dict:
    """The secondary (local) engine: calibrated p_ai / p_edit from SPAI, SBI and TruFor."""
    p_ai, calibrated = _ai_from_features(f, cal)
    edit_parts: list[float] = []
    sbi_pct = None
    if f.get("sbi") is not None:
        table = ((cal or {}).get("sbi_real_faces") or {}).get("table")
        if table:
            # SBI fires on ~half of ordinary real faces at 0.5, so its raw output is not a probability.
            # Use how unusual the face is compared with real faces: 0 below the 90th percentile, rising to
            # ~0.95 at the very top of the real-face distribution.
            sbi_pct = _percentile(table, float(f["sbi"]))
            edit_parts.append(0.95 * max(0.0, (sbi_pct - 0.90) / 0.10))
        else:
            edit_parts.append(float(f["sbi"]))
    p_edit = sum(edit_parts) / len(edit_parts) if edit_parts else None
    tq = ((cal or {}).get("trufor_real_quantiles") or {}).get("q99")
    trufor_outlier = f.get("trufor") is not None and tq is not None and f["trufor"] > tq
    if trufor_outlier:
        p_edit = max(p_edit or 0.0, 0.7)
    p_fake = None
    if p_ai is not None or p_edit is not None:
        p_fake = 1 - (1 - (p_ai or 0.0)) * (1 - (p_edit or 0.0))
    return {"p_ai": p_ai, "p_edit": p_edit, "p_fake": p_fake, "calibrated": calibrated, "sbi_pct": sbi_pct,
            "edit_parts": edit_parts, "trufor_outlier": trufor_outlier}


def _label(p_fake: float) -> tuple[str, str]:
    return next((k, h) for t, k, h in LABELS if p_fake >= t)


def build_verdict(claims: list[Claim], not_run: list[dict] | None = None) -> dict:
    cal = calibration()
    f = features_from_claims(claims)
    loc = _local_heads(f, cal)
    use_hive = f.get("hive_ai") is not None or f.get("hive_deepfake") is not None
    hive_failed = next((n.get("reason", "") for n in (not_run or [])
                        if n.get("component") == "api_hive" and "unavailable" in (n.get("reason") or "")), None)
    proof = bool(f["provenance_proof"])

    if use_hive:
        p_ai, p_edit = f.get("hive_ai"), f.get("hive_deepfake")
        calibrated = True  # Hive's own scores; measured on held-out data when hive_eval.json exists
    else:
        p_ai, p_edit, calibrated = loc["p_ai"], loc["p_edit"], loc["calibrated"]
    if proof:
        p_ai = max(p_ai or 0.0, 0.99)

    if p_ai is None and p_edit is None:
        missing = [n["component"] for n in (not_run or []) if n["component"] in ("spai", "sbi", "trufor", "api_hive")]
        return {"available": False, "label": "not_analysed", "headline": "Not enough detectors ran to give a verdict",
                "detail": "Install the detection models (Settings) or wait for the analysis to finish.",
                "missing": missing}

    pa, pe = (p_ai or 0.0), (p_edit or 0.0)
    p_fake = 1 - (1 - pa) * (1 - pe)
    key, headline = _label(p_fake)
    if proof:
        key, headline = "fake", "Fake"

    # --- what kind of fake
    kind = None
    if p_fake >= 0.5:
        kind = "AI-generated image" if pa >= pe else "Face swap / edited photo"
    lean = "leaning fake" if p_fake >= 0.5 else "leaning real"
    side = p_fake >= 0.5

    # --- second opinion (local detectors) when Hive decided
    second = None
    if use_hive and loc["p_fake"] is not None:
        lp = loc["p_fake"]
        lkey, lhead = _label(lp)
        second = {"engine": "local", "label": lkey, "headline": lhead, "p_fake": round(lp, 4),
                  "agrees": (lp >= 0.5) == side,
                  "strong_disagreement": (lp >= 0.85 and not side) or (lp < 0.15 and side)}

    # --- confidence: strength + independent agreement + input quality
    penalties = []
    if f["screenshot"]:
        penalties.append("it is a screenshot (re-rendered copy)")
    if f["small"]:
        penalties.append("the picture is very small")
    if proof:
        confidence = "high"
    elif 0.35 <= p_fake < 0.65:
        confidence = "low"
    elif use_hive:
        if second and second["strong_disagreement"]:
            confidence = "low"
        elif (p_fake >= 0.9 or p_fake <= 0.1) and not penalties and not (second and not second["agrees"]):
            confidence = "high"
        else:
            confidence = "medium"
    else:
        # Independent "votes" on the final side, using calibrated quantities (raw SBI fires on half of real faces).
        signals = []
        if loc["p_ai"] is not None:
            signals.append(loc["p_ai"])
        if loc["edit_parts"]:
            signals.append(loc["edit_parts"][0])
        strong_agree = sum(1 for v in signals if (v >= 0.75 if side else v <= 0.25))
        confidence = "high" if (p_fake >= 0.9 or p_fake <= 0.1) and strong_agree >= 2 and not penalties else "medium"

    # --- plain-language reasons (most decisive first)
    reasons: list[str] = []
    if proof:
        reasons.append("Proof in the file: " + f["provenance_proof"][0].rstrip(".") + ".")
    if use_hive:
        if f.get("hive_ai") is not None:
            s = f["hive_ai"]
            src = f" — most likely made with {f['hive_source'].split(' (')[0]}" if f.get("hive_source") else ""
            reasons.append(f"Hive's AI-image detector rates it {s:.0%} AI-generated{src}." if s >= 0.5 else
                           f"Hive's AI-image detector found no sign of an AI image generator ({s:.0%} AI-generated).")
        if f.get("hive_deepfake") is not None:
            d = f["hive_deepfake"]
            reasons.append(f"Hive's deepfake check flags a face swap ({d:.0%})." if d >= 0.5 else
                           f"Hive's deepfake check found no face swap ({d:.0%}).")
        if second:
            pct = min(99, max(1, round(second["p_fake"] * 100)))
            if second["agrees"]:
                reasons.append(f"Second opinion — our local detectors agree: {second['headline']} ({pct}% likely fake).")
            else:
                why = ""
                if second["p_fake"] >= 0.5:
                    if (loc["p_edit"] or 0) >= (loc["p_ai"] or 0) and loc["sbi_pct"] is not None:
                        why = f"; a face looks more blended than {loc['sbi_pct']:.0%} of real faces"
                    elif loc["trufor_outlier"]:
                        why = "; the edit detector (TruFor) highlights regions that don't match the rest"
                    elif f.get("spai") is not None:
                        why = f"; the AI-image detector SPAI scores it {f['spai']:.2f}"
                reasons.append(f"Second opinion disagrees — our local detectors say {second['headline']} "
                               f"({pct}% likely fake{why}).")
    else:
        if f.get("spai") is not None:
            s = f["spai"]
            reasons.append(
                f"The AI-image detector (SPAI) found frequency patterns typical of image generators (score {s:.2f})." if s >= 0.5 else
                f"The AI-image detector (SPAI) found the frequency patterns of a normal camera photo (score {s:.2f}).")
        sbi_pct = loc["sbi_pct"]
        if f.get("sbi") is not None and sbi_pct is not None:
            s = f["sbi"]
            if sbi_pct >= 0.95:
                reasons.append(f"A face looks more blended than {sbi_pct:.0%} of real faces we tested — a typical "
                               f"face-swap trace (face-swap detector {s:.2f}).")
            else:
                reasons.append(f"The faces look like ordinary real faces to the face-swap detector "
                               f"(more unusual than only {sbi_pct:.0%} of real faces).")
        elif f.get("sbi") is not None:
            s = f["sbi"]
            if s >= 0.5 and pa >= 0.65:
                reasons.append(f"The face also triggers the face-swap detector ({s:.2f}) — common for AI-generated faces.")
            elif s >= 0.5:
                reasons.append(f"A face shows blending traces typical of face swaps (face-swap detector {s:.2f}).")
            else:
                reasons.append(f"No face-swap blending traces were found on the {f.get('sbi_faces', 1)} face(s) "
                               f"(face-swap detector {s:.2f}).")
        if loc["trufor_outlier"]:
            reasons.append("The edit detector (TruFor) highlights regions whose camera noise does not match the rest.")
    if f["screenshot"]:
        reasons.append("This is a screenshot of a post — we analysed the picture inside it, not the app around it.")

    caveats = ["A verdict says how the picture was made, not who made it or whether the event it shows happened."]
    if not calibrated and p_ai is not None and not proof:
        caveats.append("Scores are not yet calibrated on test data — treat the percentage as approximate.")
    if second and second["strong_disagreement"]:
        caveats.append("Hive and the local detectors strongly disagree. Hive decides the verdict, but check the "
                       "evidence below before acting on it.")
    if not use_hive and hive_failed:
        caveats.append("Hive was unavailable for this file (every configured key failed), so this verdict comes "
                       "from the local detectors.")
    if penalties:
        caveats.append("Lower confidence because " + " and ".join(penalties) + ".")

    reliability = None
    if use_hive:
        he = hive_eval()
        if he:
            reliability = (f"Hive was tested here on {he['n']:,} held-out images from recent AI generators and real "
                           f"photo sources: right {he['accuracy']:.0%} of the time — real photos called real "
                           f"{he['real_correct']:.0%}, fakes caught {he['fake_correct']:.0%}.")
        else:
            reliability = "Hive is a commercial detector; its accuracy on images like this has not been measured here."
    elif cal and (cal.get("verdict_eval") or cal.get("eval")):
        e = cal.get("verdict_eval") or cal["eval"]
        reliability = (f"Tested on {e['n']:,} images from AI generators and photo sources it had never seen: right "
                       f"{e['accuracy']:.0%} of the time overall"
                       + (f" — real photos called real {e['real_correct']:.0%}, fakes caught {e['fake_correct']:.0%}"
                          if "real_correct" in e else "")
                       + (f"; when the verdict is not 'Uncertain' it was right {e['accuracy_when_not_uncertain']:.0%}"
                          if e.get("accuracy_when_not_uncertain") is not None else "") + ".")

    detectors = []
    for name, what, val, head, primary in (
        ("Hive", "AI-generated (primary)", f.get("hive_ai"), "ai", True),
        ("Hive deepfake", "Face swap (primary)", f.get("hive_deepfake"), "edit", True),
        ("SPAI", "AI-generated image", f.get("spai"), "ai", False),
        ("SBI", "Face swap", (loc["sbi_pct"] if loc["sbi_pct"] is not None else f.get("sbi")), "edit", False),
        ("TruFor", "Local editing", f.get("trufor"), "edit", False),
        ("UFD (legacy)", "AI-generated (GAN-era)", f.get("ufd"), "ai", False),
    ):
        if val is not None:
            # an edit/face-swap detector answers a different question when the picture is fully AI-generated
            relevant = not (head == "edit" and side and kind == "AI-generated image" and val < 0.5)
            detectors.append({"name": name, "checks": what, "score": round(float(val), 4), "head": head,
                              "primary": primary,
                              "agrees": ((val >= 0.5) == side) if relevant else None})

    return {
        "available": True,
        "label": key, "headline": headline,
        "kind": kind,
        "p_fake": round(p_fake, 4), "p_ai": None if p_ai is None else round(p_ai, 4),
        "p_edit": None if p_edit is None else round(p_edit, 4),
        "lean": lean, "confidence": confidence,
        "calibrated": calibrated or proof,
        "proof": proof,
        "engine": "hive" if use_hive else "local",
        "engine_note": ("Decided by Hive (commercial detector). Local detectors give the second opinion." if use_hive else
                        "Decided by the local detectors — Hive was unavailable." if hive_failed else
                        "Decided by the local detectors."),
        "second_opinion": second,
        "reasons": reasons[:4],
        "caveats": caveats,
        "reliability": reliability,
        "detectors": detectors,
        "screenshot": f["screenshot"],
        "summary": _summary(key, kind, p_fake, lean, proof),
    }


def _summary(key: str, kind: str | None, p: float, lean: str, proof: bool = False) -> str:
    pct = round(p * 100) if proof else min(99, max(1, round(p * 100)))
    if key == "fake":
        return f"This is very likely fake ({kind or 'manipulated'}) — {pct}% likely fake."
    if key == "likely_fake":
        return f"This is probably fake ({kind or 'manipulated'}) — {pct}% likely fake."
    if key == "uncertain":
        return f"We can't tell reliably — {lean} ({pct}% likely fake). Get a second opinion before acting on it."
    if key == "likely_real":
        return f"This is probably a real photo — {100 - pct}% likely real. No strong signs of AI generation or editing."
    return f"This looks like a real photo — {100 - pct}% likely real. No signs of AI generation or editing were found."
