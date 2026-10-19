"""Link-first investigation, copy relations, takedown tracker, safety gate and case deletion."""
import http.server
import io
import os
import socketserver
import stat
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import select

from dfir.core.db import case_dir, init_db, session, vault_for
from dfir.core.models import Case, Location, MatchEdge, Media, Report
from dfir.propagation import discover
from dfir.propagation.capture import MEDIA_EXT, _wants_video_first
from dfir.propagation.spread import describe_ops
from dfir.reporting import tracker

init_db()


def make_case(**kw) -> str:
    with session() as s:
        c = Case(title="t", **kw)
        s.add(c)
        s.commit()
        return c.id


def add_media(case_id, role="discovered", loc=None) -> str:
    with session() as s:
        m = Media(case_id=case_id, sha256=os.urandom(32).hex(), modality="image", role=role, label=role, fingerprints={"x": 1})
        m.location_id = loc
        s.add(m)
        s.commit()
        return m.id


def add_loc(case_id, url, media_ids=(), **kw) -> str:
    with session() as s:
        l = Location(case_id=case_id, url=url, state=kw.pop("state", "live"), media_ids=list(media_ids), **kw)
        s.add(l)
        s.commit()
        return l.id


def edge(case_id, a, b, verified):
    with session() as s:
        s.add(MatchEdge(case_id=case_id, a_media=a, b_media=b, verified=verified, tier="geometric" if verified else "hash"))
        s.commit()


# ------------------------------------------------------------------------------------------------ relations
def test_relations_verified_chain_similar_nomatch_and_dismissed():
    cid = make_case()
    p, a, b, c, d = (add_media(cid, "primary"), add_media(cid), add_media(cid), add_media(cid), add_media(cid))
    seed = add_loc(cid, "https://x.test/seed", [p], seed=True)
    la = add_loc(cid, "https://x.test/a", [a])
    lb = add_loc(cid, "https://x.test/b", [b])  # only linked to the suspect media THROUGH copy a
    lc = add_loc(cid, "https://x.test/c", [c])
    ld = add_loc(cid, "https://x.test/d", [d])
    lx = add_loc(cid, "https://x.test/x", [], state="restricted")
    ldis = add_loc(cid, "https://x.test/dis", [a], dismissed=True)
    edge(cid, p, a, True)
    edge(cid, a, b, True)
    edge(cid, p, c, False)
    with session() as s:
        rel = discover.relations(s, cid)
    assert rel[seed]["status"] == "seed"
    assert rel[la]["status"] == "verified_copy"
    assert rel[lb]["status"] == "verified_copy"  # chain of verified matches
    assert rel[lc]["status"] == "similar"  # unverified match never counts as a copy
    assert rel[ld]["status"] == "no_match"
    assert rel[lx]["status"] == "no_media" and "login" in rel[lx]["reason"]
    assert rel[ldis]["status"] == "dismissed"


def test_page_link_candidates_follow_posts_and_same_site_only():
    loc = Location(case_id="c", url="https://news.example.org/story", final_url="https://news.example.org/story", page_meta={"links": [
        {"href": "https://news.example.org/story2"}, {"href": "https://news.example.org/login"},
        {"href": "https://www.instagram.com/p/AbC123/"}, {"href": "https://x.com/someone/status/12345"},
        {"href": "https://other.net/page"}, {"href": "https://news.example.org/story#top"},
        {"href": "https://news.example.org/privacy"}]})
    urls = [u for u, _ in discover.page_link_candidates(loc)]
    assert "https://news.example.org/story2" in urls
    assert "https://www.instagram.com/p/AbC123/" in urls and "https://x.com/someone/status/12345" in urls
    assert not any("login" in u or "privacy" in u or "other.net" in u for u in urls)
    assert "https://news.example.org/story" not in urls  # not the page itself
    # on a social platform only post links are followed, never arbitrary pages
    plat = Location(case_id="c", url="https://www.instagram.com/p/Zzz/", page_meta={"links": [
        {"href": "https://www.instagram.com/somebody/"}, {"href": "https://www.instagram.com/p/Other1/"}]})
    assert [u for u, _ in discover.page_link_candidates(plat)] == ["https://www.instagram.com/p/Other1/"]


def test_reverse_search_links_prefill_only_with_a_public_url():
    assert all(l["prefilled"] for l in discover.reverse_search_links("https://img.example.org/a.jpg"))
    assert not any(l["prefilled"] for l in discover.reverse_search_links(None))
    assert discover._is_private("http://127.0.0.1:8000/x.png") and not discover._is_private("https://img.example.org/a.png")


def test_plain_language_version_differences():
    out = describe_ops([{"op": "mirror"}, {"op": "crop", "kept_region_of_a": [0, 0, 0.5, 0.5]}, {"op": "recompress", "quality": 60},
                        {"op": "localized_change", "regions_in_a": [{}, {}]}, {"op": "format_change", "from": "image/png", "to": "image/jpeg"}])
    assert "Mirrored left-to-right" in out and "Cropped (keeps about 25% of the picture)" in out
    assert "Re-compressed (JPEG quality ≈ 60)" in out
    assert any("2 areas" in t and "possible edit" in t for t in out) and "Different file format (png → jpeg)" in out


# ------------------------------------------------------------------------------------------------ capture helpers
def test_media_url_detection_and_video_first():
    assert MEDIA_EXT.search("https://cdn.example/a/b/pic.JPG?x=1") and MEDIA_EXT.search("https://cdn.example/v.mp4")
    assert not MEDIA_EXT.search("https://example.org/post/12345")
    assert _wants_video_first(Location(case_id="c", url="https://www.youtube.com/watch?v=1", platform="YouTube"))
    assert _wants_video_first(Location(case_id="c", url="https://www.instagram.com/reel/AB/", platform="Instagram"))
    assert not _wants_video_first(Location(case_id="c", url="https://www.instagram.com/p/AB/", platform="Instagram"))


def test_pasted_photo_link_is_downloaded_as_primary_media():
    """A direct image URL is fetched without a browser, vaulted with its HTTP evidence and becomes the suspect media."""
    im = Image.new("RGB", (320, 240), (200, 30, 30))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    data = buf.getvalue()

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Last-Modified", "Wed, 01 Oct 2025 10:00:00 GMT")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        from dfir.propagation.capture import run_capture
        cid = make_case()
        url = f"http://127.0.0.1:{srv.server_address[1]}/photo.png"
        lid = add_loc(cid, url, [], state="pending", seed=True)
        with session() as s:
            r = run_capture(s, cid, s.get(Location, lid), analyse=False)
            assert r["state"] == "live" and r["direct_media"] and len(r["media"]) == 1
            m = s.get(Media, r["media"][0])
            loc = s.get(Location, lid)
            assert m.role == "primary" and m.modality == "image" and (m.width, m.height) == (320, 240)
            assert loc.state == "live" and loc.media_ids == [m.id] and loc.http_status == 200
            assert loc.published_source.startswith("HTTP Last-Modified")  # declared by the server, not proof of creation
        rec = vault_for(cid).record(m.sha256)
        assert rec["acquisition"]["method"].startswith("HTTP download") and rec["acquisition"]["media_url"] == url
    finally:
        srv.shutdown()


# ------------------------------------------------------------------------------------------------ takedown tracker
def test_platform_report_starts_a_two_hour_clock_for_intimate_cases_and_escalates_when_overdue():
    cid = make_case(incident_type="ncii", safety={"intimate": True})
    mid = add_media(cid, "primary")
    lid = add_loc(cid, "https://www.instagram.com/p/AAA/", [mid], seed=True, platform="Instagram")
    with session() as s:
        r = tracker.upsert_report(s, cid, {"key": "platform:Instagram", "kind": "platform", "title": "Report to Instagram",
                                           "target": "Instagram", "status": "submitted", "reference": "IG-1", "location_ids": [lid]})
        assert abs((r.due_at.replace(tzinfo=timezone.utc) - r.submitted_at.replace(tzinfo=timezone.utc)) - timedelta(hours=2)) < timedelta(seconds=5)
        assert "2026" in r.clock_basis or "Rule 3(2)(b)" in r.clock_basis
        plan = tracker.build_plan(s, cid)
        assert not any(a["key"].startswith("escalate:") for a in plan["actions"])
        assert any(a["key"] == "stopncii" for a in plan["actions"])  # intimate case: on-device blocking route offered
        # the deadline passes; a later update without 'kind' must NOT reset the report's kind
        past = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        r2 = tracker.upsert_report(s, cid, {"key": "platform:Instagram", "due_at": past})
        assert r2.kind == "platform"
        plan = tracker.build_plan(s, cid)
        esc = next(a for a in plan["actions"] if a["key"].startswith("escalate:"))
        assert "IG-1" in esc["prepared_text"] and "Grievance" in esc["prepared_text"] and plan["summary"]["overdue"] == 1
        # removal confirmed -> no longer overdue
        tracker.upsert_report(s, cid, {"key": "platform:Instagram", "status": "removed"})
        assert tracker.build_plan(s, cid)["summary"]["overdue"] == 0


def test_minor_case_gets_url_only_route_and_no_stopncii():
    cid = make_case(incident_type="ncii", safety={"minors_possible": True, "intimate": True})
    add_loc(cid, "https://t.me/chan/12", [], platform="Telegram", seed=True)
    with session() as s:
        keys = [a["key"] for a in tracker.build_plan(s, cid)["actions"]]
    assert "minor_report" in keys and "stopncii" not in keys


def test_ncrp_acknowledgement_is_recorded_in_timeline_and_custody():
    cid = make_case(incident_type="other")
    with session() as s:
        r = tracker.upsert_report(s, cid, {"key": "ncrp", "kind": "ncrp", "title": "NCRP complaint", "status": "submitted", "reference": "31234567890123"})
        assert r.reference == "31234567890123" and r.due_at is not None  # a suggested follow-up date, not a legal deadline
        assert "not a legal deadline" in r.clock_basis
    log = vault_for(cid).custody.entries()
    assert any(e["action"] == "report.recorded_by_user" and e["details"]["reference"] == "31234567890123" for e in log)


# ------------------------------------------------------------------------------------------------ safety gate
def test_hive_is_never_used_for_possible_minors_and_always_asks_for_intimate(monkeypatch):
    from dfir.analyzers import detectors_api as api
    from dfir.analyzers.base import Ctx
    h = api.HiveDetector()
    monkeypatch.setattr(api, "hive_auto_upload", lambda: True)

    class Fake:
        def __init__(self, safety):
            self.safety, self.case_id = safety, make_case(safety=safety)

            class V:
                class custody:
                    @staticmethod
                    def append(*a, **k):
                        raise AssertionError("must not upload / log an upload")
            self.vault, self.media = V(), type("M", (), {"sha256": "0" * 64})()

    nr = h._consented(Fake({"minors_possible": True}))
    assert nr is not None and "minor" in nr.reason
    nr = h._consented(Fake({"intimate": True}))  # no consent record -> not uploaded
    assert nr is not None and "no consent" in nr.reason.lower()
    assert Ctx  # imported for parity with analyzers


# ------------------------------------------------------------------------------------------------ deletion
def test_delete_case_requires_confirmation_and_removes_everything_including_readonly_vault():
    from dfir.main import app
    with TestClient(app) as client:
        c = client.post("/api/cases", json={"title": "to delete", "incident_type": "other"}).json()
        cid = c["id"]
        v = vault_for(cid)
        rec = v.store(b"evidence bytes", kind="original", filename="a.bin", mime="application/octet-stream", acquisition={"method": "test"})
        assert not os.access(v.path(rec["sha256"]), os.W_OK) or (os.stat(v.path(rec["sha256"])).st_mode & stat.S_IWRITE) == 0
        client.post(f"/api/cases/{cid}/reports", json={"key": "ncrp", "kind": "ncrp", "title": "x"})
        pre = client.get(f"/api/cases/{cid}/deletion-preview").json()
        assert pre["reports"] == 1 and pre["active_jobs"] == 0
        assert client.delete(f"/api/cases/{cid}").status_code == 400  # no confirmation
        assert client.delete(f"/api/cases/{cid}", params={"confirm": "delete"}).status_code == 400  # must be exactly DELETE
        assert client.get(f"/api/cases/{cid}").status_code == 200
        out = client.delete(f"/api/cases/{cid}", params={"confirm": "DELETE"})
        assert out.status_code == 200 and out.json()["counts"]["Report"] == 1
        assert client.get(f"/api/cases/{cid}").status_code == 404
        from dfir.core.config import DATA_DIR as _D
        assert not (_D / "cases" / cid).exists()  # vault + custody log are gone from disk
        with session() as s:
            assert not s.exec(select(Report).where(Report.case_id == cid)).all()
        from dfir.core.config import DATA_DIR
        assert cid in (DATA_DIR / "deleted_cases.jsonl").read_text(encoding="utf-8")  # a minimal deletion record survives


def test_case_creation_without_title_names_itself_from_the_link(monkeypatch):
    from dfir.api import workflow
    from dfir.main import app
    monkeypatch.setattr(workflow, "start_investigation", lambda s, cid, urls, find, depth: type("J", (), {"model_dump": lambda self, mode=None: {"id": "job_x"}})())
    with TestClient(app) as client:
        c = client.post("/api/cases", json={"urls": ["https://www.instagram.com/p/AbC/"], "incident_type": "ncii"}).json()
        assert c["title"] == "Suspected fake from www.instagram.com" and c["job"]["id"] == "job_x"
