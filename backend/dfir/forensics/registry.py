"""Forensic-module registry with plugin discovery.

Three ways to add a module, none of which touch the core code:

1. Built-in modules in ``dfir/forensics/modules/`` (always present).
2. An installable package that exposes an entry point in the ``dfir.forensics`` group, e.g. in its pyproject.toml::

       [project.entry-points."dfir.forensics"]
       my_noiseprint = "my_package.module:NoiseprintModule"

   so ``pip install my-dfir-noiseprint`` adds it, and ``pip uninstall`` removes it.
3. A drop-in file in the plugin folder (``plugins/`` beside the project, or ``DFIR_PLUGIN_DIR``). The file defines a
   ``ForensicModule`` subclass decorated with ``@register`` (or a ``MODULES = [...]`` list) and may set
   ``DFIR_PLUGIN_API = 1``. Drop-in files are the user's own code and run with the app's permissions.

Optional model weights are declared by the module (``model_specs``) and registered with the model store, so
``scripts/setup_models.py <key>`` fetches them independently of everything else. A module whose requirements
are missing is listed as *unavailable* with the exact install command; it never breaks the others.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sys
from pathlib import Path

from ..core.config import PROJECT_ROOT
from ..core import model_store
from .base import API_VERSION, ForensicModule

log = logging.getLogger("dfir.forensics")

_MODULES: dict[str, ForensicModule] = {}
_ORIGIN: dict[str, str] = {}
_PLUGIN_REPORT: list[dict] = []
_loaded = False
_trace: list[str] | None = None  # ids registered while a plugin file is being executed
ENTRY_POINT_GROUP = "dfir.forensics"


def plugin_dir() -> Path:
    return Path(os.environ.get("DFIR_PLUGIN_DIR") or PROJECT_ROOT / "plugins")


def register(cls: type[ForensicModule]) -> type[ForensicModule]:
    """Class decorator: instantiate and add the module. Re-registering an id replaces it (plugin override)."""
    inst = cls()
    _MODULES[inst.id] = inst
    _ORIGIN.setdefault(inst.id, "built-in")
    if _trace is not None:
        _trace.append(inst.id)
    for spec in getattr(inst, "model_specs", []) or []:
        model_store.SPECS.setdefault(spec.key, spec)  # lets scripts/setup_models.py fetch the plugin's weights
    return cls


def _adopt(obj, origin: str) -> list[str]:
    """Accept a class, an instance, or an iterable of them."""
    added = []
    items = obj if isinstance(obj, (list, tuple, set)) else [obj]
    for it in items:
        cls = it if isinstance(it, type) else type(it)
        if not issubclass(cls, ForensicModule):
            raise TypeError(f"{cls!r} is not a ForensicModule")
        register(cls)
        _ORIGIN[cls.id] = origin
        added.append(cls.id)
    return added


def _load_entry_points() -> None:
    try:
        from importlib.metadata import entry_points
        eps = entry_points(group=ENTRY_POINT_GROUP)
    except Exception as e:  # noqa: BLE001
        log.warning("entry-point discovery failed: %s", e)
        return
    for ep in eps:
        rec = {"source": f"entry point {ep.name}", "name": ep.name, "loaded": False, "modules": [], "error": ""}
        try:
            rec["modules"] = _adopt(ep.load(), f"plugin package ({ep.value})")
            rec["loaded"] = True
        except Exception as e:  # noqa: BLE001
            rec["error"] = f"{type(e).__name__}: {e}"[:300]
            log.warning("plugin %s failed to load: %s", ep.name, e)
        _PLUGIN_REPORT.append(rec)


def _load_dropins() -> None:
    global _trace
    d = plugin_dir()
    if not d.is_dir():
        return
    for f in sorted(d.glob("*.py")):
        if f.name.startswith("_"):
            continue
        rec = {"source": f"plugin file {f.name}", "name": f.stem, "loaded": False, "modules": [], "error": ""}
        snapshot, origins = dict(_MODULES), dict(_ORIGIN)
        _trace = []
        try:
            spec = importlib.util.spec_from_file_location(f"dfir_plugin_{f.stem}", f)
            mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
            sys.modules[spec.name] = mod  # type: ignore[union-attr]
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            api = getattr(mod, "DFIR_PLUGIN_API", API_VERSION)
            if api != API_VERSION:
                raise RuntimeError(f"written for plugin API {api}, this app provides API {API_VERSION}")
            if getattr(mod, "MODULES", None):
                _adopt(mod.MODULES, f"plugin file ({f.name})")
            ids = list(dict.fromkeys(_trace))
            for mid in ids:
                _ORIGIN[mid] = f"plugin file ({f.name})"
            rec["modules"], rec["loaded"] = ids, True
        except Exception as e:  # noqa: BLE001
            _MODULES.clear(); _MODULES.update(snapshot)  # a broken plugin must not leave half-registered modules behind
            _ORIGIN.clear(); _ORIGIN.update(origins)
            rec["error"] = f"{type(e).__name__}: {e}"[:300]
            log.warning("plugin file %s failed to load: %s", f.name, e)
        finally:
            _trace = None
        _PLUGIN_REPORT.append(rec)


def load_plugins(force: bool = False) -> list[dict]:
    """Import the built-in modules, then discover plugins. Safe to call repeatedly."""
    global _loaded
    if _loaded and not force:
        return _PLUGIN_REPORT
    if force:
        for mid in [m for m, o in _ORIGIN.items() if o != "built-in"]:
            _MODULES.pop(mid, None)
            _ORIGIN.pop(mid, None)
        _PLUGIN_REPORT.clear()
    from . import modules  # noqa: F401  (importing registers the six built-in modules)
    _load_entry_points()
    _load_dropins()
    _loaded = True
    return _PLUGIN_REPORT


def all_modules() -> list[ForensicModule]:
    load_plugins()
    return sorted(_MODULES.values(), key=lambda m: (m.order, m.id))


def get(module_id: str) -> ForensicModule | None:
    load_plugins()
    return _MODULES.get(module_id)


def origin(module_id: str) -> str:
    return _ORIGIN.get(module_id, "built-in")


def plugin_report() -> list[dict]:
    load_plugins()
    return list(_PLUGIN_REPORT)


def describe() -> list[dict]:
    """Everything the UI needs to list the modules: availability, requirements, validation, plugin origin."""
    from .base import validation_for
    out = []
    for m in all_modules():
        reqs = m.check_requirements()
        why = m.unavailable_reason()
        v = validation_for(m.id)
        out.append({"id": m.id, "title": m.title, "question": m.question, "version": m.version, "modalities": list(m.modalities),
                    "available": why is None, "reason": why or "", "origin": origin(m.id),
                    "requirements": [r.model_dump() for r in reqs], "limitations": list(m.limitations),
                    "validation": v.model_dump(), "api_version": API_VERSION})
    return out
