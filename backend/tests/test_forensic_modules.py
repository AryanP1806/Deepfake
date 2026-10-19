"""Forensic modules: contract, plugin architecture, validated behaviour on simulated ground truth, integration with the vault and custody ledger."""
import io
import json
import shutil
import textwrap
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from dfir.core.db import init_db
from dfir.forensics import prnu, registry
from dfir.forensics.base import CONFIDENCE_CAP, FCtx, Finding, ForensicModule, ModuleResult, Requirement, wilson
from dfir.forensics.bench import synth
from dfir.main import app

init_db()
HAVE_FFMPEG = shutil.which("ffmpeg") is not None


def run(mid, path, modality="image", **kw):
    return registry.get(mid).execute(FCtx(path=Path(path), modality=modality, media={"filename": Path(path).name}, **kw))


def jpg(tmp_path, name, b: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(b)
    return p


# ------------------------------------------------------------------------------------------------ contract
def test_six_modules_registered_with_required_metadata():
    ids = {m.id for m in registry.all_modules()}
    assert {"prnu", "camera_pipeline", "editing", "video_acquisition", "audio_forensics", "provenance"} <= ids
    for m in registry.all_modules():
        assert m.title and m.question and m.version and m.modalities and m.limitations


def test_every_result_carries_findings_confidence_limitations_versions_and_validation(tmp_path):
    p = jpg(tmp_path, "a.jpg", synth.Sensor(seed=1).capture(synth.scene(1), 1, jpeg_q=92))
    for mid in ("provenance", "prnu", "camera_pipeline", "editing"):
        r = run(mid, p)
        assert isinstance(r, ModuleResult) and r.findings, mid
        assert r.limitations and r.versions and r.validation.summary, mid
        assert r.confidence is None or 0 <= r.confidence <= 1
        assert r.confidence_label and r.run_at


def test_missing_evidence_is_unknown_never_clean(tmp_path):
    p = jpg(tmp_path, "a.jpg", synth.Sensor(seed=1).capture(synth.scene(1), 1, jpeg_q=92))
    r = run("prnu", p)  # no candidate device on file
    assert r.finding("prnu.no_candidate").kind == "unknown"
    assert r.finding("attribution.device").kind == "unknown"
    assert r.confidence is None
    pr = run("provenance", p)
    assert pr.finding("c2pa.none").kind == "unknown" or pr.finding("c2pa.unavailable").kind == "unknown"
    assert pr.finding("attribution.device").kind == "unknown"


def test_confidence_is_capped_by_validation_tier():
    class M(ForensicModule):
        id, title, question = "t", "T", "q"

    def result(tier, kind, conf):
        r = ModuleResult(module="t", title="T", version="1", findings=[Finding(id="f", title="f", statement="s", kind=kind, confidence=conf)])
        r.validation.tier = tier
        M().finalise(r)
        return r
    assert result("none", "supported", 0.9).confidence == CONFIDENCE_CAP["none"]
    assert result("synthetic", "supported", 0.95).confidence == CONFIDENCE_CAP["synthetic"]
    assert result("real", "supported", 0.9).confidence == 0.9
    assert result("synthetic", "hypothesis", 0.9).confidence == CONFIDENCE_CAP["none"]  # a hypothesis is never above the unvalidated cap
    assert result("real", "observation", None).confidence is None


def test_unavailable_module_reports_unknown_with_install_hint(tmp_path):
    class NeedsModel(ForensicModule):
        id, title, question, modalities = "needs_model", "Needs a model", "q", ("image",)
        requirements = [Requirement(kind="python", name="surely_not_installed_pkg", how_to_enable="pip install surely-not")]
    p = jpg(tmp_path, "a.jpg", synth.Sensor(seed=1).capture(synth.scene(1), 1, jpeg_q=92))
    r = NeedsModel().execute(FCtx(path=p, modality="image"))
    assert r.status == "unavailable" and "pip install surely-not" in r.summary and r.unknowns


def test_a_crashing_module_never_raises(tmp_path):
    class Boom(ForensicModule):
        id, title, question, modalities = "boom", "Boom", "q", ("image",)

        def analyse(self, fx):
            raise RuntimeError("kaput")
    p = jpg(tmp_path, "a.jpg", synth.Sensor(seed=1).capture(synth.scene(1), 1, jpeg_q=92))
    r = Boom().execute(FCtx(path=p, modality="image"))
    assert r.status == "error" and "kaput" in r.error and r.unknowns


def test_wilson_interval_is_honest_for_zero_failures():
    lo, hi = wilson(0, 100)
    assert lo == 0 and 0.03 < hi < 0.05


# ------------------------------------------------------------------------------------------------ plugins
PLUGIN = textwrap.dedent('''
    from dfir.forensics.base import ForensicModule, ModuleResult, Finding, Requirement
    from dfir.forensics.registry import register
    DFIR_PLUGIN_API = {api}

    @register
    class Hello(ForensicModule):
        id = "hello_plugin"; title = "Hello"; question = "q"; modalities = ("image",); version = "9.1"
        requirements = [Requirement(kind="model", name="hello_model", optional=True, how_to_enable="setup_models.py hello_model")]
        def analyse(self, fx):
            return ModuleResult(module=self.id, title=self.title, version=self.version, findings=[Finding(id="h", title="h", statement="hi", kind="observation")])
''')


def test_drop_in_plugin_loads_and_broken_or_incompatible_ones_are_isolated(tmp_path, monkeypatch):
    (tmp_path / "good.py").write_text(PLUGIN.format(api=1))
    (tmp_path / "old_api.py").write_text(PLUGIN.format(api=99).replace("hello_plugin", "old_plugin"))
    (tmp_path / "broken.py").write_text("raise ImportError('missing dependency')")
    monkeypatch.setenv("DFIR_PLUGIN_DIR", str(tmp_path))
    try:
        rep = registry.load_plugins(force=True)
        by = {r["name"]: r for r in rep}
        assert by["good"]["loaded"] and by["good"]["modules"] == ["hello_plugin"]
        assert not by["old_api"]["loaded"] and "plugin API" in by["old_api"]["error"]
        assert not by["broken"]["loaded"] and "missing dependency" in by["broken"]["error"]
        assert registry.get("hello_plugin") is not None and registry.get("old_plugin") is None
        assert registry.get("prnu") is not None  # built-ins unaffected
        d = next(m for m in registry.describe() if m["id"] == "hello_plugin")
        assert d["origin"].startswith("plugin file") and d["available"]  # an OPTIONAL missing model does not block it
        assert any(not r["ok"] and r["optional"] for r in d["requirements"])
    finally:
        monkeypatch.delenv("DFIR_PLUGIN_DIR")
        registry.load_plugins(force=True)
    assert registry.get("hello_plugin") is None


def test_plugin_model_specs_are_registered_for_independent_download():
    from dfir.core import model_store

    class WithSpec(ForensicModule):
        id, title, question = "with_spec", "S", "q"
        model_specs = [model_store.ModelSpec(key="plug_model", title="t", files={"w.bin": "https://example.invalid/w.bin"}, licence="MIT", size_note="1 MB",
                                             trained_on="x", blind_spots="y", used_by="with_spec")]
    registry.register(WithSpec)
    try:
        assert "plug_model" in model_store.SPECS
    finally:
        registry._MODULES.pop("with_spec", None)
        model_store.SPECS.pop("plug_model", None)


# ------------------------------------------------------------------------------------------------ module 1: PRNU
def test_prnu_does_not_match_a_different_camera_that_shares_the_same_colour_filter_pattern():
    """Regression: with every simulated camera sharing one Bayer pattern, impostor PCE used to be ~250 (a false match) because the
    residual kept CFA-phase structure. Different cameras must stay near the noise floor; the true camera must match."""
    S = 256
    sens = [synth.Sensor(seed=i, h=S, w=S) for i in (0, 1)]

    def gray(b):
        return prnu.load_gray(io.BytesIO(b))
    fps = [prnu._crop(prnu.estimate_fingerprint([prnu._crop(gray(s.capture(synth.scene(100 * d + i, S, S), 500 + i, jpeg_q=95))) for i in range(6)])) for d, s in enumerate(sens)]
    g = prnu._crop(gray(sens[0].capture(synth.scene(9000, S, S), 777, jpeg_q=95)))
    w = prnu.noise_residual(g)
    genuine = prnu._pce(w, fps[0] * g)[0]
    impostor = prnu._pce(w, fps[1] * g)[0]
    assert genuine > prnu.PCE_MATCH and impostor < prnu.PCE_NOISE, (genuine, impostor)


def test_prnu_module_compares_against_candidate_and_never_names_a_device(tmp_path):
    S = 256
    s0, s1 = synth.Sensor(seed=0, h=S, w=S), synth.Sensor(seed=1, h=S, w=S)
    refs = [prnu.load_gray(io.BytesIO(s0.capture(synth.scene(i, S, S), 50 + i, jpeg_q=95))) for i in range(6)]
    k = prnu._crop(prnu.estimate_fingerprint([prnu._crop(g) for g in refs]))
    cand = [{"id": "dev1", "name": "Phone A", "height": S, "width": S, "n_references": 6, "coherent": True, "fingerprint": k}]
    own = jpg(tmp_path, "own.jpg", s0.capture(synth.scene(321, S, S), 9, jpeg_q=95))
    other = jpg(tmp_path, "other.jpg", s1.capture(synth.scene(322, S, S), 9, jpeg_q=95))
    r_own = run("prnu", own, candidate_devices=cand)
    r_other = run("prnu", other, candidate_devices=cand)
    assert r_own.finding("prnu.match.dev1").kind == "supported" and "does not prove" in r_own.finding("prnu.match.dev1").statement
    assert r_other.finding("prnu.none.dev1").kind == "observation" and "NOT evidence" in r_other.finding("prnu.none.dev1").caution
    for r in (r_own, r_other):
        assert r.finding("attribution.device").kind == "unknown"
    cand[0]["height"] = 300  # different pixel size: cannot compare, and must not be called a non-match
    assert run("prnu", own, candidate_devices=cand).finding("prnu.size_mismatch.dev1").kind == "unknown"


# ------------------------------------------------------------------------------------------------ module 2: camera pipeline
def test_cfa_trace_present_in_demosaiced_pixels_and_erased_by_resizing():
    from dfir.forensics.modules import camera as C
    a = synth.Sensor(seed=3).capture(synth.scene(11), 1, jpeg_q=None)
    assert C.cfa_summary(C.cfa_tiles(a))["present"]
    assert not C.cfa_summary(C.cfa_tiles(synth.t_resize(0.9)(a)))["present"]
    assert not C.cfa_summary(C.cfa_tiles(synth.Sensor(seed=4, cfa=False).capture(synth.scene(12), 1, jpeg_q=None)))["present"]


def test_camera_module_describes_but_does_not_overclaim(tmp_path):
    p = jpg(tmp_path, "a.jpg", synth.Sensor(seed=3).capture(synth.scene(11), 1, jpeg_q=98))
    r = run("camera_pipeline", p)
    prof = r.finding("profile.camera_like")
    assert prof.kind in ("hypothesis", "unknown") and (prof.confidence or 0) <= CONFIDENCE_CAP["none"]
    assert "make or model" in " ".join(r.limitations)


# ------------------------------------------------------------------------------------------------ module 3: editing
def test_editing_untouched_image_is_not_flagged_and_double_compressed_splice_is_localised(tmp_path):
    base = synth.Sensor(seed=5).capture(synth.scene(21), 1, jpeg_q=None)
    clean = run("editing", jpg(tmp_path, "c.jpg", synth.to_jpeg(base, 92)))
    assert clean.finding("localisation").kind == "observation"
    donor = synth.Sensor(seed=6).capture(synth.scene(22), 2, jpeg_q=None)
    hits = 0
    for i, (x, y) in enumerate(((80, 70), (200, 210), (120, 260))):
        out, m = synth.splice(synth.decode(synth.to_jpeg(base, 75)), synth.decode(synth.to_jpeg(donor, 95)), (x, y, 128, 128))
        r = run("editing", jpg(tmp_path, f"s{i}.jpg", synth.to_jpeg(out, 92)))
        hits += r.finding("localisation").kind == "hypothesis"
    assert hits >= 2  # measured: ~70-80 % on this kind of history mismatch; the full rate is in validation_results.json


def test_copy_move_is_found_and_reported_as_a_hypothesis_not_a_conclusion(tmp_path):
    found = 0
    for i in range(4):  # measured detection rate is about two thirds; assert it works on most, not on every image
        base = synth.Sensor(seed=7 + i).capture(synth.scene(31 + i), 1, jpeg_q=None)
        out, _ = synth.copy_move(base, (60, 60, 112, 112), (300, 280))
        f = run("editing", jpg(tmp_path, f"cm{i}.jpg", synth.to_jpeg(out, 92))).finding("copy_move")
        if f.kind == "hypothesis":
            found += 1
            assert "Repeating patterns" in f.caution
    assert found >= 2


def test_global_enlargement_is_detected_but_downscaling_is_documented_as_undetectable(tmp_path):
    import cv2
    a = synth.Sensor(seed=8, h=640, w=640).capture(synth.scene(41, 640, 640), 1, jpeg_q=None)
    up = cv2.resize(a, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
    assert run("editing", jpg(tmp_path, "up.jpg", synth.to_jpeg(up, 92))).finding("resampling.global").kind == "supported"
    none = run("editing", jpg(tmp_path, "n.jpg", synth.to_jpeg(a, 92)))
    assert none.finding("resampling.none") is not None and "Reductions" in none.finding("resampling.none").caution


# ------------------------------------------------------------------------------------------------ module 4: video
@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_rolling_shutter_sign_and_proportionality_and_global_shutter_control(tmp_path):
    from dfir.forensics.modules import video as V
    from dfir.forensics.modules._av import decode_gray
    est = {}
    for ro in (0.0, 0.4, 0.8):
        p = tmp_path / f"rs{ro}.mp4"
        synth.make_video(p, seconds=4, readout=ro, seed=2)
        fr = decode_gray(p, 320, 240)
        est[ro] = V.rolling_shutter(fr, V.global_motion(fr))["readout_fraction"]
    assert abs(est[0.0]) < 0.05 and est[0.4] > 0.1 and est[0.8] > est[0.4] * 1.4  # proportional (the estimator reads low; see validation)


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_video_timing_gaps_duplicates_and_codec_traces(tmp_path):
    from dfir.forensics.modules import video as V
    from dfir.forensics.modules._av import frame_table
    p = tmp_path / "t.mp4"
    synth.make_video(p, seconds=5, readout=0.3, seed=3, drop=[20, 21, 60], duplicate=[35])
    ts = V.timing_stats(np.array([t["t"] for t in frame_table(p)]))
    assert ts["n_gaps"] == 2 and 3 in ts["gap_multiples"] and 2 in ts["gap_multiples"]
    r = run("video_acquisition", p, "video")
    assert r.finding("codec.reencoded").kind == "observation" and r.finding("timing.gaps").kind == "hypothesis"
    assert r.finding("rolling_shutter") is not None


# ------------------------------------------------------------------------------------------------ module 5: audio
def test_audio_estimators_on_known_ground_truth():
    from dfir.forensics.modules import audio as A
    sr = 22050
    x = synth.add_noise_floor(synth.speech_like(14, sr, 1), sr, -58, seed=1)
    assert not A.hum_analysis(x, sr)["present"]
    h = A.hum_analysis(synth.add_hum(x, sr, 60.0, -45.0), sr)
    assert h["present"] and h["f0"] == 60.0
    assert not A.clipping(synth.speech_like(8, sr, 2))["clipped"]
    assert A.clipping(synth.clip(synth.speech_like(8, sr, 2) * 5, 1.0))["clipped"]
    dry = A.rt60_estimate(synth.add_noise_floor(synth.add_reverb(synth.speech_like(20, sr, 3), sr, 0.5, seed=1), sr, -60, seed=2), sr)
    wet = A.rt60_estimate(synth.add_noise_floor(synth.add_reverb(synth.speech_like(20, sr, 3), sr, 0.9, seed=1), sr, -60, seed=2), sr)
    assert dry["assessable"] and wet["assessable"] and wet["rt60_s"] > dry["rt60_s"]


def test_av_sync_recovers_known_offset_and_ignores_unrelated_signals():
    from dfir.forensics.modules import audio as A
    rng = np.random.default_rng(0)
    fps, n = 20, 600
    env = np.convolve(rng.standard_normal(n), np.hanning(5), "same")
    r = A.av_sync(np.roll(env, 4) + 0.8 * rng.standard_normal(n), env, fps)
    assert r["lag_ms"] == 200 and r["p"] < 0.01
    assert A.av_sync(rng.standard_normal(n), env, fps)["p"] > 0.01
    assert not A.av_sync(env[:50], env[:50], fps)["assessable"]  # too short: Unknown


# ------------------------------------------------------------------------------------------------ module 6: provenance
def test_provenance_never_attributes_a_device_from_metadata_alone(tmp_path):
    from dfir.core.claims import Claim, Status, Strength
    p = jpg(tmp_path, "a.jpg", synth.Sensor(seed=1).capture(synth.scene(1), 1, jpeg_q=92))
    claim = Claim(stage="decomposition", category="metadata", statement="File metadata names a capture device: Samsung SM-S731B", status=Status.VERIFIED,
                  strength=Strength.WEAK, produced_by="metadata@1.0", data={"make": "Samsung", "model": "SM-S731B"})
    r = run("provenance", p, claims_fn=lambda: [claim])
    d = r.finding("exif.device")
    assert d.kind == "observation" and "Samsung" in d.statement and "not validated" in d.caution
    assert r.finding("attribution.device").kind == "unknown"


# ------------------------------------------------------------------------------------------------ integration
@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def wait_jobs(client, case_id, timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not client.get(f"/api/cases/{case_id}/analysis-status").json()["active"]:
            jobs = client.get(f"/api/cases/{case_id}/jobs").json()
            if all(j["status"] in ("done", "error") for j in jobs):
                return jobs
        time.sleep(1)
    raise AssertionError("jobs did not finish")


def test_modules_run_in_the_pipeline_and_land_in_the_vault_claims_and_custody_ledger(client):
    case = client.post("/api/cases", json={"title": "fm", "incident_type": "other"}).json()
    cid = case["id"]
    b = synth.Sensor(seed=9).capture(synth.scene(51), 1, jpeg_q=95)
    r = client.post(f"/api/cases/{cid}/media", files={"file": ("a.jpg", b, "image/jpeg")}, data={"role": "primary"})
    assert r.status_code == 200, r.text
    wait_jobs(client, cid)
    fx = client.get(f"/api/cases/{cid}/forensics").json()
    res = fx["media"][0]["results"]
    assert {x["module"] for x in res} >= {"provenance", "prnu", "camera_pipeline", "editing"}
    assert all(x["result_sha256"] and x["findings"] and x["limitations"] and x["versions"] for x in res)
    # custody ledger has one entry per module run, naming the tool, version and the hash of the stored result
    from dfir.core.db import vault_for
    entries = vault_for(cid).entries() if hasattr(vault_for(cid), "entries") else vault_for(cid).custody.entries()
    fm = [e for e in entries if e["action"].startswith("forensics.")]
    assert len(fm) >= 4 and all(e["details"].get("result_sha256") for e in fm) and all(e.get("tool") for e in fm)
    sha = res[0]["result_sha256"]
    assert vault_for(cid).verify(sha)  # stored, hashed, intact
    # interpretive findings appear as NEUTRAL claims that cannot change the verdict
    claims = client.get(f"/api/cases/{cid}/media/{fx['media'][0]['media_id']}").json()["claims"]
    mod_claims = [c for c in claims if c["category"] == "forensic_module"]
    assert all(c["polarity"] == "neutral" for c in mod_claims)
    # re-running one module replaces only that module's output
    job = client.post(f"/api/cases/{cid}/forensics/run", json={"media_id": fx["media"][0]["media_id"], "modules": ["camera_pipeline"]}).json()
    wait_jobs(client, cid)
    after = client.get(f"/api/cases/{cid}/forensics").json()["media"][0]["results"]
    assert sorted(x["module"] for x in after) == sorted(x["module"] for x in res)


def test_candidate_device_workflow_and_custody(client):
    case = client.post("/api/cases", json={"title": "dev", "incident_type": "other"}).json()
    cid = case["id"]
    S = 384
    s = synth.Sensor(seed=12, h=S, w=S)
    ids = []
    for i in range(5):
        r = client.post(f"/api/cases/{cid}/media", files={"file": (f"ref{i}.jpg", s.capture(synth.scene(60 + i, S, S), i, jpeg_q=95), "image/jpeg")}, data={"role": "reference"})
        ids.append(r.json()["media"]["id"])
    wait_jobs(client, cid)
    assert client.post(f"/api/cases/{cid}/devices", json={"name": "x", "reference_media_ids": ids[:3]}).status_code == 400  # needs 5
    assert client.post(f"/api/cases/{cid}/devices", json={"name": " ", "reference_media_ids": ids}).status_code == 400
    assert client.post(f"/api/cases/{cid}/devices", json={"name": "Phone A", "reference_media_ids": ids}).status_code == 200
    wait_jobs(client, cid)
    devs = client.get(f"/api/cases/{cid}/devices").json()
    assert len(devs) == 1 and devs[0]["n_references"] == 5 and devs[0]["coherent"] and "cannot verify" in devs[0]["attestation"]
    from dfir.core.db import vault_for
    v = vault_for(cid)
    assert v.verify(devs[0]["fingerprint_sha256"])
    ent = [e for e in v.custody.entries() if e["action"] == "forensics.device_fingerprint_created"]
    assert ent and set(ent[0]["details"]["references"]) == {d["sha256"] for d in devs[0]["references"]}
    # a new photo from the same camera is then compared automatically
    own = client.post(f"/api/cases/{cid}/media", files={"file": ("q.jpg", s.capture(synth.scene(99, S, S), 77, jpeg_q=95), "image/jpeg")}, data={"role": "primary"}).json()["media"]
    wait_jobs(client, cid)
    res = next(m for m in client.get(f"/api/cases/{cid}/forensics").json()["media"] if m["media_id"] == own["id"])["results"]
    p = next(x for x in res if x["module"] == "prnu")
    assert any(f["id"].startswith("prnu.match.") and f["kind"] == "supported" for f in p["findings"])


def test_modules_api_lists_validation_and_toggle_persists(client):
    d = client.get("/api/forensics/modules").json()
    assert len(d["modules"]) >= 6 and all("validation" in m and "requirements" in m for m in d["modules"])
    assert client.put("/api/forensics/modules/audio_forensics", json={"enabled": False}).status_code == 200
    assert next(m for m in client.get("/api/forensics/modules").json()["modules"] if m["id"] == "audio_forensics")["enabled"] is False
    client.put("/api/forensics/modules/audio_forensics", json={"enabled": True})
    assert client.put("/api/forensics/modules/nope", json={"enabled": False}).status_code == 404


def test_preferences_validate_and_persist_to_disk(client):
    from dfir.core import prefs
    p = client.get("/api/preferences").json()
    assert p["theme"] == "neomorphism" and p["accent"].startswith("#") and p["profile"]["role"]
    r = client.put("/api/preferences", json={"theme": "claymorphism", "mode": "dark", "accent": "#b24a3a", "motion": "reduce"}).json()
    assert (r["theme"], r["mode"], r["accent"], r["motion"]) == ("claymorphism", "dark", "#b24a3a", "reduce") and r["updated_at"]
    assert json.loads(prefs.PREFS_FILE.read_text())["theme"] == "claymorphism"  # survives a restart
    assert client.get("/api/preferences").json()["accent"] == "#b24a3a"
    for bad in ({"theme": "glass"}, {"mode": "auto"}, {"accent": "red"}, {"motion": "wild"}):
        assert client.put("/api/preferences", json=bad).status_code == 400
    client.put("/api/preferences", json={"theme": "workbench", "mode": "system", "accent": "#2f6f9f", "motion": "system"})


def test_benchmark_results_file_is_well_formed_and_makes_no_real_data_claim_for_synthetic_modules():
    from dfir.forensics.base import validation_data
    d = validation_data()
    if not d:
        pytest.skip("validation_results.json not generated yet")
    assert "simulated" in d["caveat"].lower() or "synthetic" in d["caveat"].lower()
    for mid, m in d["modules"].items():
        assert m["tier"] in ("none", "synthetic", "real")
        assert m["tier"] != "real", f"{mid}: no real labelled camera data has been run, so no module may claim the 'real' tier"


def test_dashboard_search_and_global_overviews(client):
    case = client.post("/api/cases", json={"title": "Dashboard probe zebra", "incident_type": "other", "description": "needle-in-description"}).json()
    d = client.get("/api/dashboard").json()
    assert {"recent", "counts", "alerts", "system"} <= set(d)
    assert any(c["id"] == case["id"] for c in d["recent"])
    assert d["system"]["backend"]["running"] and {m["key"] for m in d["system"]["models"]} == {"clip", "whisper"}
    assert client.get("/api/search", params={"q": "zebra"}).json()["results"][0]["case_id"] == case["id"]
    assert client.get("/api/search", params={"q": "needle-in"}).json()["results"]
    assert client.get("/api/search", params={"q": "x"}).json()["results"] == []  # too short to search
    for url in ("/api/cases-summary", "/api/vault-overview", "/api/monitoring-overview", "/api/reports-overview"):
        assert client.get(url).status_code == 200, url
    r = client.put("/api/preferences", json={"profile": {"name": "  Test User ", "role": ""}}).json()
    assert r["profile"] == {"name": "Test User", "role": "Investigator"}
