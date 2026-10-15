"""Forensic capture of a public URL.

Captures (when permitted by the case safety policy): HTTP status + headers, final URL, the
rendered HTML, an MHTML snapshot, a full-page screenshot, page metadata (og:/article:/JSON-LD),
visible author/handle and publish time, and the page's media files. Every item goes into the
vault with acquisition details and a custody entry.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import httpx
from sqlmodel import Session

from ..core.claims import Claim, Status, Strength
from ..core.custody import ts_pair
from ..core.db import save_claims, vault_for
from ..core.models import Case, Location
from ..safety.gate import SafetyPolicy
from .platforms import classify

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36 DFIR-evidence-capture")
REMOVED_PATTERNS = re.compile(
    r"(this (content|page|post|video|tweet) (isn't|is not|is no longer) available|post (was )?(not found|removed|deleted)|"
    r"page (not found|isn't available)|video (unavailable|has been removed)|account (suspended|doesn.t exist)|"
    r"sorry, this page isn't available|this tweet was deleted|content removed)", re.I)
LOGIN_PATTERNS = re.compile(r"(log ?in to (see|continue|view)|sign in to (continue|view)|you must log in)", re.I)

EXTRACT_JS = r"""
() => {
  const meta = {};
  document.querySelectorAll('meta[property],meta[name],meta[itemprop]').forEach(m => {
    const k = m.getAttribute('property') || m.getAttribute('name') || m.getAttribute('itemprop');
    if (k && !(k in meta)) meta[k] = m.getAttribute('content');
  });
  const ld = [];
  document.querySelectorAll('script[type="application/ld+json"]').forEach(s => { try { ld.push(JSON.parse(s.textContent)); } catch(e){} });
  const times = [...document.querySelectorAll('time[datetime]')].map(t => t.getAttribute('datetime')).slice(0, 10);
  const imgs = [...document.images].filter(i => i.naturalWidth >= 200 && i.naturalHeight >= 200)
     .map(i => ({src: i.currentSrc || i.src, w: i.naturalWidth, h: i.naturalHeight}));
  const vids = [...document.querySelectorAll('video')].map(v => v.currentSrc || v.src ||
     (v.querySelector('source') && v.querySelector('source').src)).filter(Boolean);
  const authorEl = document.querySelector('[rel=author],[itemprop=author],.author,[data-author]');
  const handles = [...new Set((document.body.innerText.match(/(^|\s)@[A-Za-z0-9_.]{3,30}/g) || []).map(s => s.trim()))].slice(0, 20);
  const counts = {};
  const t = document.body.innerText;
  for (const k of ['views','likes','shares','comments','reposts']) {
    const m = t.match(new RegExp('([\\d.,]+\\s?[KMk]?)\\s+' + k, 'i')); if (m) counts[k] = m[1];
  }
  return {title: document.title, meta, ld, times, imgs, vids, handles, counts,
          author: authorEl ? (authorEl.getAttribute('data-author') || authorEl.textContent.trim()).slice(0, 100) : null,
          text: t.slice(0, 20000)};
}
"""


def _norm_text(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d+\s?(s|m|h|d|w)\b|\d[\d.,]*\s?[KMk]?\s+(views|likes)", "", t or "")).strip().lower()


def _first(*vals):
    return next((v for v in vals if v), None)


def _ld_find(ld, keys):
    stack = list(ld if isinstance(ld, list) else [ld])
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            for k in keys:
                if x.get(k):
                    return x[k]
            stack.extend(x.values())
        elif isinstance(x, list):
            stack.extend(x)
    return None


def parse_time(v) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def capture_page(url: str, *, screenshot: bool = True) -> dict:
    """Render the page in headless Chromium and return raw capture materials."""
    from playwright.sync_api import sync_playwright

    out: dict = {"requested_url": url, "started": ts_pair()}
    with sync_playwright() as p:
        # Inside the Docker image Chromium cannot create its own sandbox; the container (non-root, no extra
        # privileges) is the sandbox there. Never set this on a normal desktop install.
        browser = p.chromium.launch(args=["--no-sandbox"] if os.environ.get("DFIR_CHROMIUM_NO_SANDBOX") == "1" else [])
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900}, locale="en-IN",
                                  timezone_id="Asia/Kolkata")
        page = ctx.new_page()
        try:
            resp = page.goto(url, wait_until="networkidle", timeout=45000)
        except Exception as e:  # noqa: BLE001
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e2:  # noqa: BLE001
                browser.close()
                return {**out, "error": f"{type(e2).__name__}: {str(e2)[:300]}"}
        out["http_status"] = resp.status if resp else None
        out["headers"] = dict(resp.headers) if resp else {}
        out["final_url"] = page.url
        out["html"] = page.content()
        try:
            cdp = ctx.new_cdp_session(page)
            out["mhtml"] = cdp.send("Page.captureSnapshot", {"format": "mhtml"})["data"]
        except Exception:  # noqa: BLE001
            out["mhtml"] = None
        out["page"] = page.evaluate(EXTRACT_JS)
        if screenshot:
            out["screenshot"] = page.screenshot(full_page=True, type="png")
        out["browser"] = f"Chromium {browser.version} via Playwright"
        out["finished"] = ts_pair()
        browser.close()
    return out


def download(url: str, max_bytes: int = 200 * 1024 * 1024) -> tuple[bytes, dict] | None:
    try:
        with httpx.stream("GET", url, headers={"User-Agent": UA}, follow_redirects=True, timeout=60) as r:
            if r.status_code != 200:
                return None
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    return None
            return bytes(buf), {"status": r.status_code, "headers": dict(r.headers), "final_url": str(r.url)}
    except httpx.HTTPError:
        return None


def ytdlp_download(url: str, workdir: Path) -> tuple[Path, dict] | None:
    """Use yt-dlp for public video-host pages. Records version + command for the custody log."""
    import yt_dlp
    workdir.mkdir(parents=True, exist_ok=True)
    opts = {"outtmpl": str(workdir / "%(id)s.%(ext)s"), "noplaylist": True, "quiet": True,
            "max_filesize": 300 * 1024 * 1024, "format": "best[ext=mp4]/best"}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            fn = Path(ydl.prepare_filename(info))
    except Exception:  # noqa: BLE001
        return None
    if not fn.exists():
        return None
    keep = {k: info.get(k) for k in ("id", "title", "uploader", "uploader_id", "channel", "timestamp", "upload_date",
                                     "view_count", "like_count", "description", "webpage_url")}
    return fn, {"tool": f"yt-dlp {yt_dlp.version.__version__}", "info": keep}


def run_capture(s: Session, case_id: str, loc: Location, *, actor: str = "investigator") -> dict:
    """Capture a Location and ingest any media found. Returns a summary dict."""
    from ..pipeline import analyze_media, ingest_media

    case = s.get(Case, case_id)
    policy = SafetyPolicy.from_case(case.safety)
    v = vault_for(case_id)
    cls = classify(loc.url)
    loc.domain, loc.platform = cls["domain"], loc.platform or cls["platform"]
    if not loc.account_handle and cls["handle"]:
        loc.account_handle = cls["handle"]
    v.custody.append("capture.start", actor=actor, details={"url": loc.url, "location_id": loc.id,
                                                            "policy": {"minors_possible": policy.minors_possible}})
    cap = capture_page(loc.url, screenshot=policy.may_screenshot_pages)
    now = datetime.now(timezone.utc)
    loc.last_checked_at = now
    if "error" in cap:
        loc.state = "error"
        loc.notes = (loc.notes + f"\nCapture failed {now.isoformat()}: {cap['error']}").strip()
        s.add(loc)
        s.commit()
        v.custody.append("capture.failed", actor=actor, details={"url": loc.url, "error": cap["error"]})
        return {"state": "error", "error": cap["error"]}

    page = cap.get("page") or {}
    acq = {"method": "headless browser capture", "tool": cap.get("browser"), "requested_url": loc.url,
           "final_url": cap.get("final_url"), "http_status": cap.get("http_status"),
           "started": cap["started"], "finished": cap["finished"], "location_id": loc.id}
    html_rec = v.store(cap["html"].encode("utf-8"), kind="capture_html", filename=f"{loc.id}.html", mime="text/html",
                       acquisition=acq, actor=actor, tool=cap.get("browser"))
    loc.html_sha = html_rec["sha256"]
    if cap.get("mhtml"):
        v.store(cap["mhtml"].encode("utf-8"), kind="capture_mhtml", filename=f"{loc.id}.mhtml",
                mime="multipart/related", acquisition=acq, actor=actor, tool=cap.get("browser"))
    if cap.get("screenshot"):
        shot = v.store(cap["screenshot"], kind="capture_screenshot", filename=f"{loc.id}.png", mime="image/png",
                       acquisition=acq, actor=actor, tool=cap.get("browser"))
        loc.screenshot_sha = shot["sha256"]
    v.store(json.dumps({"headers": cap.get("headers"), "page_meta": page.get("meta"), "ld": page.get("ld")},
                       indent=2).encode(), kind="capture_headers", filename=f"{loc.id}_headers.json",
            mime="application/json", acquisition=acq, actor=actor)

    status = cap.get("http_status") or 0
    text = page.get("text", "")
    prev_fp = loc.content_fingerprint
    new_fp = hashlib.sha256(_norm_text(text).encode()).hexdigest()
    if status in (404, 410) or REMOVED_PATTERNS.search(text[:5000]):
        state = "removed"
    elif status in (401, 403, 429) or (LOGIN_PATTERNS.search(text[:3000]) and len(text) < 3000):
        state = "restricted"
    elif status >= 400:
        state = "error"
    elif prev_fp and prev_fp != new_fp:
        state = "changed"
    else:
        state = "live"
    loc.state = state
    loc.http_status = status
    loc.final_url = cap.get("final_url") or ""
    loc.headers = {k: v_ for k, v_ in (cap.get("headers") or {}).items()
                   if k.lower() in ("date", "last-modified", "server", "content-type", "x-cache", "via", "etag", "age")}
    if state != "removed":
        loc.content_fingerprint = new_fp
    meta = page.get("meta") or {}
    ld = page.get("ld") or []
    loc.page_meta = {"title": page.get("title"), **{k: meta.get(k) for k in (
        "og:title", "og:description", "og:site_name", "og:type", "og:image", "og:video", "article:published_time",
        "article:author", "author", "twitter:creator", "datePublished", "uploadDate") if meta.get(k)},
        "visible_handles": page.get("handles", [])}
    if page.get("counts"):
        loc.reach = page["counts"]
    pub = _first(meta.get("article:published_time"), meta.get("datePublished"), meta.get("uploadDate"),
                 _ld_find(ld, ["datePublished", "uploadDate", "dateCreated"]), (page.get("times") or [None])[0])
    if pub and parse_time(pub) and not loc.published_at:
        loc.published_at = parse_time(pub)
        loc.published_source = "page metadata (as displayed/declared by the site)"
    author = _first(meta.get("article:author"), meta.get("author"), meta.get("twitter:creator"),
                    _ld_find(ld, ["author"]), page.get("author"))
    if isinstance(author, list):
        author = author[0] if author else None
    if isinstance(author, dict):
        author = author.get("alternateName") or author.get("name")
    if author and not loc.account_handle:
        loc.account_handle = str(author).lstrip("@")[:80]
        loc.account_display = str(author)[:120]
    caption = _first(meta.get("og:description"), meta.get("description"))
    if caption and not loc.caption:
        loc.caption = caption[:2000]
    if not loc.first_captured_at:
        loc.first_captured_at = now
    s.add(loc)
    s.commit()

    claims: list[Claim] = []
    shot_ref = [loc.screenshot_sha] if loc.screenshot_sha else []
    claims.append(Claim(case_id=case_id, stage="propagation", category="capture",
                        statement=f"Page {loc.url} captured at {now.isoformat()} — state: {state} (HTTP {status})",
                        status=Status.VERIFIED, strength=Strength.STRONG,
                        why="Captured directly by this system; the HTML and screenshot are hashed in the vault.",
                        limitations="Shows what the public page served to an anonymous visitor at capture time "
                                    "(from this network, in this region).",
                        evidence_refs=[loc.html_sha] + shot_ref, produced_by="capture@1.0",
                        data={"location_id": loc.id, "state": state}))
    if loc.account_handle:
        observed = bool(author or cls["handle"])
        claims.append(Claim(case_id=case_id, stage="propagation", category="account",
                            statement=f"Account “{loc.account_handle}” observed posting at {loc.url}"
                            if observed else f"Investigator noted account “{loc.account_handle}” for {loc.url}",
                            status=Status.VERIFIED if observed else Status.INFERRED, strength=Strength.MODERATE,
                            why="The handle appears in the page's URL or author metadata at capture time."
                            if observed else "Entered by the investigator; not found in the captured page.",
                            limitations="Shows which account posted this copy, not who operates the account or who "
                                        "made the media. Display names can be changed or impersonated.",
                            what_would_confirm="Platform account records (lawful request by police).",
                            evidence_refs=[loc.html_sha] + shot_ref, produced_by="capture@1.0",
                            data={"location_id": loc.id, "handle": loc.account_handle, "platform": loc.platform}))
    if loc.published_at:
        claims.append(Claim(case_id=case_id, stage="propagation", category="time",
                            statement=f"Page declares publication time {loc.published_at.isoformat()} for {loc.url}",
                            status=Status.VERIFIED, strength=Strength.MODERATE,
                            why="Read from the captured page's own metadata.",
                            limitations="The site's declared time; platforms may show edit time or approximate times.",
                            evidence_refs=[loc.html_sha], produced_by="capture@1.0",
                            data={"location_id": loc.id, "published_at": loc.published_at.isoformat()}))
    save_claims(s, claims)
    s.commit()

    ingested = []
    if state in ("live", "changed"):
        if not policy.may_download_discovered_media:
            v.custody.append("capture.media_skipped", actor="system",
                             details={"url": loc.url, "reason": "safety policy: possible minor — media not downloaded"})
        else:
            media_urls = []
            for u in (page.get("vids") or []) + [meta.get("og:video"), meta.get("og:image")] + \
                     [i["src"] for i in sorted(page.get("imgs") or [], key=lambda i: -i["w"] * i["h"])[:3]]:
                if u and not u.startswith("blob:") and urljoin(cap["final_url"], u) not in media_urls:
                    media_urls.append(urljoin(cap["final_url"], u))
            for mu in media_urls[:4]:
                got = download(mu)
                if not got:
                    continue
                data, info = got
                try:
                    m = ingest_media(s, case_id, data, mu.rsplit("/", 1)[-1][:80] or "media", role="discovered",
                                     location_id=loc.id, actor=actor,
                                     label=f"{loc.platform}: {loc.account_handle or loc.domain}",
                                     acquisition={"method": "HTTP download of media embedded in captured page",
                                                  "media_url": mu, "page_url": loc.url, "http": info["status"],
                                                  "time": ts_pair(), "location_id": loc.id})
                    ingested.append(m.id)
                    if m.id not in (loc.media_ids or []):
                        loc.media_ids = [*(loc.media_ids or []), m.id]
                except ValueError:
                    continue
            if not ingested and loc.platform in ("YouTube", "X (Twitter)", "Instagram", "Facebook", "TikTok",
                                                 "Dailymotion", "Reddit"):
                got = ytdlp_download(loc.url, Path(v.root.parent / "tmp_ytdlp"))
                if got:
                    fn, meta_info = got
                    m = ingest_media(s, case_id, fn.read_bytes(), fn.name, role="discovered", location_id=loc.id,
                                     actor=actor, label=f"{loc.platform}: {loc.account_handle or loc.domain}",
                                     acquisition={"method": "yt-dlp download", **meta_info, "page_url": loc.url,
                                                  "time": ts_pair()})
                    fn.unlink(missing_ok=True)
                    ingested.append(m.id)
                    loc.media_ids = [*(loc.media_ids or []), m.id]
        s.add(loc)
        s.commit()
        from ..core.models import Media
        for mid in ingested:
            m = s.get(Media, mid)
            s.refresh(m)
            if not m.fingerprints:  # only newly seen bytes need analysis
                analyze_media(case_id, mid)
    return {"state": state, "media": ingested, "location_id": loc.id}
