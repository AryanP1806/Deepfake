# DFIR — Deepfake Investigation & Response System

A local investigation workbench that treats a deepfake as an **incident, not a file**:

**Detect → Explain → Trace → Connect → Preserve → Investigate → Prove → Report → Act → Monitor**

Every finding is a *claim* with an explicit status — **Verified · Corroborated · Inferred · Lead · Unknown** — plus
its evidence (vault SHA-256s), the tool and version that produced it, its limitations, and what would confirm it.
There is no single "fake probability". Detector outputs are evidence, not verdicts. When metadata, watermarks or
detector hits are absent, the result is *Unknown*, never "authentic".

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
cd backend && uv sync --extra capture --extra provenance --extra ocr --extra ml --extra asr && uv run playwright install chromium
```

```bash
cd backend && uv run --extra capture --extra provenance --extra ocr --extra ml --extra asr uvicorn dfir.main:app --port 8000
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

## What each stage does

| Stage | Implementation |
|---|---|
| Forensic decomposition | EXIF/XMP/ffprobe, JPEG quantisation tables, C2PA (`c2pa-python`), IPTC DigitalSourceType, SD/ComfyUI/InvokeAI parameter chunks, SD DWT-DCT watermark decoding, ELA, noise residual, spectral peaks, JPEG ghost, faces, video frame/temporal signals, audio bandwidth/pause analysis, OCR (handles, platform marks), optional Whisper and learned detectors |
| Media genealogy | PDQ + pHash (incl. mirrored), SIFT + RANSAC geometric verification (crops, captions, screen recordings), frame-sequence and audio-landmark alignment, transform inference (crop, mirror, scale, caption/border, screen capture, recompress, format, partial edit, trim, audio swap), Edmonds arborescence → family tree |
| Propagation | Headless-Chromium capture (screenshot, HTML, MHTML, headers, og/JSON-LD metadata, visible handles, displayed counts), media download (HTTP / yt-dlp), Wayback CDX, RDAP, DNS + ASN |
| Relationships | Typed graph (media, URL, account, domain, hashtag, device/encoder signature, generator signal) with status on every edge; centrality and look-alike-handle analytics used to **prioritise leads, never to accuse** |
| Reconstruction | Swimlane timeline with time source and precision for each event, conflict detection, and the unknown creation time drawn as a hatched band |
| Preservation | Write-once, content-addressed vault (SHA-256 + MD5); hash-chained custody ledger; ZIP package with manifest, hashes.csv, PDF case file, complaint DOCX, draft BSA s.63 certificate and `verify_package.py` |
| Gaps / next action | Ranked open questions (who can obtain the evidence, effort, value) and an incident-type-specific action plan |
| Reporting | NCRP-mapped complaint blocks (Complaint Ready → Review/Edit → **you** submit), platform report texts, possibly relevant provisions marked *confirm with a lawyer/police* |
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
