"""FastAPI dashboard + JSON API — the face of the machine script supervisor.

`create_app` wires a single `Database` into a set of read endpoints (projects,
passes, shots, script, prompts, alignment, metrics), the correction/lesson
write endpoints that drive the learning loop, and two guarded media routes.
"""
from __future__ import annotations

import mimetypes
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from scripty.core import config
from scripty.core.db import Database

STATIC_DIR = Path(__file__).resolve().parent / "static"

#: hostnames the local single-user server will answer for. Anything else in
#: the Host header means a DNS-rebinding attempt (a remote page whose hostname
#: was re-pointed at 127.0.0.1) and is rejected. "testserver" is Starlette's
#: TestClient default; it is a bare label that cannot resolve via public DNS.
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "testserver"})

#: methods that never mutate state and therefore skip the Origin check
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _bare_host(header: str) -> str:
    """Strip the port from a Host header value ('[::1]:8787' -> '::1')."""
    value = (header or "").strip().lower()
    if value.startswith("["):                      # bracketed IPv6
        return value[1:value.index("]")] if "]" in value else value
    if value.count(":") == 1:                      # host:port
        return value.rsplit(":", 1)[0]
    return value                                   # bare host (or raw IPv6)


def _sweep_stale_passes(db: Database) -> None:
    """Fail any pass row left 'running' by a previous process.

    Pass workers are daemon threads: if the server is quit/killed mid-pass,
    ``finish_pass`` never runs and the row stays 'running' forever, which
    would 409-block RUN PASS and make /progress report running:true for good.
    Reconcile at startup — nothing can actually be running yet.
    """
    for row in db.rows("SELECT id FROM passes WHERE status = 'running'"):
        db.finish_pass(int(row["id"]), "failed",
                       {"error": "interrupted: server stopped mid-pass"})


class CorrectionIn(BaseModel):
    """Body of POST /api/corrections — one field-level human fix."""

    project_id: int
    pass_id: int
    entity_type: str
    entity_id: int
    field: str
    model_value: str = ""
    human_value: str = ""
    note: str = ""
    scope: str = "project"


class DistillIn(BaseModel):
    """Body of POST /api/lessons/distill."""

    project_id: Optional[int] = Field(default=None)


def _pick_brain() -> Any:
    """Mock brain unless Anthropic credentials resolve; None if brain module
    is unavailable (learn.distill then uses its deterministic fallback)."""
    try:
        from scripty.brain import get_brain
    except ImportError:
        return None
    name = "anthropic" if config.default_provider() == "anthropic" else "mock"
    try:
        return get_brain(name)
    except Exception:
        return None


def _guess_media_type(path: Path, fallback: str) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or fallback


def create_app(db_path: Path | str | None = None) -> FastAPI:
    """Build the Scripty dashboard app around one SQLite database."""
    db = Database(Path(db_path) if db_path is not None else config.db_path())
    _sweep_stale_passes(db)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        # Give in-flight pass workers a moment to finish, and never close
        # the shared Database underneath a live worker (close() is not
        # lock-guarded): daemon threads die with the process anyway, and
        # _sweep_stale_passes reconciles their rows on the next startup.
        threads = list(_app.state.pass_threads.values())
        for thread in threads:
            thread.join(timeout=5.0)
        if not any(t.is_alive() for t in threads):
            db.close()

    app = FastAPI(title="Scripty", lifespan=lifespan)
    app.state.db = db
    app.state.progress = {}          # {project_id: list[str]} pass progress
    app.state.pass_threads = {}      # {project_id: threading.Thread}
    app.state.pass_lock = threading.Lock()

    @app.middleware("http")
    async def _local_only(request: Request, call_next: Any) -> Any:
        # Host check: defeats DNS rebinding (browser would send the
        # attacker's hostname even after the rebind to 127.0.0.1).
        if _bare_host(request.headers.get("host", "")) not in _LOCAL_HOSTS:
            return JSONResponse({"detail": "invalid host header"},
                                status_code=400)
        # Origin check on state-changing requests: blocks CSRF, including
        # preflight-less "simple" requests (multipart uploads, empty-body
        # POSTs). Browsers always attach Origin to cross-origin POSTs;
        # non-browser local clients (CLI, Electron main) send none.
        if request.method not in _SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin is not None and \
                    (urlsplit(origin).hostname or "").lower() not in _LOCAL_HOSTS:
                return JSONResponse(
                    {"detail": "cross-origin request rejected"},
                    status_code=403)
        return await call_next(request)

    from scripty.server.ingest_api import router as ingest_router
    app.include_router(ingest_router)

    # ---- lookups that 404 --------------------------------------------------

    def _project(project_id: int) -> dict:
        row = db.get_project(project_id)
        if row is None:
            raise HTTPException(status_code=404,
                                detail=f"no project {project_id}")
        return row

    def _pass(pass_id: int) -> dict:
        row = db.get_pass(pass_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"no pass {pass_id}")
        return row

    # ---- projects / passes -------------------------------------------------

    @app.get("/api/projects")
    def list_projects() -> list[dict]:
        return db.list_projects()

    @app.get("/api/projects/{project_id}")
    def project_detail(project_id: int) -> dict:
        project = _project(project_id)
        return {
            "project": project,
            "passes": db.passes_for_project(project_id),
            "artifacts": db.artifacts_for_project(project_id),
            "truth": db.truth_for_project(project_id) is not None,
        }

    @app.get("/api/passes/{pass_id}")
    def pass_detail(pass_id: int) -> dict:
        pass_row = _pass(pass_id)
        return {
            "pass": pass_row,
            "shots": db.shots_for_pass(pass_id),
            "scenes": db.scenes_for_pass(pass_id),
            "dialogue": db.dialogue_for_pass(pass_id),
            "metrics": pass_row.get("metrics") or {},
        }

    @app.get("/api/passes/{pass_id}/script")
    def pass_script(pass_id: int) -> dict:
        from scripty.script import render_html, to_fountain
        pass_row = _pass(pass_id)
        project = _project(int(pass_row["project_id"]))
        title = str(project.get("name") or "UNTITLED").upper()
        fountain = to_fountain(
            db.scenes_for_pass(pass_id), db.shots_for_pass(pass_id),
            db.dialogue_for_pass(pass_id), title=title, author="Scripty")
        return {"fountain": fountain,
                "html": render_html(fountain, title=title)}

    @app.get("/api/passes/{pass_id}/cutlog")
    def pass_cutlog(pass_id: int) -> PlainTextResponse:
        from scripty.script import cut_log
        pass_row = _pass(pass_id)
        project = _project(int(pass_row["project_id"]))
        fps = float(project.get("fps") or 24.0)
        return PlainTextResponse(cut_log(db.shots_for_pass(pass_id), fps=fps))

    @app.get("/api/passes/{pass_id}/prompts")
    def pass_prompts(pass_id: int,
                     revision: Optional[int] = Query(default=None)) -> list[dict]:
        _pass(pass_id)
        return db.prompts_for_pass(pass_id, revision)

    @app.get("/api/passes/{pass_id}/alignment")
    def pass_alignment(pass_id: int) -> dict:
        _pass(pass_id)
        row = db.alignment_for_pass(pass_id)
        if row is None:
            raise HTTPException(status_code=404,
                                detail=f"no alignment for pass {pass_id}")
        return row.get("report") or {}

    # ---- learning loop -----------------------------------------------------

    @app.post("/api/corrections")
    def post_correction(body: CorrectionIn) -> dict:
        from scripty.learn import memory
        _project(body.project_id)
        _pass(body.pass_id)
        try:
            correction_id = memory.record_correction(
                db, project_id=body.project_id, pass_id=body.pass_id,
                entity_type=body.entity_type, entity_id=body.entity_id,
                field=body.field, model_value=body.model_value,
                human_value=body.human_value, note=body.note,
                scope=body.scope)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"id": correction_id}

    @app.post("/api/lessons/distill")
    def post_distill(body: DistillIn | None = None) -> dict:
        from scripty.learn import distill
        project_id = body.project_id if body is not None else None
        lesson_ids = distill(db, _pick_brain(), project_id=project_id)
        return {"lesson_ids": lesson_ids, "count": len(lesson_ids)}

    @app.get("/api/lessons")
    def list_lessons(project_id: Optional[int] = Query(default=None)) -> list[dict]:
        return db.lessons(project_id=project_id)

    @app.post("/api/lessons/{lesson_id}/deactivate")
    def deactivate_lesson(lesson_id: int) -> dict:
        row = db.row("SELECT id FROM lessons WHERE id = ?", (lesson_id,))
        if row is None:
            raise HTTPException(status_code=404,
                                detail=f"no lesson {lesson_id}")
        db.deactivate_lesson(lesson_id)
        return {"ok": True, "id": lesson_id}

    @app.get("/api/metrics/{project_id}")
    def project_metrics(project_id: int) -> list[dict]:
        _project(project_id)
        return [{
            "pass_id": p["id"],
            "number": p["number"],
            "status": p["status"],
            "created_at": p.get("created_at", ""),
            "metrics": p.get("metrics") or {},
        } for p in db.passes_for_project(project_id)]

    # ---- media (path-validated) --------------------------------------------

    @app.get("/media/video/{project_id}")
    def media_video(project_id: int) -> FileResponse:
        project = _project(project_id)
        try:
            video = Path(str(project.get("video_path") or "")).resolve()
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=404,
                                detail="video path unresolvable") from exc
        if not video.is_file():
            raise HTTPException(status_code=404, detail="video file missing")
        return FileResponse(video,
                            media_type=_guess_media_type(video, "video/mp4"))

    @app.get("/media/keyframe")
    def media_keyframe(path: str = Query(...)) -> FileResponse:
        base = (config.home() / "projects").resolve()
        try:
            target = Path(path).resolve()
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=403,
                                detail="path not allowed") from exc
        if not target.is_relative_to(base):
            raise HTTPException(status_code=403, detail="path not allowed")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="keyframe missing")
        return FileResponse(target,
                            media_type=_guess_media_type(target, "image/jpeg"))

    # ---- dashboard ---------------------------------------------------------

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
