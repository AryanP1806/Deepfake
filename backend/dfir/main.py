"""FastAPI application entry point:  uv run uvicorn dfir.main:app --port 8000"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from .api import cases, investigation, lab, reporting, system
from .core.config import APP_NAME, APP_VERSION, PROJECT_ROOT
from .core.consent import OUTWARD_ACTIONS, ConsentRequired
from .core import logbuf
from .core.db import init_db
from .monitor import scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    logbuf.install()
    logging.getLogger("dfir").info("DFIR backend starting")
    init_db()
    scheduler.start()
    if os.environ.get("DFIR_WARMUP", "1") == "1":  # preload detectors so the first analysis doesn't stall
        from .api.lab import warm_models
        from .core import jobs as _jobs
        _jobs.submit("warmup", None, warm_models)
    yield
    scheduler.stop()


app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5180", "http://127.0.0.1:5180",
                                                 "http://localhost:8000", "http://127.0.0.1:8000"],
                   allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(ConsentRequired)
async def consent_required(_req: Request, exc: ConsentRequired):
    return JSONResponse(status_code=428, content={"detail": str(exc), "consent_action": exc.action,
                                                  "description": OUTWARD_ACTIONS.get(exc.action, exc.action),
                                                  "case_id": exc.case_id})


for r in (cases.router, investigation.router, reporting.router, system.router, lab.router):
    app.include_router(r)


@app.get("/api/health")
def health():
    return {"ok": True, "app": APP_NAME, "version": APP_VERSION}


# --------------------------------------------------------------------------- UI on the same port
# Serve the built frontend (frontend/dist, from `npm run build`) so http://localhost:8000 opens the app.
# Any non-API path falls back to index.html so client-side routes (/case/..., /lab) work on reload.
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        raise HTTPException(404, "Not Found")
    if not (FRONTEND_DIST / "index.html").exists():
        return HTMLResponse(
            "<h3>DFIR backend is running.</h3><p>The UI has not been built yet. Either run "
            "<code>npm run build</code> in <code>frontend/</code> and reload this page, or open the dev UI at "
            "<a href='http://localhost:5180'>http://localhost:5180</a>.</p><p>API docs: <a href='/docs'>/docs</a></p>")
    target = (FRONTEND_DIST / path).resolve()
    if path and target.is_file() and FRONTEND_DIST.resolve() in target.parents:
        return FileResponse(target)
    return FileResponse(FRONTEND_DIST / "index.html")
