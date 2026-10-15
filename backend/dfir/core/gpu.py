"""GPU model manager: load models on demand, keep them resident while VRAM allows, evict the
least-recently-used model when a new one would exceed the budget, and fall back to CPU when a
model cannot fit. Every model runs in FP32 (FP16 is ~4x slower on Turing TU117 GPUs)."""
from __future__ import annotations

import gc
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger("dfir.gpu")
BUDGET_MB = int(os.environ.get("DFIR_VRAM_BUDGET_MB", "3300"))


@dataclass
class _Entry:
    obj: Any
    device: str
    est_mb: int
    last_used: float = field(default_factory=time.time)
    unload: Callable[[Any], None] | None = None


class ModelManager:
    def __init__(self):
        self._models: dict[str, _Entry] = {}
        self._lock = threading.RLock()
        self.locks: dict[str, threading.Lock] = {}  # one inference at a time per model

    @staticmethod
    def cuda() -> bool:
        try:
            import torch
            return torch.cuda.is_available()
        except Exception:  # noqa: BLE001
            return False

    def get(self, key: str, loader: Callable[[str], Any], est_mb: int,
            unload: Callable[[Any], None] | None = None) -> tuple[Any, str]:
        """Return (model, device). ``loader(device)`` must build the model on that device."""
        with self._lock:
            e = self._models.get(key)
            if e:
                e.last_used = time.time()
                return e.obj, e.device
            device = "cpu"
            if self.cuda():
                self._make_room(est_mb, keep=key)
                device = "cuda"
            t = time.time()
            try:
                obj = loader(device)
            except Exception as ex:  # noqa: BLE001
                if device == "cuda" and "out of memory" in str(ex).lower():
                    log.warning("%s: GPU out of memory while loading — evicting everything and retrying", key)
                    self.evict_all(except_key=key)
                    try:
                        obj = loader("cuda")
                    except Exception:  # noqa: BLE001
                        log.warning("%s: still no room on GPU — loading on CPU", key)
                        device = "cpu"
                        obj = loader("cpu")
                else:
                    raise
            self._models[key] = _Entry(obj, device, est_mb, unload=unload)
            self.locks.setdefault(key, threading.Lock())
            log.info("loaded %s on %s in %.1fs (est. %d MB)", key, device, time.time() - t, est_mb)
            return obj, device

    def _resident_mb(self) -> int:
        return sum(e.est_mb for e in self._models.values() if e.device == "cuda")

    def _make_room(self, need_mb: int, keep: str) -> None:
        while self._resident_mb() + need_mb > BUDGET_MB:
            victims = sorted(((e.last_used, k) for k, e in self._models.items() if e.device == "cuda" and k != keep))
            if not victims:
                break
            self.unload(victims[0][1])

    def unload(self, key: str) -> None:
        with self._lock:
            e = self._models.pop(key, None)
            if not e:
                return
            if e.unload:
                try:
                    e.unload(e.obj)
                except Exception:  # noqa: BLE001
                    pass
            del e
            gc.collect()
            try:
                import torch
                torch.cuda.empty_cache()
            except Exception:  # noqa: BLE001
                pass
            log.info("unloaded %s from GPU", key)

    def evict_all(self, except_key: str | None = None) -> None:
        for k in list(self._models):
            if k != except_key:
                self.unload(k)

    def status(self) -> list[dict]:
        return [{"key": k, "device": e.device, "est_mb": e.est_mb, "idle_s": round(time.time() - e.last_used)}
                for k, e in self._models.items()]

    def run(self, key: str, fn: Callable[[], Any]) -> Any:
        """Run inference under the model's lock; on CUDA OOM, free other models and retry once."""
        lock = self.locks.setdefault(key, threading.Lock())
        with lock:
            try:
                return fn()
            except RuntimeError as ex:
                if "out of memory" not in str(ex).lower():
                    raise
                log.warning("%s: CUDA OOM during inference — freeing other models and retrying", key)
                self.evict_all(except_key=key)
                return fn()


models = ModelManager()
