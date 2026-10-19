# Forensic modules: what they do, how far they can be trusted

Six independent modules, each answering one question about one file. Every module returns the same shape:

| Field | Meaning |
|---|---|
| `findings` | Each tagged **Measured** (observation), **Supported** (error rates measured against ground truth), **Hypothesis** (not validated) or **Unknown** (could not be assessed) |
| `confidence` | How strongly the module's own evidence supports its headline. **Capped by validation tier**: no validation 0.35, simulated data only 0.70, real labelled data 0.95. A hypothesis is never above 0.35 |
| `limitations` | What the method cannot tell you |
| `versions` | Library / tool versions that produced the numbers |
| `evidence` | Heat maps and plots (stored in the evidence vault) and the claims the finding rests on |
| `validation` | The tier and the measured metrics, taken from `validation_results.json` |

Missing evidence is **Unknown**, never "clean". No module names an exact camera or an exact AI model.

Results are stored as hashed, read-only vault artifacts, every run adds a chain-of-custody entry (tool, version, status, result hash),
and interpretive findings become claims with **neutral polarity**, so they sit beside the verdict and cannot change it until a method
has been validated on real data.

## The modules

| # | Module (`id`) | Question | Reuses |
|---|---|---|---|
| 1 | PRNU sensor-noise fingerprinting (`prnu`) | Could this image come from one of the candidate cameras on file? | `forensics/prnu.py`, new candidate-device store |
| 2 | Camera-processing fingerprints (`camera_pipeline`) | Does the pixel data carry demosaicing, sharpening, noise, lens and JPEG traces? | `metadata` JPEG tables |
| 3 | Editing and recompression (`editing`) | Were parts processed differently, resized, recompressed or copied? | `image_forensics.ela`, `noise_residual`, camera-module CFA tiles |
| 4 | Video acquisition (`video_acquisition`) | Frame timing, codec traces, camera motion, rolling shutter | `media_io` (ffprobe/ffmpeg) |
| 5 | Audio forensics (`audio_forensics`) | Hum, reverberation, noise floor, clipping, audio/video sync | `media_io.decode_audio`, YuNet face detector |
| 6 | Provenance (`provenance`) | EXIF, C2PA, AI watermarks, generator metadata | `metadata`, `c2pa`, `provenance_signals` claims; `reasoning/generator.py`; `reasoning/source.py` |

## Plugins

Optional models install independently. See `plugins/README.md`. A module lists `requirements` and `model_specs`; a missing optional
requirement never blocks the module, a missing required one marks only that module *unavailable* with the install command.
Add a module by dropping a `.py` file into `plugins/` or by installing a package with a `dfir.forensics` entry point. Weights are
fetched and SHA-256 pinned with `scripts/setup_models.py <key>`. A plugin that fails to load, or was written for another contract
version, is reported and isolated.

## Validation: what was measured

Run `cd backend && uv run python ../scripts/forensics_bench.py --real-dir ../models/calibration_data/test --real-n 160`
(about 3 hours; `--quick` takes minutes). Results are written to `backend/dfir/forensics/validation_results.json` and shown in the app.

**There is no real labelled camera dataset on this machine. Every device-source and acquisition result below comes from simulated data
with known truth, so every module's tier is `synthetic` (or `none`), and none may claim `real`.** (A test enforces this.)
The only real-image measurement is the false-positive rate of the editing and camera modules on 160 real web photographs, which are
*presumed* unedited but not guaranteed, so those rates are upper bounds.

### 1. PRNU (8 simulated cameras sharing one Bayer pattern, 6 test images each, 512 px, PCE ≥ 60)

| Condition | True-camera match | False matches (336 impostor trials per row) |
|---|---|---|
| none / JPEG q95 / q85 / q70 | 100 % (48/48) | 0 |
| JPEG q50 | 54 % (26/48) | 0 |
| resized 0.9 (resized back) | 100 % | 0 |
| resized 0.5 (resized back) | 92 % | 0 |
| blur σ=1 | 98 % | 0 |
| brightness / contrast | 100 % | 0 |
| rotated 90° | 0 % (correct: not comparable) | 0 |
| cropped, messaging-app, screenshot | not comparable (different size, the module refuses) | n/a |

Pooled: **0 false matches in 3,360 impostor trials** (95 % upper bound 0.11 %).

> **Defect found and fixed while validating.** The first benchmark showed impostor PCE ≈ 250 between *different* simulated cameras
> (threshold 60): the residual kept row/column and colour-filter-phase structure common to cameras of one design, so unrelated cameras
> correlated. The existing zero-meaning step is now done separately in each of the four 2×2 phase subsets (Chen et al. 2008). Impostor PCE
> fell to ≈ 20 (noise floor) and a regression test pins it. Real cameras of one model share even more such structure; this is why a
> match only ever *supports* a shared sensor.

References are also checked for coherence (leave-one-out): a fingerprint built from photos that do not match each other is flagged and its
matches are down-weighted. With only 5 small (256 px) photos the check is under-powered and can reject genuine references; use 10+ photos.

### 2. Camera-processing traces (simulated Bayer sensors, bilinear demosaicing)

* Demosaicing trace: found in 100 % of images saved lossless or at JPEG q ≥ 90; **0 % at q ≤ 85**; 0 % after any resize, and 0 % on images with no colour filter array.
* Sharpening: **not validated**. Average edge overshoot was 1.5 % unsharpened and 2.0 % at unsharp-mask amount 1.5 (7 of 24 had enough clean edges), too close to separate. No sharpening conclusion is drawn.
* Noise suppression hypothesis: flagged 24/24 denoised, 0/24 normal sensors.
* Lateral chromatic aberration: radial shifts of 0.54 px (corner) found 50 % of the time, 1.09 px 100 %, 0.0 px never (0/12); magnitude over-read by 20 % at 0.5 px and under-read by 17 % at 1.1 px.
* Real photos: demosaicing trace found in 0/160 real web photos and 0/160 AI images (web photos have been recompressed or resized).

### 3. Editing (simulated edits with known masks, 24 each; untouched n = 48)

| Test | Result |
|---|---|
| Untouched images flagged as having an edited region | 2 % (1/48) |
| Splice with a **different compression history** localised (IoU 0.41) | 79 % (19/24) |
| Splice from another camera, pasted before a single JPEG save | **4 % (1/24), effectively missed** |
| Rescaled paste (1.3×) | **0 % (0/24), missed** |
| Copy-move (112 px patch) | 67 % (16/24) |
| Whole-image enlargement 1.2× / 1.5× | 88 % / 100 % |
| Reduction 0.8× and integer 2× enlargement | 0 % (not detectable by this method) |
| Double JPEG q85→q92 | 100 %; q75→q92 4 %; q95→q75 0 %; same quality 0 % |

False positives on **real web photographs (n = 160)** and on **AI-generated images (n = 160)**:

| Flag | Real web photos | AI-generated |
|---|---|---|
| Region where two independent cues agree | 6 % (10/160) | 9 % (15/160) |
| Whole-image resampling | **30 %** (48/160) | **83 %** (133/160) |
| Double JPEG | 9 % (14/160) | 1 % |
| Block-grid shift | 1 % | 1 % |
| Copy-move | 0 % | 12 % (19/160) |

So "resampled" says almost nothing on its own: a third of ordinary web photos and most generated images trigger it. The app says so next
to every such finding.

### 4. Video (simulated panning clips, libx264)

* Rolling-shutter read-out: true 0 → estimated ≈ 0 (never flagged); true 0.3 / 0.6 / 0.9 of a frame period → flagged 100 %, but **the estimate reads about half the truth** (ratio 0.48 / 0.51 / 0.54). Use it to detect and compare, not as an absolute figure. Needs real horizontal camera motion; otherwise Unknown.
* Frame timing: 2 of 2 dropped-frame groups found, gap multiples reported; 2 of 2 duplicated frames found; 1 false duplicate in a clean control.
* x264 encoder trace detected in the libx264 file and not in an MPEG-4 file; static camera classified static; panning camera not.

### 5. Audio (simulated speech-like signals)

* Mains hum 50/60 Hz: detected and correctly identified 100 % from −60 to −42 dBFS (12/12 each), false alarm 0/24.
* Reverberation (blind RT60): true 0.3 / 0.5 / 0.9 s → median 0.36 / 0.57 / 0.95 s; fails (Unknown) above about 1.2 s.
* Clipping: not flagged at gain 1–2, flagged 100 % at gain 3 and 5. Noise-floor change between joined recordings: 12/12, none when joined from the same conditions.
* Audio/video offset estimator: recovered −200, 0, +150, +300 ms exactly on simulated series (50 ms resolution); 1 of 36 unrelated signal pairs reached p < 0.01.
  The full pipeline (face detection → mouth motion) was not validated on real or rendered talking-head video.

## What remains unvalidated, and hardware or model limits

* No real labelled camera set (Dresden, VISION, a phone with known serial) has been run. PRNU, demosaicing, rolling shutter and device-level claims are simulation-validated only. Validating on real data needs a labelled set (several hundred photos from known devices at native resolution); a loader for such a set is **not written yet**.
* The chat copies of the Samsung S25 FE photos had no EXIF and were downscaled, so device identification on them could not be tested.
* Video and audio modules run on CPU. A 6 s clip takes ~10 s (video) and ~5 s (audio); the audio/video sync step also needs the YuNet face model.
* No voice-clone detector, SyncNet, learned noiseprint or other deep model is bundled; the plugin slot exists for each.
* Thresholds were tuned on simulated data, and the splice, resampling and copy-move detectors have noticeable false-positive rates on web photos; treat localisation boxes as leads for human review.
* Proprietary watermarks (SynthID and others) cannot be checked: there is no public detector.
