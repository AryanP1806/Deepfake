import io
import json

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageOps, PngImagePlugin
from sqlmodel import select

from dfir.analyzers.explain import explain
from dfir.core.claims import Claim, Polarity, Status, Strength, corroborate, lint_text, sanitize_text, validate_claim
from dfir.core.custody import CustodyLog, verify_chain
from dfir.core.db import init_db, load_claims, session
from dfir.core.models import AnalysisRun, Case
from dfir.genealogy import match as gm
from dfir.genealogy.fingerprint import image_fingerprint
from dfir.pipeline import analyze_media, ingest_media, run_genealogy
from dfir.propagation.adapters.search import ADAPTERS

init_db()


def scene(seed=3, w=900, h=700):
    rng = np.random.default_rng(seed)
    im = Image.new("RGB", (w, h), (90, 110, 150))
    d = ImageDraw.Draw(im)
    for _ in range(140):
        x, y, r = int(rng.integers(0, w)), int(rng.integers(0, h)), int(rng.integers(4, 50))
        d.ellipse([x - r, y - r, x + r, y + r], fill=tuple(int(c) for c in rng.integers(0, 255, 3)))
    return Image.fromarray(np.clip(np.asarray(im).astype(int) + rng.normal(0, 5, (h, w, 3)), 0, 255).astype(np.uint8))


def jpeg(im, q=85):
    b = io.BytesIO()
    im.convert("RGB").save(b, "JPEG", quality=q)
    return Image.open(io.BytesIO(b.getvalue()))


# ---------------------------------------------------------------- claims & language
def test_status_rules():
    a = Claim(stage="t", category="detector", statement="x", status=Status.INFERRED, produced_by="ufd@1")
    b = Claim(stage="t", category="detector", statement="y", status=Status.INFERRED, produced_by="ufd@1")
    c = Claim(stage="t", category="detector", statement="z", status=Status.INFERRED, produced_by="hive@1")
    assert corroborate([a, b]) == Status.INFERRED, "two outputs of the same model never corroborate"
    assert corroborate([a, c]) == Status.CORROBORATED
    bad = Claim(stage="t", category="x", statement="s", status=Status.CORROBORATED, produced_by="p", sources=["one"])
    assert validate_claim(bad)


def test_banned_language_lint():
    txt = "The video was created by @x, the perpetrator."
    assert lint_text(txt)
    assert not lint_text(sanitize_text(txt))


def test_explain_never_reports_authentic_from_absence():
    unknown = Claim(stage="d", category="c2pa", statement="No C2PA", status=Status.UNKNOWN, produced_by="c2pa@1")
    ex = explain([unknown])
    assert ex["level"] == "Inconclusive"
    assert "NOT a finding of authenticity" in ex["reasons"][0]["text"]


def test_detector_disagreement_is_inconclusive():
    pro = Claim(stage="d", category="detector", statement="a", status=Status.INFERRED, strength=Strength.MODERATE,
                polarity=Polarity.SUPPORTS_MANIPULATION, produced_by="ufd@1")
    con = Claim(stage="d", category="detector", statement="b", status=Status.INFERRED, strength=Strength.WEAK,
                polarity=Polarity.SUPPORTS_AUTHENTIC, produced_by="hive@1")
    assert explain([pro, con])["level"] == "Inconclusive"


# ---------------------------------------------------------------- custody
def test_custody_chain_detects_tampering(tmp_path):
    log = CustodyLog(tmp_path / "c.jsonl")
    for i in range(5):
        log.append("act", details={"i": i})
    lines = (tmp_path / "c.jsonl").read_text(encoding="utf-8").splitlines()
    assert verify_chain(lines)[0]
    e = json.loads(lines[2])
    e["actor"] = "mallory"
    tampered = lines[:2] + [json.dumps(e)] + lines[3:]
    assert not verify_chain(tampered)[0]
    assert not verify_chain(lines[:2] + lines[3:])[0], "deleting an entry breaks the chain"


# ---------------------------------------------------------------- matching robustness
@pytest.mark.parametrize("name,fn", [
    ("resize", lambda im: im.resize((450, 350))),
    ("recompress", lambda im: jpeg(im, 40)),
    ("mirror", lambda im: ImageOps.mirror(im)),
    ("crop", lambda im: im.crop((150, 100, 750, 550))),
    ("caption_bar", lambda im: _caption(im)),
    ("mirror_crop_jpeg", lambda im: jpeg(ImageOps.mirror(im.crop((100, 80, 800, 620))).resize((500, 385)), 60)),
])
def test_transformed_copies_match(name, fn):
    src = scene()
    v = fn(src)
    r = gm.compare_images(src, v, image_fingerprint(src), image_fingerprint(v))
    assert r.matched, name
    if name in ("crop", "caption_bar", "mirror", "mirror_crop_jpeg"):
        assert r.verified, f"{name} should be geometrically verified"
    if "mirror" in name:
        assert r.geometry.mirrored


def _caption(im):
    out = Image.new("RGB", (im.width, im.height + 120), (255, 255, 255))
    out.paste(im, (0, 120))
    ImageDraw.Draw(out).text((20, 40), "@someone shocking", fill=(0, 0, 0))
    return out


def test_crop_rectangle_recovered():
    src = scene()
    crop = src.crop((150, 100, 750, 550))
    g = gm.geometric_verify(src, crop)
    x0, y0, x1, y1 = g.b_in_a
    assert abs(x0 - 150 / 900) < 0.03 and abs(y0 - 100 / 700) < 0.03
    assert abs(x1 - 750 / 900) < 0.03 and abs(y1 - 550 / 700) < 0.03


def test_unrelated_images_do_not_match():
    a, b = scene(1), scene(99)
    r = gm.compare_images(a, b, image_fingerprint(a), image_fingerprint(b))
    assert not r.verified
    assert not r.matched


# ---------------------------------------------------------------- end-to-end analysis
def _bytes(im, fmt="JPEG", **kw):
    b = io.BytesIO()
    im.save(b, fmt, **kw)
    return b.getvalue()


def test_pipeline_absence_is_unknown_and_sd_parameters_verified():
    with session() as s:
        c = Case(title="t")
        s.add(c)
        s.commit()
        cid = c.id
        info = PngImagePlugin.PngInfo()
        info.add_text("parameters", "a portrait\nSteps: 20, Sampler: Euler a, CFG scale: 7, Seed: 1, Size: 512x512, Model: sd15")
        m1 = ingest_media(s, cid, _bytes(scene(5), "PNG", pnginfo=info), "gen.png")
        m2 = ingest_media(s, cid, _bytes(scene(5).crop((100, 100, 700, 600))), "copy.jpg", role="discovered")
        id1, id2 = m1.id, m2.id
    analyze_media(cid, id1)
    analyze_media(cid, id2)
    with session() as s:
        c1 = load_claims(s, cid, "decomposition", id1)
        assert any(c.category == "provenance" and c.status == Status.VERIFIED and c.strength == Strength.STRONG for c in c1)
        c2 = load_claims(s, cid, "decomposition", id2)
        meta = [c for c in c2 if c.category == "metadata"]
        assert meta and all(c.status == Status.UNKNOWN for c in meta), "stripped metadata must be Unknown, never clean"
        runs = s.exec(select(AnalysisRun).where(AnalysisRun.media_id == id2)).all()
        assert all(r.outcome in ("ok", "not_run") for r in runs), [(r.component, r.reason) for r in runs if r.outcome == "error"]
    tree = run_genealogy(cid)
    fam = [f for f in tree["families"] if len(f["members"]) == 2]
    assert fam and fam[0]["root"] == id1
    with session() as s:
        inherited = [c for c in load_claims(s, cid, "genealogy", id2) if c.category == "family_provenance"]
        assert inherited and inherited[0].status == Status.INFERRED


def test_keyed_adapters_report_not_run_without_keys():
    for a in ADAPTERS:
        if a.key_names:
            nr = a.available()
            assert nr is not None and "not configured" in nr.reason
