"""Device/source report, PRNU, generator attribution and the lead engine."""
import io
import os
from datetime import datetime, timedelta, timezone

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter

from dfir.core.claims import Claim, Polarity, Status, Strength, lint_text
from dfir.core.db import init_db, save_claims, session, vault_for
from dfir.core.models import Case, Location, MatchEdge, Media
from dfir.forensics import prnu
from dfir.reasoning.generator import canon, generator_attribution
from dfir.reasoning.lead_engine import build_lead_engine
from dfir.reasoning.source import device_report

init_db()
T0 = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------------------------------------ helpers
def claim(cat, text, status=Status.VERIFIED, strength=Strength.MODERATE, **data):
    return Claim(stage="decomposition", category=cat, statement=text, status=status, strength=strength, produced_by="test@1",
                 polarity=Polarity.SUPPORTS_MANIPULATION if cat in ("c2pa", "provenance", "watermark") else Polarity.NEUTRAL, data=data)


def new_case(**kw) -> str:
    with session() as s:
        c = Case(title="t", **kw)
        s.add(c)
        s.commit()
        return c.id


def add_media(cid, role="discovered", w=1024, h=1024, fp=None) -> str:
    with session() as s:
        m = Media(case_id=cid, sha256=os.urandom(32).hex(), modality="image", role=role, label=f"{role}-{os.urandom(2).hex()}",
                  width=w, height=h, size=w * h, mime="image/jpeg", fingerprints=fp or {"x": 1})
        s.add(m)
        s.commit()
        return m.id


def add_post(cid, handle, minutes, caption, media_ids, platform="X (Twitter)", seed=False, declared=True, display="") -> str:
    with session() as s:
        l = Location(case_id=cid, url=f"https://x.test/{handle}/status/{os.urandom(3).hex()}", platform=platform, domain="x.test",
                     account_handle=handle, account_display=display, caption=caption, state="live", media_ids=list(media_ids), seed=seed,
                     published_at=(T0 + timedelta(minutes=minutes)) if declared else None,
                     published_source="page metadata (declared by the site)" if declared else "",
                     first_captured_at=T0 + timedelta(days=5), discovered_via="manual")
        s.add(l)
        s.commit()
        return l.id


def derive(cid, a, b, op="crop", ds=2.5, verified=True):
    """b is a verified altered copy of a (direction inferred a -> b)."""
    with session() as s:
        s.add(MatchEdge(case_id=cid, a_media=a, b_media=b, verified=verified, tier="geometric",
                        transforms={"direction_score": ds, "transforms_b_from_a": [{"op": op, "kept_region_of_a": [0, 0, 0.6, 0.6]}] if op == "crop" else [{"op": op, "text": ["lol"]}],
                                    "transforms_a_from_b": []}))
        s.commit()


def scenario(identity_clue=False):
    """Accounts A, B, C, D share one fake. A first; B copies A's caption and link 8 min later with a crop; C overlays text 3 h later; D re-posts A's file."""
    cid = new_case()
    m0 = add_media(cid, "primary")
    m1, m2 = add_media(cid, w=700, h=700), add_media(cid)
    derive(cid, m0, m1, "crop")
    derive(cid, m0, m2, "text_overlay_added")
    cap = "BREAKING: leaked photo shows the minister at the party #leak #viral #news http://news.example/story?utm=1"
    add_post(cid, "alice_news", 0, cap, [m0], seed=True)
    add_post(cid, "bob_reposts", 8, cap + " (via alice)" if False else cap, [m1])
    add_post(cid, "carol_memes", 180, "lol look at this", [m2])
    add_post(cid, "dave_other", 30 * 60, "Is this real?? thoughts", [m0])
    if identity_clue:
        with session() as s:
            save_claims(s, [Claim(case_id=cid, media_id=m1, stage="decomposition", category="ocr", statement="handle read",
                                  status=Status.INFERRED, produced_by="ocr@1", data={"handle": "alice_news"})])
            s.commit()
    return cid, (m0, m1, m2)


def engine(cid):
    with session() as s:
        return build_lead_engine(s, cid)


def by_type(out, t):
    return [l for l in out["leads"] if l["type"] == t]


# ------------------------------------------------------------------------------------------------ lead engine
def test_first_known_uploader_is_reported_with_its_basis_and_limits():
    cid, _ = scenario()
    out = engine(cid)
    fu = by_type(out, "first_uploader")
    assert len(fu) == 1 and "alice_news" in fu[0]["headline"] and fu[0]["confidence"] == "moderate"
    assert "eight minutes" not in fu[0]["confidence_reason"] and "8 minutes" in fu[0]["confidence_reason"]
    assert any("earlier post" in a["explanation"] for a in fu[0]["alternatives"])  # unseen earlier posts always listed
    assert {c["key"] for c in fu[0]["would_confirm"]} >= {"upload_logs", "archive"} and fu[0]["would_disprove"]
    assert any("not show who created" in c for c in fu[0]["cautions"])


def test_roles_never_name_a_creator_or_an_editor():
    cid, _ = scenario()
    out = engine(cid)
    roles = {a["label"]: [r["role"] for r in a["roles"]] for a in out["accounts"]}
    assert "First known uploader" in roles["@alice_news"]
    assert "Distributor" in roles["@bob_reposts"] and "Distributor" in roles["@dave_other"]
    assert "First known uploader of an altered version" in roles["@carol_memes"]  # not "editor"
    flat = " ".join(r for rs in roles.values() for r in rs)
    assert "creator" not in flat.lower() and "editor" not in flat.lower()
    assert all("Original creator" in a["not_established"] for a in out["accounts"])
    assert {g["role"] for g in out["roles_glossary"]} == {"Original creator", "Editor", "First known uploader", "Distributor"}


def test_altered_version_lead_points_at_the_first_poster_of_the_overlay_without_calling_them_the_editor():
    cid, _ = scenario()
    lead = by_type(engine(cid), "altered_version")
    carol = [l for l in lead if "carol_memes" in l["headline"]]
    assert carol and "first known uploader of an altered version" in carol[0]["headline"]
    assert any("does NOT say" in c for c in carol[0]["cautions"]) and any("Text added" in e["text"] for e in carol[0]["evidence"])


def test_same_file_and_time_alone_never_make_a_relationship_lead():
    cid, _ = scenario()
    links = by_type(engine(cid), "link")
    pairs = {tuple(sorted(l["subjects"])) for l in links}
    assert not any("carol_memes" in " ".join(p) or "dave_other" in " ".join(p) for p in pairs)  # shared media / time are expected of reposts


def test_identical_caption_and_link_is_only_low_confidence_until_an_independent_kind_of_evidence_appears():
    cid, _ = scenario()
    ab = [l for l in by_type(engine(cid), "link") if set(l["subjects"]) == {"x (twitter):alice_news", "x (twitter):bob_reposts"}]
    assert len(ab) == 1 and ab[0]["confidence"] == "low"  # one independent kind (text); content/time are not distinctive
    assert any("Identical caption" in e["text"] for e in ab[0]["evidence"]) and any("same link" in e["text"] for e in ab[0]["evidence"])
    assert any("independently re-posted" in a["explanation"] for a in ab[0]["alternatives"])
    assert any("NOT shown that they know each other" in c for c in ab[0]["cautions"])

    cid2, _ = scenario(identity_clue=True)  # A's handle is visible inside B's copy -> a second independent kind
    ab2 = [l for l in by_type(engine(cid2), "link") if set(l["subjects"]) == {"x (twitter):alice_news", "x (twitter):bob_reposts"}]
    assert ab2 and ab2[0]["confidence"] == "moderate" and any("handle is visible" in e["text"] for e in ab2[0]["evidence"])


def test_capture_only_times_force_low_confidence_and_say_why():
    cid = new_case()
    m0 = add_media(cid, "primary")
    add_post(cid, "a_acc", 0, "x" * 30, [m0], seed=True, declared=False)
    add_post(cid, "b_acc", 5, "y" * 30, [m0], declared=False)
    fu = by_type(engine(cid), "first_uploader")[0]
    assert fu["confidence"] == "low" and "captured" in fu["confidence_reason"]
    assert any(e["text"].endswith("capture time only)") for e in fu["evidence"])


def test_graph_separates_observed_from_hypothesis_and_leads_carry_no_accusatory_wording():
    cid, _ = scenario(identity_clue=True)
    out = engine(cid)
    kinds = {e["kind"] for e in out["graph"]["edges"]}
    assert kinds == {"observed", "hypothesis"}
    assert all(e["kind"] == "observed" for e in out["graph"]["edges"] if e["label"].startswith("posted"))
    assert all(e["kind"] == "hypothesis" for e in out["graph"]["edges"] if e["label"].startswith("possible link"))
    text = " ".join(l["headline"] + " ".join(e["text"] for e in l["evidence"]) for l in out["leads"])
    assert lint_text(text) == []
    assert "authorised" in out["limits"][-1].lower()


def test_versions_show_how_they_differ_and_their_earliest_appearance():
    cid, (m0, m1, m2) = scenario()
    out = engine(cid)
    v = {x["id"]: x for x in out["versions"]}
    assert v[m0]["label"] == "Reported media" and v[m0]["earliest"]["account"] == "@alice_news" and v[m0]["parent"] is None
    assert v[m1]["parent"]["differences"][0].startswith("Cropped") and v[m1]["earliest"]["account"] == "@bob_reposts"
    assert any("Text added" in d for d in v[m2]["parent"]["differences"]) and len(v[m0]["appearances"]) == 2  # alice + dave
    assert out["summary"]["versions"] == 3 and out["summary"]["families"] == 1


# ------------------------------------------------------------------------------------------------ generator attribution
def test_signed_c2pa_names_the_family_and_is_established():
    cl = [claim("c2pa", "C2PA manifest present (embedded); validation state: Valid; claim generator: OpenAI; signed by: OpenAI", Strength.STRONG and Status.VERIFIED,
                state="Valid", generator="OpenAI GPT-4o image", issuer="OpenAI")]
    r = generator_attribution(cl)
    assert r["possible_family"].startswith("OpenAI") and r["tier"] == "established"
    assert "signed" in r["candidates"][0]["kinds"] and "does not show who made it" in r["cautions"][0]


def test_embedded_parameters_are_supported_but_labelled_forgeable():
    r = generator_attribution([claim("provenance", "Embedded generation parameters identify the tool family: Stable Diffusion (AUTOMATIC1111)",
                                     generator_family="Stable Diffusion (AUTOMATIC1111)")])
    assert r["possible_family"] == "Stable Diffusion family" and r["tier"] == "supported"
    assert "anyone can add" in r["candidates"][0]["evidence"][0]["reliability"].lower()


def test_a_single_classifier_guess_is_only_suggested_and_says_so():
    r = generator_attribution([claim("detector", "Hive says AI", vendor="Hive", score=0.97, source_probs={"midjourney": 0.81, "flux": 0.1})])
    assert r["possible_family"] == "Midjourney" and r["tier"] == "suggested"
    assert "single statistical signal" in r["cautions"][0]


def test_conflicting_declared_families_are_not_resolved_by_picking_one():
    cl = [claim("provenance", "Embedded parameters: Stable Diffusion", generator_family="Stable Diffusion (AUTOMATIC1111)"),
          claim("metadata", "Software field names an AI generation tool: “Midjourney”", field="EXIF Software", value="Midjourney")]
    r = generator_attribution(cl)
    assert r["possible_family"] is None and r["conflict"] and "more than one generator family" in r["headline"]


def test_no_evidence_does_not_claim_the_media_is_real_and_lists_unchecked_watermarks():
    r = generator_attribution([claim("c2pa", "No C2PA / Content Credentials manifest found", Status.UNKNOWN)])
    assert r["possible_family"] is None and r["headline"] == "No generator could be attributed"
    assert any("does not mean the media is real" in c for c in r["cautions"])
    assert any("SynthID" in n["name"] for n in r["not_checked"])


def test_a_generator_label_on_an_image_the_detectors_call_real_is_flagged_as_contradiction():
    r = generator_attribution([claim("provenance", "Embedded parameters", generator_family="Stable Diffusion (AUTOMATIC1111)")], p_ai=0.05)
    assert "do NOT think this image is AI-generated" in r["cautions"][0]
    assert canon("made with Adobe Firefly") == "Adobe Firefly" and canon("Adobe Photoshop 25") is None  # Photoshop is editing, not Firefly


# ------------------------------------------------------------------------------------------------ device / source report
def _vault_image(cid, im: Image.Image, fmt="JPEG", exif=None, **save):
    buf = io.BytesIO()
    im.save(buf, fmt, **({"exif": exif} if exif else {}), **save)
    rec = vault_for(cid).store(buf.getvalue(), kind="original", filename="a." + fmt.lower(), mime="image/" + fmt.lower(), acquisition={"method": "test"})
    with session() as s:
        m = Media(case_id=cid, sha256=rec["sha256"], modality="image", role="primary", label="f", width=im.size[0], height=im.size[1], size=len(buf.getvalue()),
                  mime="image/" + fmt.lower(), fingerprints={"x": 1})
        s.add(m)
        s.commit()
        s.refresh(m)
        return m


def test_device_report_labels_camera_as_declared_and_flags_edits():
    cid = new_case()
    ex = Image.Exif()
    ex[0x010F], ex[0x0110], ex[0x0131] = "Apple", "iPhone 13", "Adobe Photoshop 25.0"
    ex[0x9003 - 0x9003 + 0x0132] = "2026:01:03 12:00:00"
    sub = ex.get_ifd(0x8769)
    sub[0x9003], sub[0xA002], sub[0xA003] = "2026:01:01 10:00:00", 4032, 3024  # DateTimeOriginal, PixelX/Y: the file was later resized
    m = _vault_image(cid, Image.fromarray(np.random.default_rng(1).integers(0, 255, (300, 400, 3), dtype=np.uint8)), exif=ex)
    with session() as s:
        r = device_report(s, cid, m)
    assert r["device"]["claimed"]["model"] == "iPhone 13" and r["device"]["tier"].startswith("declared")
    assert "not a unique device" in r["device"]["note"]
    ids = {f["id"] for f in r["consistency"]}
    assert {"modified_after_capture", "editing_software", "size_mismatch"} <= ids
    assert any(x["kind"] == "editing" for x in r["software"]) and any("Taken" in t["label"] for t in r["timestamps"])
    assert any("platform" in l.lower() for l in r["limits"])


def test_metadata_free_file_is_not_treated_as_suspicious_and_platform_size_is_only_a_hint():
    cid = new_case()
    m = _vault_image(cid, Image.fromarray(np.random.default_rng(2).integers(0, 255, (810, 1080, 3), dtype=np.uint8)))
    with session() as s:
        r = device_report(s, cid, m)
    assert r["device"]["claimed"] is None and "does not mean the media is real or fake" in r["device"]["note"]
    assert r["platform_hints"] and "Instagram-style" in r["platform_hints"][0]["label"] and "no camera metadata" in r["platform_hints"][0]["why"]


# ------------------------------------------------------------------------------------------------ PRNU (synthetic sensors)
def _shot(k, seed, hw=(256, 256)):
    r = np.random.default_rng(seed)
    scene = gaussian_filter(r.normal(0, 1, hw), 12)
    scene = 120 + 45 * scene / scene.std() + 8 * gaussian_filter(r.normal(0, 1, hw), 1.5)
    return Image.fromarray(np.clip(scene * (1 + k) + r.normal(0, 1.2, hw), 0, 255).astype(np.uint8)).convert("RGB")


def test_prnu_separates_same_sensor_from_other_sensor_and_refuses_resized_images(tmp_path):
    rng = np.random.default_rng(5)
    ka, kb = rng.normal(0, 0.03, (256, 256)), rng.normal(0, 0.03, (256, 256))
    refs = []
    for i in range(6):
        p = tmp_path / f"r{i}.png"
        _shot(ka, 10 + i).save(p)
        refs.append(p)
    same, other = tmp_path / "same.png", tmp_path / "other.png"
    _shot(ka, 99).save(same)
    _shot(kb, 98).save(other)
    assert prnu.compare_to_references(refs, same)["status"] == "consistent"
    r_other = prnu.compare_to_references(refs, other)
    assert r_other["status"] == "not_consistent" and r_other["pce"] < prnu.PCE_NOISE
    small = tmp_path / "small.png"
    _shot(ka, 97).resize((128, 128)).save(small)
    bad = prnu.compare_to_references(refs, small)
    assert bad["status"] == "not_comparable" and "same pixel size" in bad["summary"] or "256" in bad["summary"]
    few = prnu.compare_to_references(refs[:2], same)
    assert few["status"] == "not_comparable" and "at least" in few["summary"]
    assert prnu.compare_pair(same, _shot(ka, 96).save(tmp_path / "p.png") or tmp_path / "p.png")["status"] == "consistent"
    assert any("AI-generated images have no sensor pattern" in l for l in r_other["limits"])


def test_prnu_refuses_to_compare_two_copies_of_the_same_picture_and_too_few_references():
    """Copies of one picture correlate because of the picture, which would look like a sensor match but is not one."""
    from fastapi.testclient import TestClient

    from dfir.main import app
    cid = new_case()
    same = {"image": {"pdq": "f" * 64, "pdq_mirror": "0" * 64}}
    a, b = add_media(cid, fp=same), add_media(cid, fp=same)
    far = add_media(cid, fp={"image": {"pdq": "0f" * 32, "pdq_mirror": "f0" * 32}})
    with TestClient(app) as c:
        r = c.post(f"/api/cases/{cid}/prnu", json={"target_media_id": a, "compare_with": b})
        assert r.status_code == 400 and "same picture" in r.json()["detail"]
        r = c.post(f"/api/cases/{cid}/prnu", json={"target_media_id": a, "reference_media_ids": [far]})
        assert r.status_code == 400 and "at least" in r.json()["detail"]


def _with_embedded_preview(main: Image.Image, preview: Image.Image) -> bytes:
    """A JPEG whose EXIF segment carries a small preview image, the way cameras write it."""
    a, b = io.BytesIO(), io.BytesIO()
    main.save(a, "JPEG")
    preview.save(b, "JPEG")
    seg = b"Exif\x00\x00" + b"MM\x00*\x00\x00\x00\x08" + b"\x00" * 8 + b.getvalue()
    return a.getvalue()[:2] + b"\xff\xe1" + (len(seg) + 2).to_bytes(2, "big") + seg + a.getvalue()[2:]


def _report_for(cid, raw: bytes, size):
    rec = vault_for(cid).store(raw, kind="original", filename="p.jpg", mime="image/jpeg", acquisition={"method": "test"})
    with session() as s:
        m = Media(case_id=cid, sha256=rec["sha256"], modality="image", role="primary", label="p", width=size[0], height=size[1], size=len(raw),
                  mime="image/jpeg", fingerprints={"x": 1})
        s.add(m)
        s.commit()
        s.refresh(m)
        return device_report(s, cid, m)


def test_embedded_camera_preview_reveals_a_picture_changed_after_capture():
    rng = np.random.default_rng(11)
    scene = Image.fromarray(gaussian_filter(rng.normal(128, 40, (300, 400)), 6).clip(0, 255).astype(np.uint8)).convert("RGB")
    other = Image.fromarray(gaussian_filter(np.random.default_rng(99).normal(128, 40, (300, 400)), 6).clip(0, 255).astype(np.uint8)).convert("RGB")
    ok = _report_for(new_case(), _with_embedded_preview(scene, scene.resize((64, 48))), scene.size)
    assert {f["id"] for f in ok["consistency"]} >= {"thumbnail_ok"}                                        # preview matches: nothing flagged
    changed = _report_for(new_case(), _with_embedded_preview(scene, other.resize((64, 48))), scene.size)
    assert any(f["id"] == "thumbnail_content" and f["severity"] == "high" for f in changed["consistency"])  # preview shows another picture
    cropped = _report_for(new_case(), _with_embedded_preview(scene, scene.resize((64, 64))), scene.size)
    assert any(f["id"] == "thumbnail_aspect" for f in cropped["consistency"])                               # preview has another shape: cropped after capture


def test_a_lone_unrelated_post_gets_no_first_uploader_lead():
    cid = new_case()
    m0 = add_media(cid, "primary")
    add_post(cid, "solo_acc", 0, "something", [m0], seed=True)
    assert by_type(engine(cid), "first_uploader") == []


def test_platform_info_from_ytdlp_fills_the_account_but_never_a_login_wall_caption():
    from dfir.propagation.capture import LOGIN_CAPTION, _adopt_platform_info
    loc = Location(case_id="c", url="https://www.instagram.com/reels/AbC/", platform="Instagram")
    _adopt_platform_info(loc, {"channel": "better.watch.now", "uploader": "Some Name", "uploader_id": "47846285255", "description": "meme templates",
                               "timestamp": "1753951080", "like_count": "3", "view_count": None})
    assert loc.account_handle == "better.watch.now" and loc.account_display == "Some Name" and loc.caption == "meme templates"
    assert loc.published_at.year == 2025 and "declared by the platform" in loc.published_source and loc.reach == {"likes": "3"}
    assert LOGIN_CAPTION.search("Create an account or log in to Instagram – Share what you're into") and not LOGIN_CAPTION.search("my holiday photos")
    keep = Location(case_id="c", url="https://x/", account_handle="already", caption="page caption")
    _adopt_platform_info(keep, {"channel": "other", "description": "other"})
    assert keep.account_handle == "already" and keep.caption == "page caption"  # never overwrites what the page gave
