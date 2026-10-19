"""Candidate-device fingerprints.

A *candidate device* is a named sensor-noise fingerprint built from reference photos that the investigator attests came
from one physical camera. It is evidence DERIVED from those photos, so:

  * the fingerprint is stored in the case's evidence vault (hashed, read-only), and its creation is written to the
    chain-of-custody ledger together with the SHA-256 of every reference photo it was built from;
  * the references' coherence (leave-one-out) is measured and stored, so a fingerprint built from photos that do not
    share a pattern is flagged instead of silently used;
  * a fingerprint only ever *tests a hypothesis* ("could this image come from THIS camera?"). It never names a camera.
"""
from __future__ import annotations

import io
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ..core.db import case_dir, vault_for
from . import prnu

INDEX = "devices.json"


def _index_path(case_id: str) -> Path:
    return case_dir(case_id) / INDEX


def list_devices(case_id: str) -> list[dict]:
    p = _index_path(case_id)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _save(case_id: str, devices: list[dict]) -> None:
    _index_path(case_id).write_text(json.dumps(devices, indent=2), encoding="utf-8")


def create_device(case_id: str, name: str, refs: list[dict], note: str = "", actor: str = "investigator") -> dict:
    """refs: [{id, sha256, label}] — photos attested to come from the camera. All must share one pixel size."""
    v = vault_for(case_id)
    grays, used, skipped = [], [], []
    for r in refs:
        g = prnu.load_gray(v.path(r["sha256"]))
        if grays and g.shape != grays[0].shape:
            skipped.append(r["label"])
            continue
        grays.append(g)
        used.append(r)
    if len(grays) < prnu.MIN_REFERENCES:
        raise ValueError(f"A fingerprint needs at least {prnu.MIN_REFERENCES} reference photos of exactly the same pixel size "
                         f"(found {len(grays)}{'; ' + str(len(skipped)) + ' had a different size' if skipped else ''}).")
    if min(grays[0].shape) < 256:
        raise ValueError("The reference photos are too small (under 256 px) for a sensor-noise fingerprint.")
    k = prnu._crop(prnu.estimate_fingerprint([prnu._crop(g) for g in grays]))
    coherence = prnu.reference_coherence(grays)
    buf = io.BytesIO()
    np.save(buf, k.astype(np.float16))
    did = "dev_" + uuid.uuid4().hex[:10]
    rec = v.store(buf.getvalue(), kind="artifact", filename=f"{did}_fingerprint.npy", mime="application/x-npy",
                  tool="dfir.forensics.prnu@1", actor=actor,
                  acquisition={"derived_from": [r["sha256"] for r in used], "analysis": "prnu_fingerprint", "device_name": name})
    dev = {"id": did, "name": name, "note": note, "created_at": datetime.now(timezone.utc).isoformat(),
           "height": int(grays[0].shape[0]), "width": int(grays[0].shape[1]), "n_references": len(used),
           "references": [{"id": r["id"], "sha256": r["sha256"], "label": r["label"]} for r in used],
           "skipped_references": skipped, "fingerprint_sha256": rec["sha256"], "coherence": coherence,
           # every reference must match the others: a single photo from another camera spoils a fingerprint
           "coherent": coherence["fraction_consistent"] == 1.0,
           "suspect_references": [used[i]["label"] for i in coherence.get("outliers", []) if i < len(used)],
           "attestation": "The investigator states these photos were taken with this camera. The app cannot verify that."}
    devs = list_devices(case_id)
    devs.append(dev)
    _save(case_id, devs)
    v.custody.append("forensics.device_fingerprint_created", actor=actor, tool="dfir.forensics.prnu@1",
                     item_sha256=rec["sha256"], details={"device": did, "name": name, "references": [r["sha256"] for r in used],
                                                         "coherent": dev["coherent"], "median_loo_pce": coherence["median_pce"]})
    return dev


def delete_device(case_id: str, device_id: str, actor: str = "investigator") -> bool:
    devs = list_devices(case_id)
    keep = [d for d in devs if d["id"] != device_id]
    if len(keep) == len(devs):
        return False
    _save(case_id, keep)  # the hashed fingerprint stays in the vault; only the working index entry is removed
    vault_for(case_id).custody.append("forensics.device_fingerprint_removed", actor=actor, details={"device": device_id})
    return True


def load_fingerprint(case_id: str, dev: dict) -> np.ndarray:
    v = vault_for(case_id)
    return np.load(io.BytesIO(v.path(dev["fingerprint_sha256"]).read_bytes())).astype(np.float32)
