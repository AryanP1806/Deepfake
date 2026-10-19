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
from urllib.parse import urljoin, urlparse

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
LOGIN_CAPTION = re.compile(r"(create an account or log in|log in (or sign up )?to (see|continue|view|instagram|facebook)|sign up to see|"
                          r"see photos and videos from|join facebook)", re.I)
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
  const links = [...document.querySelectorAll('a[href]')].map(a => ({href: a.href, text: (a.textContent || '').trim().slice(0, 80)}))
     .filter(l => /^https?:/.test(l.href)).slice(0, 300);
  return {title: document.title, meta, ld, times, imgs, vids, handles, counts, links,
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
        pass
    try:  # HTTP dates, e.g. the Last-Modified header: "Wed, 01 Oct 2025 10:00:00 GMT"
        from email.utils import parsedate_to_datetime
        d = parsedate_to_datetime(str(v).strip())
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        return None


def capture_page(url: str, *, screenshot: bool = True, mhtml: bool = True) -> dict:
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
            # Real sites (ads, trackers) often never reach "network idle", so do not wait for it: load the document,
            # then give lazy content a short, bounded chance to settle.
            resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e2:  # noqa: BLE001
            browser.close()
            return {**out, "error": f"{type(e2).__name__}: {str(e2)[:300]}"}
        try:
            page.wait_for_load_state("networkidle", timeout=4000 if not mhtml else 9000)
        except Exception:  # noqa: BLE001 — best effort only
            pass
        out["http_status"] = resp.status if resp else None
        out["headers"] = dict(resp.headers) if resp else {}
        out["final_url"] = page.url
        out["html"] = page.content()
        try:
            cdp = ctx.new_cdp_session(page)
            out["mhtml"] = cdp.send("Page.captureSnapshot", {"format": "mhtml"})["data"] if mhtml else None
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


LAST_YTDLP_ERROR = ""  # why the most recent yt-dlp download failed (shown to the user instead of failing silently)


def _js_runtimes() -> dict:
    """YouTube needs a JavaScript runtime for yt-dlp. It only auto-uses Deno, so enable whichever one is installed."""
    import shutil
    return {name: {} for name in ("deno", "node", "bun") if shutil.which(name)}


def ytdlp_download(url: str, workdir: Path) -> tuple[Path, dict] | None:
    """Use yt-dlp for public video-host pages. Records version + command for the custody log."""
    global LAST_YTDLP_ERROR
    import yt_dlp
    LAST_YTDLP_ERROR = ""
    workdir.mkdir(parents=True, exist_ok=True)
    opts = {"outtmpl": str(workdir / "%(id)s.%(ext)s"), "noplaylist": True, "quiet": True, "no_warnings": True,
            "max_filesize": 300 * 1024 * 1024, "format": "bv*[height<=1080]+ba/b[height<=1080]/b", "merge_output_format": "mp4",
            "js_runtimes": _js_runtimes()}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            done = (info.get("requested_downloads") or [{}])[0].get("filepath")  # the merged file, when video+audio were joined
            fn = Path(done or ydl.prepare_filename(info))
    except Exception as e:  # noqa: BLE001
        msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e)).replace("ERROR: ", "").strip()
        LAST_YTDLP_ERROR = msg[:240]
        return None
    if not fn.exists():
        LAST_YTDLP_ERROR = "the download finished but no file was produced"
        return None
    keep = {k: info.get(k) for k in ("id", "title", "uploader", "uploader_id", "channel", "timestamp", "upload_date",
                                     "view_count", "like_count", "description", "webpage_url")}
    return fn, {"tool": f"yt-dlp {yt_dlp.version.__version__}", "info": keep}


def _friendly_video_error(platform: str, raw: str) -> str:
    r = raw.lower()
    if any(k in r for k in ("login", "sign in", "log in", "cookies", "private", "rate-limit", "authentication")):
        why = f"{platform or 'The site'} wants a login to show this video, and this app only opens public pages as a logged-out visitor"
    elif "not available" in r or "unavailable" in r or "removed" in r:
        why = "the video is unavailable (removed, private, or blocked in this region)"
    else:
        why = raw or "the download failed"
    return f"Could not download the video: {why}. Save the video yourself and upload the file instead."


MEDIA_EXT = re.compile(r"\.(jpe?g|png|webp|gif|bmp|heic|avif|mp4|mov|webm|mkv|m4v|avi|mp3|wav|m4a|ogg|opus|flac)(\?|$)", re.I)
# platforms whose posts are normally videos: let yt-dlp fetch the real video before falling back to the page's
# preview image (otherwise a YouTube/TikTok link would only ever yield its thumbnail)
VIDEO_FIRST = {"YouTube", "TikTok", "Dailymotion", "X (Twitter)"}


def probe_url(url: str) -> dict | None:
    """If the URL itself serves an image/video/audio file (e.g. a pasted photo link), say so — no browser needed."""
    try:
        with httpx.stream("GET", url, headers={"User-Agent": UA}, follow_redirects=True, timeout=30) as r:
            ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
            if r.status_code == 200 and (ctype.startswith(("image/", "video/", "audio/")) or
                                         (ctype in ("application/octet-stream", "binary/octet-stream", "")
                                          and MEDIA_EXT.search(str(r.url)))):
                return {"content_type": ctype, "final_url": str(r.url), "status": r.status_code}
    except httpx.HTTPError:
        return None
    return None


def _adopt_platform_info(loc: Location, info: dict) -> None:
    """The post's account, caption, time and counts as the platform declares them (via yt-dlp). Never overwrites what the page gave."""
    handle = info.get("channel") or info.get("uploader_id")
    if handle and not loc.account_handle and re.match(r"^[\w.]{2,40}$", str(handle)):
        loc.account_handle = str(handle).lstrip("@")
    if info.get("uploader") and not loc.account_display:
        loc.account_display = str(info["uploader"])[:120]
    if info.get("description") and (not loc.caption or LOGIN_CAPTION.search(loc.caption)):  # also replaces a stored login-wall text
        loc.caption = str(info["description"])[:2000]
    ts = info.get("timestamp")
    if ts and not loc.published_at:
        try:
            loc.published_at = datetime.fromtimestamp(int(ts), tz=timezone.utc)
            loc.published_source = "platform metadata read by yt-dlp (declared by the platform)"
        except (ValueError, OSError):
            pass
    counts = {k: info[k] for k in ("like_count", "view_count", "comment_count", "repost_count") if info.get(k) not in (None, "")}
    if counts and not loc.reach:
        loc.reach = {k.replace("_count", "s"): str(v) for k, v in counts.items()}


def _is_video_page(loc: Location) -> bool:
    """Pages that are a video and nothing else: their preview image is a thumbnail, not the suspect media."""
    path = urlparse(loc.url).path.lower()
    if loc.platform in ("YouTube", "TikTok", "Dailymotion"):
        return True
    if loc.platform == "Instagram":
        return "/reel" in path or "/tv/" in path
    if loc.platform == "Facebook":
        return any(k in path for k in ("/videos/", "/reel", "/watch")) or "fb.watch" in loc.url
    return False


def _wants_video_first(loc: Location) -> bool:
    path = urlparse(loc.url).path.lower()
    if loc.platform in VIDEO_FIRST:
        return True
    if loc.platform == "Instagram":
        return "/reel" in path or "/tv/" in path
    if loc.platform == "Facebook":
        return any(k in path for k in ("/videos/", "/reel", "/watch")) or "fb.watch" in loc.url
    return False


def _capture_direct_media(s: Session, case_id: str, loc: Location, probe: dict, *, actor: str, analyse: bool) -> dict | None:
    """The pasted URL is a media file: download it, keep the HTTP evidence, ingest it. None = not usable as media."""
    from ..pipeline import analyze_media, ingest_media
    v = vault_for(case_id)
    got = download(probe["final_url"])
    if not got:
        return None
    data, info = got
    acq = {"method": "HTTP download of a directly linked media file", "media_url": probe["final_url"],
           "requested_url": loc.url, "http": info["status"], "content_type": probe["content_type"],
           "time": ts_pair(), "location_id": loc.id}
    try:
        m = ingest_media(s, case_id, data, urlparse(probe["final_url"]).path.rsplit("/", 1)[-1][:80] or "media",
                         role="primary" if loc.seed else "discovered", location_id=loc.id, actor=actor,
                         label=f"{loc.platform or loc.domain}: direct media link", acquisition=acq)
    except ValueError:
        return None
    now = datetime.now(timezone.utc)
    v.store(json.dumps({"headers": info["headers"], "requested_url": loc.url, "final_url": info["final_url"]}, indent=2).encode(),
            kind="capture_headers", filename=f"{loc.id}_headers.json", mime="application/json", acquisition=acq, actor=actor)
    loc.state, loc.http_status, loc.final_url = "live", info["status"], info["final_url"]
    loc.last_checked_at = now
    loc.first_captured_at = loc.first_captured_at or now
    loc.headers = {k: v_ for k, v_ in info["headers"].items()
                   if k.lower() in ("date", "last-modified", "server", "content-type", "x-cache", "via", "etag", "age")}
    loc.page_meta = {"direct_media": True, "content_type": probe["content_type"]}
    loc.media_ids = [*(loc.media_ids or []), m.id] if m.id not in (loc.media_ids or []) else loc.media_ids
    lm = info["headers"].get("last-modified") or info["headers"].get("Last-Modified")
    if lm and not loc.published_at and parse_time(lm):
        loc.published_at, loc.published_source = parse_time(lm), "HTTP Last-Modified header (server-declared, not proof of creation)"
    s.add(loc)
    s.commit()
    v.custody.append("capture.direct_media", actor=actor, item_sha256=m.sha256, details={"url": loc.url, "location_id": loc.id})
    if analyse and not m.fingerprints:
        analyze_media(case_id, m.id)
    return {"state": "live", "media": [m.id], "location_id": loc.id, "direct_media": True}


def run_capture(s: Session, case_id: str, loc: Location, *, actor: str = "investigator", analyse: bool = True,
                light: bool = False) -> dict:
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
    if policy.may_download_discovered_media and (probe := probe_url(loc.url)):
        res = _capture_direct_media(s, case_id, loc, probe, actor=actor, analyse=analyse)
        if res is not None:
            return res
    # light = candidate screening: no full-page screenshot / MHTML (slow on heavy sites). Pages that turn out to be
    # real copies are captured fully afterwards.
    cap = capture_page(loc.url, screenshot=policy.may_screenshot_pages and not light, mhtml=not light)
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
        "visible_handles": page.get("handles", []),
        "links": [l for l in (page.get("links") or [])][:120]}
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
    if caption and not loc.caption and not LOGIN_CAPTION.search(caption):  # a login wall's text is not the post's caption
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
    video_error = ""
    if state in ("live", "changed"):
        if not policy.may_download_discovered_media:
            v.custody.append("capture.media_skipped", actor="system",
                             details={"url": loc.url, "reason": "safety policy: possible minor — media not downloaded"})
        else:
            def _role() -> str:  # the first media from the link the user started with is the suspect media
                return "primary" if loc.seed and not ingested else "discovered"

            def _label() -> str:
                return f"{loc.platform}: {loc.account_handle or loc.domain}"

            def _ytdlp() -> bool:
                got = ytdlp_download(loc.url, Path(v.root.parent / "tmp_ytdlp"))
                if not got:
                    return False
                fn, meta_info = got
                m = ingest_media(s, case_id, fn.read_bytes(), fn.name, role=_role(), location_id=loc.id, actor=actor,
                                 label=_label(), acquisition={"method": "yt-dlp download", **meta_info,
                                                              "page_url": loc.url, "time": ts_pair()})
                fn.unlink(missing_ok=True)
                ingested.append(m.id)
                loc.media_ids = [*(loc.media_ids or []), m.id] if m.id not in (loc.media_ids or []) else loc.media_ids
                _adopt_platform_info(loc, meta_info.get("info") or {})
                return True

            got_video = _wants_video_first(loc) and _ytdlp()
            if _wants_video_first(loc) and not got_video:
                video_error = _friendly_video_error(loc.platform, LAST_YTDLP_ERROR)
            skip_page_media = got_video or (bool(video_error) and _is_video_page(loc))  # a thumbnail is not the video
            media_urls = []
            for u in ([] if skip_page_media else (page.get("vids") or []) + [meta.get("og:video"), meta.get("og:image")] +
                      [i["src"] for i in sorted(page.get("imgs") or [], key=lambda i: -i["w"] * i["h"])[:3]]):
                if u and not u.startswith("blob:") and urljoin(cap["final_url"], u) not in media_urls:
                    media_urls.append(urljoin(cap["final_url"], u))
            for mu in media_urls[:4 if (loc.seed or loc.depth == 0) else 2]:  # candidates: only the main picture(s)
                got = download(mu)
                if not got:
                    continue
                data, info = got
                try:
                    m = ingest_media(s, case_id, data, mu.rsplit("/", 1)[-1][:80] or "media", role=_role(),
                                     location_id=loc.id, actor=actor, label=_label(),
                                     acquisition={"method": "HTTP download of media embedded in captured page",
                                                  "media_url": mu, "page_url": loc.url, "http": info["status"],
                                                  "time": ts_pair(), "location_id": loc.id})
                    ingested.append(m.id)
                    if m.id not in (loc.media_ids or []):
                        loc.media_ids = [*(loc.media_ids or []), m.id]
                except ValueError:
                    continue
            if not ingested and not video_error and loc.platform in ("YouTube", "X (Twitter)", "Instagram", "Facebook",
                                                                      "TikTok", "Dailymotion", "Reddit"):
                if not _ytdlp():
                    video_error = _friendly_video_error(loc.platform, LAST_YTDLP_ERROR)
            if video_error and not ingested:
                loc.notes = (loc.notes + "\n" + video_error).strip()
                v.custody.append("capture.video_failed", actor=actor, details={"url": loc.url, "reason": video_error})
        s.add(loc)
        s.commit()
        if analyse:
            from ..core.models import Media
            for mid in ingested:
                m = s.get(Media, mid)
                s.refresh(m)
                if not m.fingerprints:  # only newly seen bytes need analysis
                    analyze_media(case_id, mid)
    return {"state": state, "media": ingested, "location_id": loc.id, "video_error": video_error}
