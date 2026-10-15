from pathlib import Path

import numpy as np
from PIL import Image

from dfir.analyzers.content_region import find_content_box
from dfir.analyzers.verdict import LABELS, build_verdict
from dfir.benchmark import label_from_name
from dfir.core.claims import Claim, Polarity, Status, Strength

DEMO = Path(__file__).resolve().parents[2] / "demo"


def det(model: str, score: float, category: str = "detector", **extra) -> Claim:
    data = {"model": model, "score": score, **extra}
    return Claim(stage="decomposition", category=category, statement=f"{model} {score}", status=Status.INFERRED,
                 produced_by=f"{model.lower()}@1", data=data)


def test_label_bands_cover_unit_interval():
    assert [t for t, *_ in LABELS] == sorted([t for t, *_ in LABELS], reverse=True)
    assert LABELS[-1][0] == 0.0


def test_verdict_real_and_fake_and_uncertain(monkeypatch):
    monkeypatch.setattr("dfir.analyzers.verdict.calibration", lambda: None)
    v = build_verdict([det("SPAI", 0.02), det("SBI (FF++ c23)", 0.05, "faceswap", max_score=0.05, faces=[{}])])
    assert v["label"] == "real" and v["p_fake"] < 0.15
    v = build_verdict([det("SPAI", 0.97)])
    assert v["label"] == "fake" and v["kind"] == "AI-generated image"
    v = build_verdict([det("SPAI", 0.5)])
    assert v["label"] == "uncertain" and "leaning" in v["summary"] and v["confidence"] == "low"
    assert any("not yet calibrated" in c for c in v["caveats"])


def test_face_swap_drives_edit_head(monkeypatch):
    monkeypatch.setattr("dfir.analyzers.verdict.calibration", lambda: None)
    v = build_verdict([det("SPAI", 0.05), det("SBI (FF++ c23)", 0.95, "faceswap", max_score=0.95, faces=[{}])])
    assert v["label"] == "fake" and v["kind"] == "Face swap / edited photo"


def test_provenance_proof_overrides_detectors(monkeypatch):
    monkeypatch.setattr("dfir.analyzers.verdict.calibration", lambda: None)
    proof = Claim(stage="decomposition", category="provenance", statement="Embedded SD parameters", status=Status.VERIFIED,
                  strength=Strength.STRONG, polarity=Polarity.SUPPORTS_MANIPULATION, produced_by="provenance_signals@1")
    v = build_verdict([det("SPAI", 0.01), proof])
    assert v["label"] == "fake" and v["proof"] and v["confidence"] == "high"


def test_calibrated_stack_is_used(monkeypatch):
    cal = {"ai_head": {"features": ["spai_logit", "sbi_logit", "has_face"], "coef": [0.5, 0.0, 0.0], "intercept": -1.0},
           "eval": {"n": 640, "accuracy": 0.8, "auc": 0.88}}
    monkeypatch.setattr("dfir.analyzers.verdict.calibration", lambda: cal)
    v = build_verdict([det("SPAI", 0.99)])
    assert v["calibrated"] and 0.5 < v["p_ai"] < 0.99  # the stack tempers an overconfident raw score
    assert "640" in v["reliability"] and "80%" in v["reliability"]


def test_no_detectors_means_no_verdict():
    v = build_verdict([], [{"component": "spai", "reason": "x", "how_to_enable": "", "outcome": "not_run"}])
    assert not v["available"]


def test_screenshot_is_cropped_to_embedded_picture():
    rgb = np.asarray(Image.open(DEMO / "modi-attire-fake.png").convert("RGB"))
    box, info = find_content_box(rgb)
    assert box is not None and info["reason"] == "screenshot"
    x0, y0, x1, y1 = box
    assert abs(x0 - 65) < 15 and abs(y0 - 85) < 15 and abs(x1 - 440) < 15 and abs(y1 - 538) < 15


def test_plain_photos_are_not_cropped():
    rng = np.random.default_rng(0)
    photo = (rng.random((400, 600, 3)) * 255).astype(np.uint8)
    assert find_content_box(photo)[0] is None


def test_labels_from_file_names():
    assert label_from_name("fake_1009.jpg") == "fake"
    assert label_from_name("real_1.jpg") == "real"
    assert label_from_name("modi-attire-fake.png") == "fake"
    assert label_from_name("IMG_2031.jpg") is None


def test_leak_guard_flags_demo_images():
    import importlib.util
    spec = importlib.util.spec_from_file_location("calibrate", Path(__file__).resolve().parents[2] / "scripts" / "calibrate.py")
    cal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cal)
    shas, pdqs = cal.leak_guard_hashes()
    import hashlib
    assert hashlib.sha256((DEMO / "real_1.jpg").read_bytes()).hexdigest() in shas
    # a re-encoded copy of a test image is still caught by the perceptual hash
    import io
    buf = io.BytesIO()
    Image.open(DEMO / "fake_10.jpg").save(buf, "JPEG", quality=70)
    p = cal._pdq_hex(Image.open(io.BytesIO(buf.getvalue())))
    assert any(cal._ham(p, x) <= 31 for x in pdqs)


def hive(ai: float, deepfake: float | None = None) -> list[Claim]:
    out = [Claim(stage="decomposition", category="detector", statement="hive", status=Status.INFERRED,
                 produced_by="api_hive@3.0", data={"vendor": "Hive", "score": ai, "model": "Hive (commercial)"})]
    if deepfake is not None:
        out.append(Claim(stage="decomposition", category="faceswap", statement="hive df", status=Status.INFERRED,
                         produced_by="api_hive@3.0", data={"vendor": "Hive", "score": deepfake}))
    return out


def test_hive_is_primary_and_local_is_second_opinion(monkeypatch):
    monkeypatch.setattr("dfir.analyzers.verdict.calibration", lambda: None)
    # local says real, Hive says AI-generated -> Hive decides, disagreement is flagged
    v = build_verdict([det("SPAI", 0.02), *hive(0.98, 0.0)])
    assert v["engine"] == "hive" and v["label"] == "fake" and v["kind"] == "AI-generated image"
    assert v["second_opinion"]["label"] == "real" and v["second_opinion"]["strong_disagreement"]
    assert v["confidence"] == "low" and any("disagree" in c for c in v["caveats"])
    assert v["detectors"][0]["name"] == "Hive" and v["detectors"][0]["primary"]
    # both agree -> high confidence
    v = build_verdict([det("SPAI", 0.99), *hive(0.97, 0.0)])
    assert v["label"] == "fake" and v["second_opinion"]["agrees"] and v["confidence"] == "high"
    # Hive deepfake head drives the face-swap kind
    v = build_verdict([*hive(0.01, 0.95)])
    assert v["label"] == "fake" and v["kind"] == "Face swap / edited photo"


def test_local_fallback_when_hive_unavailable(monkeypatch):
    monkeypatch.setattr("dfir.analyzers.verdict.calibration", lambda: None)
    nr = [{"component": "api_hive", "reason": "Hive unavailable — All Hive keys failed: key 1: out of credit",
           "how_to_enable": "", "outcome": "not_run"}]
    v = build_verdict([det("SPAI", 0.97)], nr)
    assert v["engine"] == "local" and v["label"] == "fake" and v["second_opinion"] is None
    assert any("Hive was unavailable" in c for c in v["caveats"])


def test_hive_key_fallback_order(monkeypatch):
    import httpx

    from dfir.analyzers import detectors_api as api
    keys = {"HIVE_SECRET_KEY": "k1", "HIVE_SECRET_KEY_2": "k2", "HIVE_SECRET_KEY_3": "k3", "HIVE_REGION": "sf1"}
    monkeypatch.setattr(api, "get_key", lambda n: keys.get(n))
    api._HIVE_COOLDOWN.clear()
    used = []
    ok = {"output": [{"classes": [{"class": "ai_generated", "value": 0.9}, {"class": "deepfake", "value": 0.1}]}]}

    def post(url, headers, json, timeout):
        k = headers["Authorization"].split()[1]
        used.append(k)
        code = {"k1": 429, "k2": 401}.get(k, 200)
        return httpx.Response(code, json=ok if code == 200 else {"error": "x"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(api.httpx, "post", post)
    classes, raw = api.hive_call(b"img", "a.jpg", "image/jpeg")
    assert used == ["k1", "k2", "k3"] and raw["key_slot"] == 3 and classes["ai_generated"] == 0.9
    assert [a["slot"] for a in raw["failed_keys"]] == [1, 2] and "k1" not in str(raw["failed_keys"])
    # the failing keys cool down: next call goes straight to key 3
    used.clear()
    api.hive_call(b"img", "a.jpg", "image/jpeg")
    assert used == ["k3"]
    # every key failing raises HiveUnavailable (the verdict then falls back to the local detectors)
    keys.pop("HIVE_SECRET_KEY_3")
    api._HIVE_COOLDOWN.clear()
    import pytest
    with pytest.raises(api.HiveUnavailable):
        api.hive_call(b"img", "a.jpg", "image/jpeg")
    # a media problem (HTTP 400) is not retried with other keys
    keys["HIVE_SECRET_KEY"] = "bad_media"
    api._HIVE_COOLDOWN.clear()
    monkeypatch.setattr(api.httpx, "post", lambda url, headers, json, timeout: httpx.Response(
        400, json={}, request=httpx.Request("POST", url)))
    with pytest.raises(RuntimeError, match="rejected the media"):
        api.hive_call(b"img", "a.jpg", "image/jpeg")
    api._HIVE_COOLDOWN.clear()
