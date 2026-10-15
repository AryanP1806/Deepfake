"""Commercial detection API adapters. Off unless a key is configured AND the user has granted
per-upload consent — victim media must never leave the machine silently."""
from __future__ import annotations

import httpx

from ..core import consent
from ..core.claims import NotRun, Polarity, Status, Strength
from ..core.config import get_key
from ..core.db import session
from .base import Analyzer, Ctx, register


def _ai_claim(ctx: Ctx, vendor: str, score: float | None, label: str, raw: dict):
    if score is None:
        ctx.claim("detector", f"{vendor}: response did not include an AI-generation score", Status.UNKNOWN,
                  data={"raw": raw})
        return
    if score >= 0.9:
        strength, pol, desc = Strength.MODERATE, Polarity.SUPPORTS_MANIPULATION, "classifies the media as likely AI-generated/manipulated"
    elif score >= 0.6:
        strength, pol, desc = Strength.WEAK, Polarity.SUPPORTS_MANIPULATION, "leans toward AI-generated/manipulated"
    elif score <= 0.1:
        strength, pol, desc = Strength.WEAK, Polarity.SUPPORTS_AUTHENTIC, "does not flag the media"
    else:
        strength, pol, desc = Strength.WEAK, Polarity.NEUTRAL, "is inconclusive"
    ctx.claim("detector", f"{vendor} {desc} (vendor score {score:.2f} for '{label}')", Status.INFERRED,
              strength=strength, polarity=pol,
              why=f"{vendor} runs proprietary detectors trained on large corpora of generated media.",
              limitations="Closed model: training data, calibration and error rates on this media type are not "
                          "independently known. The media was uploaded to the vendor with the user's consent.",
              data={"vendor": vendor, "score": score, "label": label, "raw": raw}, sources=[f"vendor:{vendor}"])


class _ApiDetector(Analyzer):
    vendor = ""
    consent_action = ""
    key_names: tuple[str, ...] = ()

    def available(self):
        missing = [k for k in self.key_names if not get_key(k)]
        if missing:
            return NotRun(component=self.name, reason=f"{self.vendor} API key not configured ({', '.join(missing)})",
                          how_to_enable="Add the key under Settings → External services")
        return None

    def _consented(self, ctx: Ctx) -> NotRun | None:
        with session() as s:
            try:
                consent.require(s, self.consent_action, ctx.case_id)
            except consent.ConsentRequired:
                return NotRun(component=self.name,
                              reason=f"Not uploaded: no consent recorded to send this media to {self.vendor}",
                              how_to_enable="Grant consent in the Decomposition view, then re-run this analyzer")
        ctx.vault.custody.append("external.upload", tool=f"{self.name}@{self.version}", item_sha256=ctx.media.sha256,
                                 details={"vendor": self.vendor, "consent_action": self.consent_action})
        return None


HIVE_MODEL = "hive/ai-generated-and-deepfake-content-detection"
HIVE_MAX_KEYS = 20
# key slot -> (retry-after epoch, reason). Keys that are out of credit / rate-limited / rejected are skipped for a
# while so every analysis doesn't pay for a known-bad key first.
_HIVE_COOLDOWN: dict[int, tuple[float, str]] = {}


class HiveUnavailable(RuntimeError):
    """Every configured Hive key failed (invalid, out of credit, rate-limited, network). Not a media problem."""

    def __init__(self, attempts: list[dict]):
        self.attempts = attempts
        super().__init__("All Hive keys failed: " + "; ".join(f"key {a['slot']}: {a['error']}" for a in attempts))


def hive_keys() -> list[dict]:
    """Configured V3 keys in fallback order: HIVE_SECRET_KEY, HIVE_SECRET_KEY_2 … _20 (or _1), plus a
    comma-separated HIVE_SECRET_KEYS. Each slot may have its own HIVE_REGION_<n>; default HIVE_REGION."""
    default_region = (get_key("HIVE_REGION") or "sf1").lower()
    out: list[dict] = []
    seen: set[str] = set()

    def add(secret: str | None, region: str | None):
        secret = (secret or "").strip()
        if secret and secret not in seen:
            seen.add(secret)
            out.append({"slot": len(out) + 1, "secret": secret, "region": (region or default_region).lower()})

    add(get_key("HIVE_SECRET_KEY"), get_key("HIVE_REGION"))
    for n in range(1, HIVE_MAX_KEYS + 1):
        add(get_key(f"HIVE_SECRET_KEY_{n}"), get_key(f"HIVE_REGION_{n}"))
    for k in (get_key("HIVE_SECRET_KEYS") or "").split(","):
        add(k, None)
    return out


def hive_auto_upload() -> bool:
    """Hive is the primary detector: with keys configured, media is sent automatically (logged in the custody
    chain). Set HIVE_AUTO_UPLOAD=0 to ask for consent per case instead."""
    return (get_key("HIVE_AUTO_UPLOAD") or "1").strip().lower() not in ("0", "false", "no", "off")


def _hive_v3_url(region: str = "sf1") -> str:
    override = get_key("HIVE_V3_URL")
    if override:
        return override
    host = "api-va1.thehive.ai" if region == "va1" else "api.thehive.ai"
    return f"https://{host}/api/v3/{HIVE_MODEL}"


def _walk_classes(obj, out: dict) -> None:
    """Collect every {class, score|value} pair anywhere in a Hive response (V2 or V3 shapes).
    Video responses have one entry per frame; the maximum over frames is kept."""
    if isinstance(obj, dict):
        cls = obj.get("class") or obj.get("class_name") or obj.get("label")
        val = obj.get("score", obj.get("value", obj.get("confidence")))
        if isinstance(cls, str) and isinstance(val, (int, float)):
            out[cls] = max(out.get(cls, 0.0), float(val))
        for v in obj.values():
            _walk_classes(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk_classes(v, out)


def _key_error(r: httpx.Response) -> str | None:
    """Errors that another key may fix (auth, credit, quota, server). None = request/media problem."""
    if r.status_code in (401, 403):
        return f"rejected (HTTP {r.status_code})"
    if r.status_code == 402:
        return "out of credit (HTTP 402)"
    if r.status_code == 429:
        return "rate-limited / quota reached (HTTP 429)"
    if r.status_code >= 500:
        return f"Hive server error (HTTP {r.status_code})"
    return None


def hive_call(data: bytes, filename: str, mime: str) -> tuple[dict, dict]:
    """Call Hive V3 with each configured key in turn until one works. Returns (classes, raw).

    Request body confirmed by a live call (2026-10-07): POST /api/v3/hive/ai-generated-and-deepfake-content-detection
    with {"input": [{"media_base64": ...}]} and "Authorization: Bearer <secret key>".
    Falls back to the legacy V2 Token API only when no V3 key exists."""
    import base64
    import time
    keys = hive_keys()
    if not keys and get_key("HIVE_API_KEY"):
        r = httpx.post("https://api.thehive.ai/api/v2/task/sync", headers={"Authorization": f"Token {get_key('HIVE_API_KEY')}"},
                       files={"media": (filename, data, mime or "application/octet-stream")}, timeout=120)
        r.raise_for_status()
        raw = r.json()
        classes: dict = {}
        _walk_classes(raw, classes)
        return classes, {"api": "v2", "response": raw}
    body = {"input": [{"media_base64": base64.b64encode(data).decode()}]}
    attempts: list[dict] = []
    now = time.time()
    ready = [k for k in keys if _HIVE_COOLDOWN.get(k["slot"], (0, ""))[0] <= now]
    # if every key is cooling down, try them all anyway (a quota may have reset). A per-minute rate limit
    # (HTTP 429) on the last key gets two short waits before giving up.
    order = list(ready or keys)
    waits = [3, 8]
    while order:
        k = order.pop(0)
        url = _hive_v3_url(k["region"])
        try:
            r = httpx.post(url, headers={"Authorization": f"Bearer {k['secret']}"}, json=body, timeout=120)
        except httpx.HTTPError as e:
            attempts.append({"slot": k["slot"], "region": k["region"], "error": f"network: {type(e).__name__}"})
            continue
        if r.status_code < 400:
            _HIVE_COOLDOWN.pop(k["slot"], None)
            raw = r.json()
            classes = {}
            _walk_classes(raw, classes)
            return classes, {"api": "v3", "url": url, "key_slot": k["slot"], "keys_configured": len(keys),
                             "failed_keys": attempts, "response": raw}
        err = _key_error(r)
        if err is None:  # bad request / unsupported media: another key will not help
            raise RuntimeError(f"Hive rejected the media (HTTP {r.status_code}): {r.text[:300]}")
        if r.status_code == 429 and not order and waits:
            time.sleep(waits.pop(0))
            order.append(k)
            continue
        attempts.append({"slot": k["slot"], "region": k["region"], "error": err})
        _HIVE_COOLDOWN[k["slot"]] = (now + (3600 if r.status_code in (401, 402, 403) else 60), err)
    raise HiveUnavailable(attempts)


@register
class HiveDetector(_ApiDetector):
    """PRIMARY detector when Hive keys are configured: its scores decide the verdict; the local detectors
    (SPAI / SBI / TruFor) still run and are shown as the second opinion. If every key fails, the verdict
    falls back to the local detectors."""
    order = 9  # right after the screenshot crop (content_region, 8), so the verdict is available early
    name, version, vendor = "api_hive", "3.0", "Hive"
    consent_action = "api_detect:hive"
    title = "Hive AI-generated & deepfake detection (primary, commercial API)"
    modalities = ("image", "video")

    def available(self):
        if hive_keys() or get_key("HIVE_API_KEY"):
            return None
        return NotRun(component=self.name, reason="Hive key not configured (HIVE_SECRET_KEY in .env)",
                      how_to_enable="Put HIVE_SECRET_KEY=... (more keys: HIVE_SECRET_KEY_2, _3, ...; optional "
                                    "HIVE_REGION=sf1 or va1) in .env")

    def _consented(self, ctx: Ctx) -> NotRun | None:
        if not hive_auto_upload():
            return super()._consented(ctx)
        ctx.vault.custody.append("external.upload", tool=f"{self.name}@{self.version}", item_sha256=ctx.media.sha256,
                                 details={"vendor": self.vendor, "consent_action": self.consent_action,
                                          "consent": "standing (Hive configured as primary detector; "
                                                     "HIVE_AUTO_UPLOAD=0 to ask per case)"})
        return None

    def run(self, ctx: Ctx):
        if (nr := self._consented(ctx)):
            return nr
        import io

        from PIL import Image
        rgb = ctx.shared.get("content_rgb") if ctx.media.modality == "image" else None
        if rgb is not None and "content_box" in ctx.shared:
            buf = io.BytesIO()
            Image.fromarray(rgb).save(buf, "PNG")
            data, fname, mime = buf.getvalue(), "content.png", "image/png"
        else:
            data, fname, mime = ctx.path.read_bytes(), ctx.media.filename or "media", ctx.media.mime
        try:
            classes, raw = hive_call(data, fname, mime)
        except HiveUnavailable as e:
            return NotRun(component=self.name,
                          reason=f"Hive unavailable — {e}. The verdict uses the local detectors instead.",
                          how_to_enable="Check the keys in .env or add another (HIVE_SECRET_KEY_2, ...), "
                                        "then re-run this analyzer")
        ctx.shared["hive_classes"] = classes
        ai = classes.get("ai_generated")
        if ai is None and "not_ai_generated" in classes:
            ai = 1 - classes["not_ai_generated"]
        deepfake = classes.get("deepfake")
        sources = {k: v for k, v in classes.items() if k not in (
            "ai_generated", "not_ai_generated", "deepfake", "not_deepfake", "none", "inconclusive",
            "inconclusive_video", "ai_generated_audio", "not_ai_generated_audio")}
        top_src = max(sources.items(), key=lambda kv: kv[1]) if sources else None
        meta = {"api": raw.get("api"), "key_slot": raw.get("key_slot"), "keys_configured": raw.get("keys_configured"),
                "failed_keys": raw.get("failed_keys") or [], "sent": "picture inside screenshot" if fname == "content.png" else "file"}
        _ai_claim(ctx, "Hive", ai, "ai_generated", {"classes": classes, **meta})
        if ctx.claims and ctx.claims[-1].data.get("vendor") == "Hive":
            ctx.claims[-1].data.update(head="ai_generated", score=ai, model="Hive (commercial)", primary=True, **meta)
            if top_src and top_src[1] >= 0.5 and (ai or 0) >= 0.5:
                ctx.claims[-1].data["generator_family"] = f"{top_src[0]} (Hive source attribution)"
        if deepfake is not None:
            ctx.claim("faceswap", f"Hive deepfake check: highest face score {deepfake:.2f}", Status.INFERRED,
                      strength=Strength.MODERATE if deepfake >= 0.9 or deepfake <= 0.05 else Strength.WEAK,
                      polarity=Polarity.SUPPORTS_MANIPULATION if deepfake >= 0.5 else Polarity.NEUTRAL,
                      why="Hive's deepfake model scores each detected face for face-swap / reenactment artefacts.",
                      limitations="Closed commercial model; the media was uploaded to Hive.",
                      data={"vendor": "Hive", "score": deepfake, "head": "edited", "model": "Hive deepfake (commercial)",
                            "primary": True}, sources=["vendor:Hive"])


@register
class SightengineDetector(_ApiDetector):
    order = 71
    name, version, vendor = "api_sightengine", "1.0", "Sightengine"
    consent_action, key_names = "api_detect:sightengine", ("SIGHTENGINE_API_USER", "SIGHTENGINE_API_SECRET")
    modalities = ("image",)
    title = "Sightengine AI-generated image detection (API)"

    def run(self, ctx: Ctx):
        if (nr := self._consented(ctx)):
            return nr
        with ctx.path.open("rb") as f:
            r = httpx.post("https://api.sightengine.com/1.0/check.json",
                           data={"models": "genai", "api_user": get_key("SIGHTENGINE_API_USER"),
                                 "api_secret": get_key("SIGHTENGINE_API_SECRET")},
                           files={"media": (ctx.media.filename or "media", f)}, timeout=120)
        r.raise_for_status()
        raw = r.json()
        score = (raw.get("type") or {}).get("ai_generated")
        _ai_claim(ctx, "Sightengine", score, "ai_generated", raw)


@register
class RealityDefenderDetector(_ApiDetector):
    order = 72
    name, version, vendor = "api_reality_defender", "1.0", "Reality Defender"
    consent_action, key_names = "api_detect:reality_defender", ("REALITY_DEFENDER_API_KEY",)
    title = "Reality Defender (official SDK)"

    def available(self):
        if (nr := super().available()):
            return nr
        try:
            import realitydefender  # noqa: F401
        except ImportError:
            return NotRun(component=self.name, reason="Reality Defender SDK not installed",
                          how_to_enable="uv pip install realitydefender (official SDK; endpoints are not re-implemented here)")
        return None

    def run(self, ctx: Ctx):
        if (nr := self._consented(ctx)):
            return nr
        import asyncio

        from realitydefender import RealityDefender  # type: ignore

        async def go():
            rd = RealityDefender(api_key=get_key("REALITY_DEFENDER_API_KEY"))
            up = await rd.upload(file_path=str(ctx.path))
            return await rd.get_result(up["request_id"])

        raw = asyncio.run(go())
        score = raw.get("score") if isinstance(raw, dict) else None
        _ai_claim(ctx, "Reality Defender", score, str(raw.get("status", "")) if isinstance(raw, dict) else "",
                  raw if isinstance(raw, dict) else {"raw": str(raw)})
