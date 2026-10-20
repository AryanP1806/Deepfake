#!/usr/bin/env python3
"""Export saved DEMO cases so the frontend can run with NO backend (for example on Vercel).

The hosted site cannot run the analysis backend (it needs PyTorch, ~4 GB of models, ffmpeg, Chromium and a writable disk), so it
shows a few complete, already-analysed cases from static files instead. This script builds those files from your own data:

  1. copies ONLY the chosen cases (database rows + evidence folders) into a scratch data directory, so nothing else you have
     investigated can leak into the export;
  2. renames the cases, clears personal complaint details, and runs the forensic modules where they are missing;
  3. starts a throw-away backend on that scratch data and saves every read-only API response the frontend uses
     (frontend/public/demo/api/*.json), plus the evidence images those responses point to (frontend/public/demo/vault/*);
  4. refuses to finish if the output still contains local paths, e-mail addresses or key-like strings.

Run from backend/:

    uv run --extra provenance --extra capture python ../scripts/export_demo.py
    uv run --extra provenance --extra capture python ../scripts/export_demo.py --cases case_a,case_b

The file name of each response is derived from the request URL by `demoKey()`; frontend/src/lib/demo.ts has the same function.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

# The saved demos. Titles and blurbs are what visitors see; the case ids refer to your own data directory.
DEMOS = [
    {"id": "case_6c6f19082755",
     "title": "Demo 1 · AI-generated school photo of a celebrity",
     "blurb": "A viral AI image from a Boredpanda article. Shows the verdict, where it was found, forensic modules and what to do next.",
     "source_url": "https://www.boredpanda.com/school-photos-of-celebrities-created-with-ai/"},
    {"id": "case_89a1fd7baac3",
     "title": "Demo 2 · Face-swap meme found through an Instagram post",
     "blurb": "A face-swapped meme traced from an Instagram post to copies on other accounts. Shows the spread map, versions and leads.",
     "source_url": "https://www.instagram.com/p/DMzd8nbIqAo/"},
    {"id": "case_5bab1606628a",
     "title": "Demo 3 · Morphed image of a private individual (fully synthetic)",
     "blurb": "A procedurally drawn stand-in, so no real person is shown. Walks through every stage: capture, genealogy, timeline, complaint and monitoring.",
     "source_url": ""},
]

MAX_IMAGE_BYTES = 250_000
MAX_PNG_BYTES = 120_000
MAX_IMAGE_SIDE = 1200
MAX_TEXT_BYTES = 120_000
KEEP_KINDS = {"original", "download", "artifact", "capture_screenshot"}  # page HTML/MHTML/headers are not shown by the UI and are large
MAX_VIDEO_BYTES = 4_000_000
EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif", "application/json": "json",
       "text/plain": "txt", "video/mp4": "mp4", "application/pdf": "pdf"}
SHA = re.compile(r"\b[0-9a-f]{64}\b")


def demo_key(url: str) -> str:
    """Same rule as demoKey() in frontend/src/lib/demo.ts."""
    url = re.sub(r"^/api/", "", url)
    path, _, query = url.partition("?")
    return f"{path}__q__{re.sub(r'[^A-Za-z0-9_=.-]', '-', query)}" if query else path


def rmtree(path: Path) -> None:
    """Evidence files are stored read-only, so make them writable before deleting a scratch copy."""
    def fix(func, p, _exc):
        os.chmod(p, 0o700)
        func(p)
    shutil.rmtree(path, onerror=fix)


def log(msg: str) -> None:
    print(f"[export {time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ------------------------------------------------------------------------------------------------ step 1: scratch data
def prepare(source: Path, work: Path, demos: list[dict]) -> Path:
    data = work / "data"
    if work.exists():
        rmtree(work)
    (data / "cases").mkdir(parents=True)
    keep = [d["id"] for d in demos]
    src = sqlite3.connect(f"file:{(source / 'dfir.sqlite3').as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(data / "dfir.sqlite3")
    src.backup(dst)
    src.close()
    qs = ",".join("?" * len(keep))
    tables = [r[0] for r in dst.execute("select name from sqlite_master where type='table'")]
    for t in tables:
        cols = [r[1] for r in dst.execute(f'pragma table_info("{t}")')]
        if t == "case":
            dst.execute(f'delete from "case" where id not in ({qs})', keep)
        elif t in ("consent", "job", "monitorconfig", "complaint"):
            dst.execute(f'delete from "{t}"')  # consents and jobs are local history; complaints may hold personal details
        elif "case_id" in cols:
            dst.execute(f'delete from "{t}" where case_id is null or case_id not in ({qs})', keep)
    for d in demos:
        desc = d["blurb"] + (f" Source: {d['source_url']}" if d["source_url"] else "")
        dst.execute('update "case" set title=?, description=?, is_demo=1, complainant=? where id=?', (d["title"], desc, "{}", d["id"]))
    dst.commit()
    dst.execute("vacuum")
    dst.close()
    for cid in keep:
        shutil.copytree(source / "cases" / cid, data / "cases" / cid)
    log(f"scratch data ready with {len(keep)} cases")
    return data


# ------------------------------------------------------------------------------------------------ step 2/3: crawl
class Backend:
    def __init__(self, data: Path, port: int):
        self.port = port
        env = {**os.environ, "DFIR_DATA_DIR": str(data), "DFIR_WARMUP": "0", "DFIR_NO_DOTENV": "1", "HIVE_AUTO_UPLOAD": "0", "PYTHONUTF8": "1"}
        self.p = subprocess.Popen([sys.executable, "-m", "uvicorn", "dfir.main:app", "--port", str(port), "--host", "127.0.0.1"], cwd=BACKEND, env=env,
                                  stdout=(data.parent / "backend.log").open("w"), stderr=subprocess.STDOUT)
        self.c = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=180)
        for _ in range(120):
            try:
                if self.c.get("/api/health").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(1)
        raise SystemExit("the scratch backend did not start; see tmp/demo_build/backend.log")

    def stop(self) -> None:
        self.p.terminate()
        try:
            self.p.wait(15)
        except subprocess.TimeoutExpired:
            self.p.kill()


def ensure_forensics(b: Backend, demos: list[dict]) -> None:
    """Run the forensic modules on each case's primary image if it has no results yet (cheap, local, no outside service)."""
    for d in demos:
        fx = b.c.get(f"/api/cases/{d['id']}/forensics").json()
        for m in fx["media"]:
            if m["role"] == "primary" and m["modality"] == "image" and not m["results"]:
                log(f"running forensic modules on {m['label'][:40]}")
                b.c.post(f"/api/cases/{d['id']}/forensics/run", json={"media_id": m["media_id"]})
        for _ in range(180):
            jobs = b.c.get(f"/api/cases/{d['id']}/jobs").json()
            if all(j["status"] in ("done", "error") for j in jobs):
                break
            time.sleep(2)


def crawl(b: Backend, demos: list[dict], out: Path) -> tuple[list[str], dict]:
    api_dir = out / "api"
    keys: list[str] = []

    def save(url: str) -> dict | list | None:
        r = b.c.get(url)
        ctype = r.headers.get("content-type", "")
        if r.status_code != 200 or "json" not in ctype:
            log(f"  skipped {url} ({r.status_code})")
            return None
        key = demo_key(url)
        f = api_dir / f"{key}.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(r.json(), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        keys.append(key)
        return r.json()

    for url in ("/api/cases", "/api/cases-summary?archived=false", "/api/cases-summary?archived=true", "/api/dashboard?limit=6", "/api/vault-overview",
                "/api/monitoring-overview", "/api/reports-overview", "/api/forensics/modules", "/api/forensics/validation"):
        save(url)
    case_sub = ["", "/overview", "/takedown", "/genealogy", "/timeline", "/spread", "/source", "/propagation", "/next-action", "/monitor", "/locations",
                "/lead-engine", "/investigation", "/graph", "/generator", "/gaps", "/forensics", "/evidence", "/complaint", "/analysis-status"]
    search: list[dict] = []
    for d in demos:
        cid = d["id"]
        log(f"crawling {cid}")
        detail = None
        for sub in case_sub:
            got = save(f"/api/cases/{cid}{sub}")
            if sub == "":
                detail = got
        for m in (detail or {}).get("media", []):
            save(f"/api/cases/{cid}/media/{m['id']}")
            search.append({"type": "Media", "title": m.get("label") or m["filename"], "subtitle": f"{d['title']} · sha256 {m['sha256'][:12]}…", "case_id": cid,
                           "route": f"/case/{cid}/decomposition/{m['id']}", "hay": f"{m.get('label','')} {m['filename']} {m['sha256']} {m['md5']}".lower()})
        prop = b.c.get(f"/api/cases/{cid}/propagation").json()
        for l in prop.get("locations", []):
            search.append({"type": "Place", "title": l["url"], "subtitle": f"{d['title']} · {l.get('platform') or l.get('domain')}", "case_id": cid,
                           "route": f"/case/{cid}/spread", "hay": f"{l['url']} {l.get('domain','')} {l.get('account_handle','')} {l.get('caption','')}".lower()})
        search.append({"type": "Case", "title": d["title"], "subtitle": cid, "case_id": cid, "route": f"/case/{cid}/overview", "hay": f"{d['title']} {d['blurb']} {cid}".lower()})
    (out / "search.json").write_text(json.dumps(search, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return keys, {}


def harvest_vault(b: Backend, demos: list[dict], out: Path) -> dict:
    """Copy the evidence objects the saved responses point to (images, small JSON and video), shrinking big images."""
    vault_dir = out / "vault"
    shas: set[str] = set()
    for f in (out / "api").rglob("*.json"):
        shas |= set(SHA.findall(f.read_text(encoding="utf-8")))
    kinds: dict[tuple[str, str], str] = {}
    for d in demos:
        for r in b.c.get(f"/api/cases/{d['id']}/evidence").json()["records"]:
            kinds[(d["id"], r["sha256"])] = r["kind"]
    files: dict[str, str] = {}
    skipped = 0
    for sha in sorted(shas):
        for d in demos:
            if kinds.get((d["id"], sha)) not in KEEP_KINDS:
                continue
            r = b.c.get(f"/api/vault/{d['id']}/{sha}")
            if r.status_code != 200:
                continue
            ctype = r.headers.get("content-type", "").split(";")[0].strip()
            ext, body = EXT.get(ctype), r.content
            if ext is None:
                skipped += 1
                break
            if ctype.startswith("image/") and ctype != "image/gif":
                im = Image.open(io.BytesIO(body))
                if len(body) > (MAX_PNG_BYTES if ctype == "image/png" else MAX_IMAGE_BYTES) or max(im.size) > MAX_IMAGE_SIDE:
                    # previews only: the originals stay in your own vault, so the hash shown in the app is the original's
                    im.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
                    buf = io.BytesIO()
                    im.convert("RGB").save(buf, "JPEG", quality=80)
                    body, ext = buf.getvalue(), "jpg"
            elif ctype == "video/mp4" and len(body) > MAX_VIDEO_BYTES:
                skipped += 1
                break
            elif ctype in ("application/json", "text/plain") and len(body) > MAX_TEXT_BYTES:
                skipped += 1
                break
            f = vault_dir / d["id"] / f"{sha}.{ext}"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(body)
            files[f"{d['id']}/{sha}"] = ext
            break
    log(f"evidence files: {len(files)} copied, {skipped} skipped (too large or not previewable)")
    return files


# ------------------------------------------------------------------------------------------------ step 4: safety scan
BANNED = [r"[A-Za-z]:\\+(?:Users|deepfake|Windows|Program)", r"/Users/", r"/home/\w+", r"Pixel-2025", r"aryanpore", r"@gmail\.", r"AppData",
          r"HIVE_\w*KEY[\"']?\s*[=:]\s*[\"']?[A-Za-z0-9]{12,}", r"Bearer\s+[A-Za-z0-9._-]{12,}", r"\bsk[-_][A-Za-z0-9]{16,}"]


def scrub_and_scan(out: Path) -> None:
    mods = out / "api" / "forensics" / "modules.json"  # lists the plug-in folder of the machine that exported it
    if mods.exists():
        m = json.loads(mods.read_text(encoding="utf-8"))
        m["plugin_dir"] = "plugins"
        mods.write_text(json.dumps(m, separators=(",", ":")), encoding="utf-8")
    bad: list[str] = []
    for f in out.rglob("*"):
        if f.suffix not in (".json", ".txt"):
            continue
        text = f.read_text(encoding="utf-8")
        for pat in BANNED:
            for m in re.finditer(pat, text):
                bad.append(f"{f.relative_to(out)}: {text[max(0, m.start() - 30):m.end() + 40]!r}")
    if bad:
        print("\n".join(bad[:30]), file=sys.stderr)
        raise SystemExit(f"{len(bad)} possible local paths / personal data / secrets in the export; fix the source cases or extend scrub_and_scan()")
    log("safety scan clean (no local paths, e-mail addresses or key-like strings)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", type=Path, default=ROOT / "data", help="your data directory")
    ap.add_argument("--out", type=Path, default=ROOT / "frontend" / "public" / "demo")
    ap.add_argument("--cases", help="comma-separated case ids (default: the three built-in demos)")
    ap.add_argument("--port", type=int, default=8012)
    ap.add_argument("--work", type=Path, default=ROOT / "tmp" / "demo_build")
    a = ap.parse_args()
    demos = DEMOS if not a.cases else [d for d in DEMOS if d["id"] in a.cases.split(",")] or [
        {"id": c, "title": c, "blurb": "", "source_url": ""} for c in a.cases.split(",")]
    data = prepare(a.source, a.work, demos)
    a.out.mkdir(parents=True, exist_ok=True)
    for child in a.out.iterdir():  # empty the folder but keep it (a running dev server may hold a handle on the folder itself)
        rmtree(child) if child.is_dir() else child.unlink()
    b = Backend(data, a.port)
    try:
        ensure_forensics(b, demos)
        keys, _ = crawl(b, demos, a.out)
        vault = harvest_vault(b, demos, a.out)
    finally:
        b.stop()
    manifest = {"generated_at": datetime.now(timezone.utc).isoformat(), "cases": demos, "keys": sorted(set(keys)), "vault": vault,
                "note": "Saved read-only demo data. Regenerate with scripts/export_demo.py."}
    (a.out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    scrub_and_scan(a.out)
    total = sum(f.stat().st_size for f in a.out.rglob("*") if f.is_file())
    log(f"done: {len(manifest['keys'])} responses, {len(vault)} evidence files, {total / 1e6:.1f} MB in {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
