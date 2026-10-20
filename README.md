# DFIR — Deepfake Investigation & Response System

A local investigation workbench that treats a deepfake as an **incident, not a file**:

**Detect → Explain → Trace → Connect → Preserve → Investigate → Prove → Report → Act → Monitor**

Every finding is a *claim* with an explicit status — **Verified · Corroborated · Inferred · Lead · Unknown** — plus
its evidence (vault SHA-256s), the tool and version that produced it, its limitations, and what would confirm it.
There is no single "fake probability". Detector outputs are evidence, not verdicts. When metadata, watermarks or
detector hits are absent, the result is *Unknown*, never "authentic".

## Try it online (no install)

**Live demo: https://deepfake-mu-six.vercel.app/**

The hosted site shows **three completed, saved example cases** so you can see what the app does without running anything:

| Demo | What it shows |
|---|---|
| 1 · AI-generated school photo of a celebrity | A viral AI image ([source article](https://www.boredpanda.com/school-photos-of-celebrities-created-with-ai/)): the verdict with reasons, where it was found, the forensic modules, and what to do next |
| 2 · Face-swap meme found through an Instagram post | A meme traced from [an Instagram post](https://www.instagram.com/p/DMzd8nbIqAo/) to copies on other accounts: spread map, versions, leads |
| 3 · Morphed image of a private individual (fully synthetic) | A procedurally drawn stand-in (no real person) that walks through every stage: capture, genealogy, timeline, complaint, monitoring |

It is **read-only**. The analysis backend (PyTorch, about 4 GB of models, ffmpeg, a headless browser and a writable disk) cannot run on
Vercel, so anything that needs it (a new investigation, an upload, a re-run, a delete) shows a friendly "connect the backend" message instead.
A description of the project, with the demo walk-through, is in the
[project folder on Google Drive](https://drive.google.com/drive/u/0/folders/1cUssmVU0RQ5XPIHA5YZ9o2kEuX3VBqbw).

**To analyse your own media**, either look at the demo above, or download the project from that folder (or this repository) and run it
yourself with Docker: see the next section. When a backend is reachable the app switches to live mode on its own.

<details><summary>How the hosted demo works, and how to rebuild it</summary>

* The frontend decides at start-up: if `/api/health` answers, it is live; otherwise it reads the saved cases from `frontend/public/demo/`.
  `?demo=1` forces the saved demo (and remembers it); `?demo=0` goes back to the live backend.
* `frontend/public/demo/` is generated from your own data by `scripts/export_demo.py`. It copies only the chosen cases into a scratch
  directory, clears personal complaint details, runs the forensic modules where missing, saves every read-only API response the screens use,
  keeps preview-sized evidence images (originals stay in your vault), and **refuses to finish if it finds local paths, e-mail addresses or key-like strings**.
  ```bash
  cd backend && uv run --extra provenance --extra capture python ../scripts/export_demo.py
  ```
  Pass `--cases case_a,case_b` for other cases (edit `DEMOS` in the script for titles).
* Vercel project settings: **Root Directory `frontend`**, build `npm run build`, output `dist`. `frontend/vercel.json` rewrites every path to the
  single-page app so deep links and refreshes work.
* To run the hosted frontend against a real backend instead, add `{ "source": "/api/(.*)", "destination": "https://YOUR-BACKEND/api/$1" }`
  as the first rewrite in `frontend/vercel.json`. The app then starts in live mode. The backend has no login, so only expose one you are happy to share.
</details>

## Quick start with Docker (no installs)

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and start it.
2. **Windows:** double-click `start.bat`. **Mac/Linux:** run `./start.sh`. (Or: `docker compose up --build`.)
3. Wait. The first start builds the app and downloads about 3 GB of detection models, once (roughly 10-30 minutes).
   Later starts take seconds.
4. Open **http://localhost:8000**.

Your cases and evidence are kept in Docker volumes, so they survive restarts. Stop with `docker compose down`
(add `-v` to also delete all cases and models). The app is only reachable from your own computer.

- **Optional Hive key:** copy `.env.example` to `.env`, fill in `HIVE_SECRET_KEY`, then `docker compose up -d --force-recreate`.
  Without a key the local detectors are used.
- **NVIDIA GPU (optional, faster):** `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build`.
- **Not included in the image:** your `.env`, case data, models and the `demo/` images, which stay on your computer.

## Run it without Docker (developers)

```bash
cd backend && uv sync --extra capture --extra provenance --extra ocr --extra ml --extra asr --extra detectors && uv run playwright install chromium
```

```bash
cd backend && uv run --extra capture --extra provenance --extra ocr --extra ml --extra asr --extra detectors uvicorn dfir.main:app --port 8000
```

```bash
cd frontend && npm install && npm run dev
```

Open **http://localhost:8000**. The backend serves the built UI (`frontend/dist`) and the API (`/api/...`, docs at
`/docs`) on the same port, so run `npm run build` in `frontend/` after UI changes. For UI development with live
reload, use the Vite dev server at http://localhost:5180 instead. You can also start the `backend`, `frontend` and `demo-fixtures` configurations from
`.claude/launch.json`.

**Lab · backend** (http://localhost:5180/lab) is a plain view of the backend. It shows backend, GPU (VRAM,
utilisation, clocks) and model status, a drop-a-file bench listing every analyzer's outcome, timing, raw findings
and artifacts, the live backend log and recent jobs. *Load models onto GPU* preloads CLIP and Whisper.

### GPU

PyTorch comes from the CUDA 12.6 wheel index (`[tool.uv.sources]` in `backend/pyproject.toml`). On Turing TU117
GPUs such as the Quadro T2000, FP16 matmuls run about 4× slower than FP32. CLIP therefore runs in FP32
(about 1.7 GB of VRAM), and Whisper uses int8 weights with FP32 compute. Both together take about 2.8 GB of a
4 GB card. Set `DFIR_CLIP_FP16=1` on GPUs with fast FP16.

### Demo case (fully synthetic, no real person)

```bash
cd backend && uv run python ../demo/make_demo_case.py
```

This builds a procedurally drawn "portrait" that carries Stable-Diffusion-style parameters and the SDXL reference
watermark, plus five transformed copies (re-encode, crop + caption, mirror + downscale, phone screen recording,
partial edit). It publishes them on a local fixture "web" at http://127.0.0.1:8765 and runs the real pipeline:
capture, genealogy, graph, timeline, gaps, complaint and monitoring. Midway through, one page is "taken down" and
monitoring detects it. Keep the `demo-fixtures` server running if you want to re-capture or monitor the demo.

### Optional models (downloaded only when you run these)

```bash
cd backend && uv run python ../scripts/setup_models.py yunet
```

| key | what | size | licence |
|---|---|---|---|
| `spai` | **SPAI** (CVPR 2025): is the picture AI-generated? Any-resolution spectral detector | ~930 MB | Apache-2.0 |
| `trufor` | **TruFor** (CVPR 2023): was part of a photo edited? Integrity score plus "where" heatmap | ~280 MB | research / non-profit only |
| `sbi` | **SBI** (CVPR 2022): is a face swapped or blended in? Per-face score | ~140 MB | research only |
| `yunet` | OpenCV YuNet face detector (used by SBI and face statistics) | ~230 KB | MIT |
| `ufd` | UniversalFakeDetect + CLIP ViT-L/14. CLIP is used for genealogy embeddings; the UFD detector itself is off by default (`DFIR_ENABLE_UFD=1`) | ~1.7 GB | — |
| `whisper` | faster-whisper small → transcripts (Hindi and other Indian languages) | ~480 MB | MIT |

The detector code is vendored under `backend/third_party/` at pinned commits (see `third_party/PATCHES.md`). Weights
are pinned by SHA-256 in `models.lock.json`. A GPU model manager (`dfir/core/gpu.py`) keeps models resident within
a VRAM budget, evicts the least-recently-used model, and falls back to the CPU if a model doesn't fit.

### The verdict: one plain answer, measured honestly

Each image gets a **verdict**: *Fake / Likely fake / Uncertain / Likely real / Real*, with a "% likely fake"
figure, a confidence level, up to four plain-language reasons, and a per-detector breakdown (InVID/WeVerify style).
The full evidence chain sits underneath, collapsed, for investigators. The verdict combines two questions:

- **AI-generated?** SPAI, stacked with SBI by logistic regression and calibrated on public data. Verified provenance
  (C2PA "AI" claim, Stable Diffusion parameters, decoded watermark) overrides the detectors and counts as proof.
- **Face swapped / edited?** SBI's highest face score, and TruFor when it is far above what real photos produce.
- **With Hive configured, Hive's two scores decide instead** and the above becomes the second opinion (see below).

**Screenshots of posts** are detected automatically, and the detectors analyse the picture inside them. On the Modi
example, SPAI scored the whole screenshot 0.00 but the embedded picture 0.9997.

**Calibration** (`scripts/calibrate.py download | score | fit`) uses OpenFake (CC BY-NC 4.0). The *validation*
split fits the stack; the *test* split, with different generators and different real-photo sources, only measures
accuracy, and those numbers are shown in the UI as "How reliable is this?". A leak guard rejects any calibration
image whose SHA-256 or perceptual hash matches the images in `demo/`. The demo images are **never** used for
training or calibration.

**Benchmark** (Lab → Benchmark, or `scripts/batch_cases.py --folder ../demo`): drop labelled files
(`fake_*.jpg`, `real_*.jpg`) to get one case per file and a correct/incorrect table with accuracy and AUC.

### Hive: the primary detector

Put your keys in `.env` (template: `.env.example`; the file is git-ignored):

```
HIVE_SECRET_KEY=...        # V3 secret key, sent as a Bearer token
HIVE_SECRET_KEY_2=...      # optional fallback keys, tried in order (… _20)
HIVE_REGION=sf1            # or va1, matching your key's policy
```

When a key is configured, **Hive decides the verdict**: `p_ai` is Hive's `ai_generated` score and `p_edit` is its
`deepfake` score. The local detectors still run and appear as the *second opinion*. When they strongly disagree,
the card says so and confidence drops to low. Keys are tried in order. A key that is rejected (401/403), out of
credit (402), rate-limited (429) or hitting server errors is skipped for a while. Only when **every** key fails
does the verdict fall back to the local detectors, and the card then says "Hive unavailable". Media goes to Hive
automatically and each upload is logged in the custody chain; set `HIVE_AUTO_UPLOAD=0` to ask for consent per case.

The V3 request (`POST https://api.thehive.ai/api/v3/hive/ai-generated-and-deepfake-content-detection`, body
`{"input":[{"media_base64": ...}]}`) was confirmed with a live call. **Measured** (`models/hive_eval.json`,
`tmp/hive_eval.py`):

- **Held-out OpenFake test images (n = 87): Hive was right 99%.** It caught every Flux.2, Midjourney 7,
  GPT-Image and Z-Image fake in the sample and called every real photo real. The local engine scored 66% on the
  same images.
- **The OpenForensics face fakes in `demo/`** (GAN faces blended into real photos, 256 px): Hive scores all five
  as real (`ai_generated` and `deepfake` ≈ 0.00), even when upscaled 4×. The local face-swap detector (SBI) catches
  four of them, and the card shows that as the disagreeing second opinion.
- Free keys are rate-limited (HTTP 429). Add more keys for fallback.

Sightengine, Reality Defender and the search APIs (SerpAPI, TinEye, YouTube, Reddit) turn on when their keys are
added under Settings.

## Start from a link, not a screenshot

On the home page, paste the link to a **photo, video or post** and press *Investigate*. One background job then runs:

1. **Open the link and download its media.** A direct image/video URL is fetched as-is; a page is opened in headless
   Chromium (screenshot, HTML, MHTML, headers saved) and its main picture/video is downloaded (YouTube, TikTok, X and
   Instagram reels go through yt-dlp first). The first media found becomes the *suspect media*.
2. **Check it** with the detectors (Hive first, local second opinion).
3. **Archive and website records** (Wayback, RDAP/DNS).
4. **Look for copies** (see below).
5. **Compare** candidate media with the suspect media and build the spread map.

Progress is shown stage by stage, including what could *not* be searched. Public pages only, as a logged-out visitor:
a login-protected post can't be read, and the app says so.

**How copies are found** (`propagation/discover.py`): (a) links on the captured pages that point to other posts or other
pages of the same site, always, and nothing is sent anywhere; (b) with the user's permission, a web search by the caption /
title (DuckDuckGo, no key; only text is sent); (c) image-search APIs if configured (SerpAPI Lens, TinEye). Every candidate
is captured and its picture is **geometrically verified** against the suspect media. Only verified copies enter the case
record (complaint, timeline, graph); unmatched pages are listed as "checked, not included" with the reason, and the user
can include one by hand. Manual reverse-image links (Google Lens, Bing, Yandex, TinEye) are offered for what automation
can't reach. Possible-minor cases download and send nothing; intimate cases never auto-send anything to a search service.

## Where it appeared: the spread map

*Suspect media → versions → places.* Solid lines are **verified** (same content, geometric match); dashed amber lines are
**unverified / speculative**; arrows appear only when one version is clearly a crop or resize of the other. Each version
lists how it differs in plain words (cropped, mirrored, re-compressed, text added, part of the picture differs...). "Earliest
copy found" never means "original".

## Takedown & complaint tracker

*Take action* is a guided list for the case type: money first (1930 / bank) when relevant, the NCRP route (anonymous
Women/Child route or Other Cyber Crime with OTP), one report per platform with ready text and the platform's own steps,
StopNCII for intimate images (never for possible minors), and escalation. The user does each step on the official site, then
records the **reference / acknowledgement number** and when they did it; this starts the deadline clock
(`reporting/refs/takedown_clocks.yaml`, with sources and an "as of" date: 2 hours for intimate/morphed/impersonating
content, 7 days for grievances, 30 days for an appeal to the Grievance Appellate Committee). When a clock passes with no
action the tracker shows **overdue** and produces the Grievance Officer escalation text. Every recorded step is a
user-attested timeline entry and a custody-log entry. Nothing is ever filed automatically.

## Deleting a case

*Case options → Archive or delete*, or the archive/trash button on the home page. **Archive** hides a case and keeps all
evidence. **Delete** removes the database rows, the evidence vault and the custody log from disk; it needs the exact word
`DELETE`, refuses while something is running, and leaves a one-line deletion record (`data/deleted_cases.jsonl`: title,
time, counts, final custody hash).

## Device & source (what the file says about where it came from)

*Investigate deeper → Device & source* lists the camera/phone the file **claims**, every timestamp it carries, the editing
software and history, owner/author fields, and what metadata survived or was stripped, with consistency checks (re-saved
after capture, capture time later than publication, pixel size that contradicts the metadata, an embedded camera preview
that shows a different picture or shape). Everything is labelled *declared in the file (editable)*: a phone **model** is not a
device and missing metadata is normal after a social-media upload.

**Sensor fingerprint (PRNU), advanced research feature** (`forensics/prnu.py`): tests a questioned image against ≥5 reference
photos from a *specific suspected camera* (upload them with "Add authentic reference"; they must have exactly the same pixel size).
Wavelet noise residual → maximum-likelihood fingerprint → peak-to-correlation energy (PCE ≥ 60 = consistent). It refuses images that
show the same picture (their noise would match because of the picture), resized images and too few references. It was validated on
synthetic sensors only, not on a camera test set; resizing, cropping, heavy compression and social-media processing destroy the signal,
and AI-generated images have none.

## AI generator attribution

*Investigate deeper → AI generator* names a **possible generator family** only as far as the evidence goes, as an evidence ladder:
**signed** (valid C2PA) > **declared** (generation parameters, software fields, IPTC type, unvalidated C2PA; forgeable) >
**watermark** (decoded open watermark; fragile) > **classifier** (Hive's source probabilities; statistical) > **inherited** (found on
another verified copy). A family is *established* (signed, or three independent kinds agree), *supported* (written in the file or
corroborated, but forgeable) or *suggested only*. Conflicting evidence yields "no single family". A generator label on an image the
detectors call real is flagged as a contradiction. No watermark or metadata never means "real"; SynthID and other proprietary
watermarks are listed as not checked.

## Connected accounts & leads (the lead engine)

*Investigate deeper → Accounts & leads* (`reasoning/lead_engine.py`) turns posts by several accounts into ranked leads:

1. **Versions.** Identical files, crops, mirrors, re-encodes and overlays, each with how it differs from its neighbour.
2. **Earliest observed appearance** of every version and family of copies, with the time source stated (declared by the page vs only
   the moment this app captured it) and archive snapshots as support.
3. **Comparison** of timestamps, captions, shared links, hashtags, handles visible inside the media, look-alike handles and shared
   technical artefacts (camera serial, sensor-noise match).
4. **Graph** that draws what was *observed* solid and what is a *hypothesis* dashed.
5. **Leads**, each with evidence (observed vs hypothesis), honest confidence, alternative explanations *with a way to test each*, and
   the authorised evidence (platform records, original uploads, device examination, lawful request) that would confirm or disprove it.

Rules built in: the roles **original creator** (never established from public copies), **editor** (never named; only "first known
uploader of an altered version"), **first known uploader** and **distributor** are kept apart. Sharing the same file is expected of
any repost and counts for almost nothing; a relationship between accounts needs several *independent kinds* of distinctive evidence
(text, identity, time, technical) before it rises above *low*, and never beyond "a hypothesis worth investigating".

## Home dashboard

`/` is a dashboard: start an investigation (paste a URL, upload a file, or paste text containing links), jump to an analysis module,
resume a recent case, and see backend, GPU and model status. The sidebar, search (Ctrl/Cmd+K over cases, file names and hashes, URLs,
domains and handles), alerts bell, light/dark switch and profile are all live. Cases, Investigation, URL Search, Monitoring, Evidence
Vault and Reports are global views over the same per-case data; the analysis tools open the matching part of the case you choose.
The default look is the soft Neomorphism theme in light mode; change it in Settings -> Appearance.

## Forensic modules (sensor noise, camera processing, editing, video, audio, provenance)

Six pluggable modules, shown per file on **Investigate deeper -> Forensic modules**. Each returns findings (Measured / Supported /
Hypothesis / Unknown), a confidence that is *capped by how well the method was validated*, limitations, tool versions and evidence, and
is stored in the evidence vault and chain of custody. Candidate-device fingerprints (PRNU) are built from reference photos you attest came
from one camera. Nothing names an exact device or AI model. Full detail and the measured accuracy and false-positive rates:
[docs/FORENSICS.md](docs/FORENSICS.md). Add your own with a drop-in file or an installable package: [plugins/README.md](plugins/README.md).

```bash
cd backend && uv run python ../scripts/forensics_bench.py --quick --out ../tmp/q.json      # smoke test, minutes
cd backend && uv run python ../scripts/forensics_bench.py --real-dir ../models/calibration_data/test --real-n 160   # ~3 h, writes the shipped validation results
python scripts/theme_contrast_audit.py      # WCAG contrast of every theme token pair
```

## Appearance

*Settings -> Appearance*: three themes (Workbench, Neomorphism, Claymorphism), each in light and dark (or match the system), a changeable
accent colour (kept readable: text on the accent and accent text on the page both meet 4.5:1), and a motion setting (match system / reduce
/ full). Choices apply to charts, controls, cards, navigation and status indicators, are saved in `data/preferences.json` and in the
browser, and survive restarts. Status colours were validated per mode and surface; status is never shown by colour alone.

## What each stage does

| Stage | Implementation |
|---|---|
| Forensic decomposition | EXIF/XMP/ffprobe, JPEG quantisation tables, C2PA (`c2pa-python`), IPTC DigitalSourceType, SD/ComfyUI/InvokeAI parameter chunks, SD DWT-DCT watermark decoding, ELA, noise residual, spectral peaks, JPEG ghost, faces, video frame/temporal signals, audio bandwidth/pause analysis, OCR (handles, platform marks), optional Whisper and learned detectors |
| Media genealogy | PDQ + pHash (incl. mirrored), SIFT + RANSAC geometric verification (crops, captions, screen recordings), frame-sequence and audio-landmark alignment, transform inference (crop, mirror, scale, caption/border, screen capture, recompress, format, partial edit, trim, audio swap), Edmonds arborescence → family tree |
| Propagation | Link-first investigation pipeline (`investigate.py`); copy discovery with geometric verification; headless-Chromium capture (screenshot, HTML, MHTML, headers, og/JSON-LD metadata, visible handles, displayed counts), media download (HTTP / yt-dlp), Wayback CDX, RDAP, DNS + ASN |
| Relationships | Typed graph (media, URL, account, domain, hashtag, device/encoder signature, generator signal) with status on every edge; centrality and look-alike-handle analytics used to **prioritise leads, never to accuse** |
| Reconstruction | Swimlane timeline with time source and precision for each event, conflict detection, and the unknown creation time drawn as a hatched band |
| Preservation | Write-once, content-addressed vault (SHA-256 + MD5); hash-chained custody ledger; ZIP package with manifest, hashes.csv, PDF case file, complaint DOCX, draft BSA s.63 certificate and `verify_package.py` |
| Gaps / next action | Ranked open questions (who can obtain the evidence, effort, value) and an incident-type-specific action plan |
| Reporting | NCRP-mapped complaint blocks (Complaint Ready → Review/Edit → **you** submit), platform report texts, possibly relevant provisions marked *confirm with a lawyer/police* |
| Tracking | Complaint & acknowledgement tracker with takedown clocks, overdue detection and escalation text (`reporting/tracker.py`) |
| Monitoring | Scheduled re-capture: removed / restored / changed / new-version alerts, plus new archive snapshots |

## Not done, and never faked

- No dark-web or Tor crawling, and no private or logged-in platform data.
- No uploader IP addresses or identities. These come only from platforms, through lawful police requests.
- Nothing is submitted to NCRP or any platform automatically. You always submit yourself.
- SynthID and other proprietary watermarks have no public detector, so they show as *not checked*.
- Legal references are aids for a lawyer or the police, not legal advice.

## Tests

```bash
cd backend && uv run --group dev pytest -q
```
