"""Build a fully synthetic DEMO case that exercises every investigation stage end-to-end.

No real person is depicted. A procedurally drawn "portrait" plays the role of an AI-generated
image. It carries Stable-Diffusion-style generation parameters and the SDXL reference invisible
watermark, so provenance detection has a real signal to find. Transformed copies (re-encode,
crop + caption, mirror + downscale, screen recording, partial edit) are published on a local
fixture "web" (http://127.0.0.1:8765). The real capture → genealogy → graph → timeline → gaps →
complaint → monitoring pipeline then runs against those pages.

Run:  cd backend && uv run python ../demo/make_demo_case.py
"""
from __future__ import annotations

import functools
import http.server
import io
import shutil
import socketserver
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, PngImagePlugin

ROOT = Path(__file__).resolve().parent
SITE = ROOT / "fixture_site"
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
sys.path.insert(0, str(ROOT.parent / "backend"))

SDXL_BITS = [int(b) for b in bin(0b101100111110110010010000011110111011000110011110)[2:]]
IST = timezone(timedelta(hours=5, minutes=30))


def font(size: int):
    for f in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_subject(seed: int = 7, W: int = 1024, H: int = 1024) -> Image.Image:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:H, 0:W]
    bg = np.stack([60 + 80 * xx / W, 90 + 60 * yy / H, 140 + 40 * np.sin(xx / 90.0)], -1)
    im = Image.fromarray(bg.clip(0, 255).astype(np.uint8))
    d = ImageDraw.Draw(im)
    for _ in range(140):  # textured background (bokeh / city lights)
        x, y, r = rng.integers(0, W), rng.integers(0, H // 2), rng.integers(4, 40)
        c = tuple(int(v) for v in rng.integers(120, 255, 3))
        d.ellipse([x - r, y - r, x + r, y + r], fill=c)
    for _ in range(40):
        x, y = rng.integers(0, W), rng.integers(H // 2, H)
        d.rectangle([x, y, x + rng.integers(20, 120), y + rng.integers(10, 60)],
                    fill=tuple(int(v) for v in rng.integers(30, 120, 3)))
    im = im.filter(ImageFilter.GaussianBlur(2))
    d = ImageDraw.Draw(im)
    cx, cy = W // 2, int(H * 0.47)
    d.ellipse([cx - 230, cy - 300, cx + 230, cy + 120], fill=(48, 30, 22))              # hair
    d.rectangle([cx - 90, cy + 140, cx + 90, cy + 260], fill=(214, 170, 140))           # neck
    d.ellipse([cx - 330, cy + 220, cx + 330, cy + 700], fill=(150, 30, 60))             # shoulders
    d.ellipse([cx - 180, cy - 230, cx + 180, cy + 230], fill=(224, 182, 150))           # face
    for ex in (-75, 75):
        d.ellipse([cx + ex - 38, cy - 60, cx + ex + 38, cy - 20], fill=(255, 255, 255))
        d.ellipse([cx + ex - 16, cy - 56, cx + ex + 16, cy - 24], fill=(60, 40, 30))
        d.arc([cx + ex - 50, cy - 100, cx + ex + 50, cy - 50], 200, 340, fill=(40, 25, 20), width=8)
    d.polygon([(cx, cy - 10), (cx - 24, cy + 60), (cx + 24, cy + 60)], fill=(205, 160, 130))
    d.chord([cx - 70, cy + 80, cx + 70, cy + 150], 0, 180, fill=(170, 60, 70))
    d.text((24, H - 48), "DEMO • SYNTHETIC IMAGE", font=font(26), fill=(255, 255, 255))
    arr = np.asarray(im).astype(np.int16) + rng.normal(0, 4, (H, W, 3))
    return Image.fromarray(arr.clip(0, 255).astype(np.uint8))


def embed_watermark(im: Image.Image) -> Image.Image:
    import cv2
    from imwatermark import WatermarkEncoder
    enc = WatermarkEncoder()
    enc.set_watermark("bits", SDXL_BITS)
    bgr = cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)
    return Image.fromarray(cv2.cvtColor(enc.encode(bgr, "dwtDct"), cv2.COLOR_BGR2RGB))


def jpeg(im: Image.Image, q: int) -> bytes:
    b = io.BytesIO()
    im.convert("RGB").save(b, "JPEG", quality=q)
    return b.getvalue()


def caption_bar(im: Image.Image, text: str) -> Image.Image:
    W, H = im.size
    out = Image.new("RGB", (W, H + 150), (255, 255, 255))
    out.paste(im, (0, 150))
    ImageDraw.Draw(out).text((24, 50), text, font=font(44), fill=(10, 10, 10))
    return out


def screen_recording(src_png: Path, out_mp4: Path) -> None:
    """Simulate a phone screen recording: the image inside app chrome, slow zoom, 30 fps, H.264."""
    im = Image.open(src_png).convert("RGB")
    W, H = 720, 1280
    frame = Image.new("RGB", (W, H), (0, 0, 0))
    d = ImageDraw.Draw(frame)
    d.rectangle([0, 0, W, 120], fill=(24, 24, 28))
    d.text((24, 40), "◀  Reels   @leaky_news", font=font(34), fill=(240, 240, 240))
    d.rectangle([0, H - 160, W, H], fill=(24, 24, 28))
    d.text((24, H - 120), "♥ 12.4K   💬 1,203   ↗ share", font=font(30), fill=(240, 240, 240))
    fit = im.copy()
    fit.thumbnail((W, H - 300))
    frame.paste(fit, ((W - fit.width) // 2, 140 + (H - 300 - fit.height) // 2))
    tmp = out_mp4.with_suffix(".frame.png")
    frame.save(tmp)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(tmp), "-f", "lavfi", "-i",
                    "sine=frequency=330:duration=6", "-vf",
                    "zoompan=z='min(zoom+0.0007,1.06)':d=180:s=720x1280:fps=30,format=yuv420p",
                    "-t", "6", "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-c:a", "aac", "-shortest",
                    str(out_mp4)], check=True)
    tmp.unlink()


POSTS = [
    # slug, platform label, handle, published (IST), caption, media filename, kind
    ("forum/thread-4412", "Forum [DEMO]", "anon_uploader_77", datetime(2026, 9, 28, 23, 14, tzinfo=IST),
     "new 'leak' lol, who is she? #exposed #viral", "v0_generator_output.png", "img"),
    ("x/status/18440021", "X (Twitter) [DEMO]", "rumour_mill_in", datetime(2026, 9, 29, 9, 2, tzinfo=IST),
     "Is this real?? Shocking pic going around #exposed #breaking", "v1_reencoded.jpg", "img"),
    ("instagram/p/Cx9demo", "Instagram [DEMO]", "leaky_news", datetime(2026, 9, 30, 18, 45, tzinfo=IST),
     "@leaky_news exclusive 🔥 #viral #exposed #trending", "v2_crop_caption.jpg", "img"),
    ("telegram/s/spicyleaks_demo/311", "Telegram channel [DEMO]", "spicyleaks_demo", datetime(2026, 10, 1, 1, 30, tzinfo=IST),
     "forwarded many times. full album in bio #leak", "v3_mirror_small.jpg", "img"),
    ("video/watch/a8f3", "Video host [DEMO]", "trend_clips_hub", datetime(2026, 10, 2, 14, 0, tzinfo=IST),
     "Screen recording of viral reel #viral", "v4_screen_recording.mp4", "video"),
    ("blog/2026/10/viral-photo-explained", "Blog [DEMO]", "gossipdesk", datetime(2026, 10, 3, 11, 20, tzinfo=IST),
     "Our desk looked into the viral photo. Edited version circulating. #factcheck", "v5_partial_edit.jpg", "img"),
    ("x/status/18440999", "X (Twitter) [DEMO]", "rum0ur_mill_in", datetime(2026, 10, 4, 8, 10, tzinfo=IST),
     "reposting before it gets taken down #exposed", "v1_reencoded.jpg", "img"),
]


def page_html(slug, platform, handle, published, caption, media, kind) -> str:
    media_tag = (f'<video controls width="420" src="/media/{media}"></video>' if kind == "video"
                 else f'<img src="/media/{media}" alt="post image" style="max-width:520px">')
    og = f'<meta property="og:video" content="/media/{media}">' if kind == "video" else \
        f'<meta property="og:image" content="/media/{media}">'
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{platform} — @{handle}</title>
<meta property="og:site_name" content="{platform}">
<meta property="og:description" content="{caption}">
<meta name="author" content="@{handle}">
<meta property="article:published_time" content="{published.isoformat()}">
{og}
<style>body{{font-family:Segoe UI,Arial;background:#f5f5f7;margin:0}}.bar{{background:#7c2d12;color:#fff;padding:6px 14px;font-size:13px}}
.post{{background:#fff;max-width:600px;margin:24px auto;padding:18px;border-radius:12px;box-shadow:0 1px 4px #0002}}
.h{{font-weight:700}}.t{{color:#666;font-size:13px}}</style></head><body>
<div class="bar">DEMO FIXTURE — synthetic page for testing the investigation tool. Not a real platform.</div>
<div class="post"><div class="h">@{handle}</div><div class="t"><time datetime="{published.isoformat()}">{published.strftime('%d %b %Y %H:%M IST')}</time> · {platform}</div>
<p>{caption}</p>{media_tag}<p class="t">{np.random.default_rng(len(slug)).integers(200, 90000):,} views · {np.random.default_rng(len(slug) + 1).integers(10, 4000):,} likes</p></div>
</body></html>"""


def build_site() -> dict[str, Path]:
    if SITE.exists():
        shutil.rmtree(SITE)
    (SITE / "media").mkdir(parents=True)
    subject = embed_watermark(make_subject())
    info = PngImagePlugin.PngInfo()
    info.add_text("parameters", "portrait photo of a young woman, city lights bokeh, 85mm, photorealistic\n"
                                "Negative prompt: cartoon, lowres\nSteps: 30, Sampler: DPM++ 2M Karras, CFG scale: 6.5, "
                                "Seed: 1928374650, Size: 1024x1024, Model hash: 31e35c80fc, Model: sd_xl_base_1.0, "
                                "Version: v1.10.1 [DEMO]")
    files = {}
    p = SITE / "media" / "v0_generator_output.png"
    subject.save(p, pnginfo=info)
    files["v0"] = p
    p = SITE / "media" / "v1_reencoded.jpg"
    p.write_bytes(jpeg(subject, 82))
    files["v1"] = p
    crop = subject.crop((112, 60, 912, 860))
    capt = caption_bar(crop, "@leaky_news  EXCLUSIVE 🔥")
    p = SITE / "media" / "v2_crop_caption.jpg"
    p.write_bytes(jpeg(capt, 78))
    files["v2"] = p
    p = SITE / "media" / "v3_mirror_small.jpg"
    p.write_bytes(jpeg(ImageOps.mirror(Image.open(SITE / "media" / "v1_reencoded.jpg")).resize((512, 512)), 60))
    files["v3"] = p
    v2png = SITE / "media" / "_v2.png"
    Image.open(SITE / "media" / "v2_crop_caption.jpg").save(v2png)
    p = SITE / "media" / "v4_screen_recording.mp4"
    screen_recording(v2png, p)
    v2png.unlink()
    files["v4"] = p
    edited = Image.open(SITE / "media" / "v1_reencoded.jpg").convert("RGB")
    d = ImageDraw.Draw(edited)
    d.rounded_rectangle([640, 700, 960, 900], 30, fill=(250, 210, 40))
    d.text((680, 770), "CENSORED", font=font(48), fill=(20, 20, 20))
    p = SITE / "media" / "v5_partial_edit.jpg"
    p.write_bytes(jpeg(edited, 85))
    files["v5"] = p
    for slug, plat, handle, pub, cap, media, kind in POSTS:
        page = SITE / f"{slug}.html"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(page_html(slug, plat, handle, pub, cap, media, kind), encoding="utf-8")
    # what the victim received on a messaging app: a forwarded, recompressed, downscaled copy of the Instagram version
    forwarded = Image.open(SITE / "media" / "v2_crop_caption.jpg").resize((640, 760))
    victim_copy = ROOT / "victim_received_copy.jpg"
    victim_copy.write_bytes(jpeg(forwarded, 55))
    files["victim"] = victim_copy
    return files


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_head(self):
        # serve /x/status/123 from x/status/123.html
        path = self.translate_path(self.path)
        if not Path(path).exists() and Path(path + ".html").exists():
            self.path += ".html"
        return super().send_head()


def serve() -> socketserver.TCPServer:
    handler = functools.partial(_Quiet, directory=str(SITE))
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", PORT), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main():
    import socket

    from sqlmodel import select

    from dfir.core.db import init_db, session, vault_for
    from dfir.core.models import Case, MonitorConfig, TimelineNote
    from dfir.monitor.scheduler import run_monitor
    from dfir.pipeline import analyze_media, ingest_media
    from dfir.propagation.intake import add_locations, capture_locations
    from dfir.reporting.complaint import get_or_create

    print("Building fixture site …")
    files = build_site()
    httpd = None
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", PORT)) != 0:
            httpd = serve()
            print(f"Serving fixtures at {BASE}")
        else:
            print(f"Fixture server already running at {BASE}")
    init_db()
    with session() as s:
        for old in s.exec(select(Case).where(Case.is_demo == True)).all():  # noqa: E712
            old.status = "archived"
            s.add(old)
        case = Case(title="DEMO — Morphed image of a private individual circulating online", incident_type="ncii",
                    description="A friend forwarded me an image that appears to show me, but I never took this photo. "
                                "It is being shared with my name. [DEMO: synthetic scenario]",
                    safety={"intimate": True, "minors_possible": False}, is_demo=True,
                    complainant={"name": "(demo complainant)", "contact": "(not provided)"})
        s.add(case)
        s.commit()
        cid = case.id
        vault_for(cid).custody.append("case.opened", actor="investigator", details={"demo": True})
        m = ingest_media(s, cid, files["victim"].read_bytes(), "whatsapp_forward_IMG-20261004-WA0012.jpg",
                         label="Copy received by the complainant (forwarded)",
                         acquisition={"method": "user upload", "note": "received as a forwarded message [DEMO]"})
        mid = m.id
    print("Analysing the complainant's copy …")
    analyze_media(cid, mid, lambda f, msg: print(f"  {f:4.0%} {msg}"))
    with session() as s:
        add_locations(s, cid, [{"url": f"{BASE}/{slug}", "platform": plat, "handle": handle,
                                "notes": "DEMO fixture page"} for slug, plat, handle, *_ in POSTS])
    print("Capturing locations (headless browser) …")
    capture_locations(cid, None, lambda f, msg: print(f"  {f:4.0%} {msg}"))
    with session() as s:
        s.add(TimelineNote(case_id=cid, at=datetime(2026, 10, 4, 21, 5, tzinfo=IST),
                           title="Complainant received the forwarded image from a friend",
                           detail="Reported by the complainant [DEMO]", kind="user_note"))
        s.add(MonitorConfig(case_id=cid, enabled=False, interval_minutes=360))
        s.commit()
        get_or_create(s, cid, regenerate=True)
    # Simulate a takedown: the Telegram post disappears, then monitoring notices.
    (SITE / "telegram/s/spicyleaks_demo/311.html").unlink()
    print("Running a monitoring pass (one page has been taken down) …")
    run_monitor(cid, lambda f, msg: print(f"  {f:4.0%} {msg}"))
    print(f"\nDEMO case ready: {cid}")
    if httpd:
        httpd.shutdown()


if __name__ == "__main__":
    main()
