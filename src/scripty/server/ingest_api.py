"""Drag-drop ingest + background pass runs (APIRouter mounted by app.py).

Adds the phase-2 desktop endpoints: register a project from a local video
path or a multipart upload, link a known (ground-truth) script the same two
ways, kick off a supervision pass on a daemon thread, and poll its progress.

State lives on the FastAPI app: ``app.state.db`` (Database),
``app.state.progress`` ({project_id: list[str]}, capped at 200 lines),
``app.state.pass_threads`` ({project_id: Thread}) and ``app.state.pass_lock``.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, UploadFile
from pydantic import BaseModel

from scripty.core import config
from scripty.core.db import Database

VIDEO_EXTS: frozenset[str] = frozenset(
    {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"})
SCRIPT_EXTS: frozenset[str] = frozenset({".fountain", ".txt"})

PROGRESS_CAP = 200   #: max progress lines retained per project
PROGRESS_TAIL = 30   #: lines returned by GET .../progress

DEFAULT_MAX_UPLOAD_MB = 4096  #: upload cap; override via SCRIPTY_MAX_UPLOAD_MB
_UPLOAD_CHUNK = 1024 * 1024

router = APIRouter()


class FromPathIn(BaseModel):
    """Body of POST /api/projects/from-path."""

    path: str
    name: Optional[str] = None


class TruthFromPathIn(BaseModel):
    """Body of POST /api/projects/{pid}/truth/from-path."""

    path: str


class PassIn(BaseModel):
    """Body of POST /api/projects/{pid}/passes (all fields optional)."""

    provider: Optional[str] = None
    transcriber: Optional[str] = None


# ---- helpers ----------------------------------------------------------------


def _db(request: Request) -> Database:
    return request.app.state.db


def _project_or_404(db: Database, project_id: int) -> dict:
    row = db.get_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no project {project_id}")
    return row


def _validated_path(raw: str, exts: frozenset[str], kind: str) -> Path:
    """Resolve a user-supplied path and 400 unless it is an existing file
    with an allowed extension. Paths outside SCRIPTY_HOME are accepted by
    design: Scripty is a local single-user app ingesting the user's files."""
    try:
        path = Path(raw).expanduser().resolve()
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400,
                            detail=f"unresolvable {kind} path") from exc
    if not path.exists():
        raise HTTPException(status_code=400, detail=f"{kind} not found: {path}")
    if not path.is_file():
        raise HTTPException(status_code=400,
                            detail=f"{kind} path is not a file: {path}")
    if path.suffix.lower() not in exts:
        allowed = ", ".join(sorted(exts))
        raise HTTPException(
            status_code=400,
            detail=f"unsupported {kind} extension {path.suffix!r} "
                   f"(allowed: {allowed})")
    return path


def _max_upload_bytes() -> int:
    try:
        mb = int(os.environ.get("SCRIPTY_MAX_UPLOAD_MB", "")
                 or DEFAULT_MAX_UPLOAD_MB)
    except ValueError:
        mb = DEFAULT_MAX_UPLOAD_MB
    return max(1, mb) * 1024 * 1024


def _save_upload(upload: UploadFile, exts: frozenset[str], kind: str) -> Path:
    """Stream a multipart upload into SCRIPTY_HOME/uploads with a sanitized,
    deduplicated filename; 400 on empty/dot/control-char names or a bad
    extension, 413 past the size cap. The dedup uses O_EXCL creation so two
    concurrent same-name uploads can never resolve to the same file."""
    sanitized = os.path.basename(upload.filename or "")
    if (not sanitized or sanitized.strip(".") == ""
            or any(ord(c) < 32 or c == "\x7f" for c in sanitized)):
        raise HTTPException(status_code=400, detail="bad upload filename")
    stem, suffix = Path(sanitized).stem, Path(sanitized).suffix
    if suffix.lower() not in exts:
        allowed = ", ".join(sorted(exts))
        raise HTTPException(
            status_code=400,
            detail=f"unsupported {kind} extension {suffix!r} "
                   f"(allowed: {allowed})")
    dest_dir = config.home() / "uploads"
    dest_dir.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:
        dest = dest_dir / (sanitized if n == 1 else f"{stem}-{n}{suffix}")
        try:
            fh = dest.open("xb")   # atomic create-or-retry: no TOCTOU race
        except FileExistsError:
            n += 1
            continue
        break
    limit, written = _max_upload_bytes(), 0
    try:
        with fh:
            while True:
                chunk = upload.file.read(_UPLOAD_CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    raise HTTPException(
                        status_code=413,
                        detail=f"upload larger than {limit} bytes "
                               f"(raise SCRIPTY_MAX_UPLOAD_MB to allow)")
                fh.write(chunk)
    except BaseException:
        dest.unlink(missing_ok=True)   # never leave partial files behind
        raise
    return dest


def _create_project(db: Database, video: Path, name: str | None) -> dict:
    from scripty import pipeline

    try:
        project_id = pipeline.create_project(db, video, name)
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        # ffprobe rejected the file (or it vanished between checks)
        raise HTTPException(status_code=400,
                            detail=f"not a readable video: {exc}") from exc
    return {"project_id": project_id, "project": db.get_project(project_id)}


def _link_truth(db: Database, project_id: int, script: Path) -> dict:
    from scripty import truth

    try:
        truth_id = truth.link_script(db, project_id=project_id,
                                     script_path=script)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _align_completed_passes(db, project_id)
    return {"truth_id": truth_id}


def _align_completed_passes(db: Database, project_id: int) -> None:
    """(Re-)align every completed pass against the just-linked truth script.

    Without this, the natural drop-film -> run-pass -> drop-script flow
    shows no verdicts (alignment otherwise only runs inside a pass), and
    re-linking truth would leave alignments paired to the OLD truth's
    scene indices. Alignment failures must never sink the link itself.
    """
    from scripty.truth.align import align_pass

    for row in db.passes_for_project(project_id):
        if row.get("status") != "complete":
            continue
        try:
            align_pass(db, pass_id=int(row["id"]))
        except Exception:  # noqa: BLE001 — best-effort refresh only
            continue


def _pass_running(db: Database, project_id: int) -> bool:
    return any(p.get("status") == "running"
               for p in db.passes_for_project(project_id))


# ---- project ingest ----------------------------------------------------------


@router.post("/api/projects/from-path")
def project_from_path(body: FromPathIn, request: Request) -> dict:
    video = _validated_path(body.path, VIDEO_EXTS, "video")
    return _create_project(_db(request), video, body.name)


@router.post("/api/projects/upload")
def project_upload(file: UploadFile, request: Request) -> dict:
    dest = _save_upload(file, VIDEO_EXTS, "video")
    video = _validated_path(str(dest), VIDEO_EXTS, "video")
    return _create_project(_db(request), video, None)


# ---- ground-truth ingest -------------------------------------------------------


@router.post("/api/projects/{project_id}/truth/from-path")
def truth_from_path(project_id: int, body: TruthFromPathIn,
                    request: Request) -> dict:
    db = _db(request)
    _project_or_404(db, project_id)
    script = _validated_path(body.path, SCRIPT_EXTS, "script")
    return _link_truth(db, project_id, script)


@router.post("/api/projects/{project_id}/truth/upload")
def truth_upload(project_id: int, file: UploadFile, request: Request) -> dict:
    db = _db(request)
    _project_or_404(db, project_id)
    dest = _save_upload(file, SCRIPT_EXTS, "script")
    script = _validated_path(str(dest), SCRIPT_EXTS, "script")
    return _link_truth(db, project_id, script)


@router.get("/api/projects/{project_id}/truth")
def get_truth(project_id: int, request: Request) -> dict:
    db = _db(request)
    _project_or_404(db, project_id)
    row = db.truth_for_project(project_id)
    if row is None:
        raise HTTPException(status_code=404,
                            detail=f"no truth script for project {project_id}")
    return {"script_path": str(row.get("script_path") or ""),
            "parsed": row.get("parsed") or {}}


# ---- background pass runs ------------------------------------------------------


@router.post("/api/projects/{project_id}/passes", status_code=202)
def start_pass(project_id: int, request: Request,
               body: PassIn | None = None) -> dict:
    from scripty import pipeline

    db = _db(request)
    _project_or_404(db, project_id)
    state = request.app.state
    with state.pass_lock:
        thread = state.pass_threads.get(project_id)
        if (thread is not None and thread.is_alive()) \
                or _pass_running(db, project_id):
            raise HTTPException(
                status_code=409,
                detail=f"a pass is already running for project {project_id}")

        lines: list[str] = []
        state.progress[project_id] = lines

        def cb(message: str) -> None:
            lines.append(str(message))
            if len(lines) > PROGRESS_CAP:
                del lines[: len(lines) - PROGRESS_CAP]

        provider = body.provider if body is not None else None
        transcriber = body.transcriber if body is not None else None

        def worker() -> None:
            try:
                pipeline.run_pass(db, project_id, provider_name=provider,
                                  transcriber_name=transcriber, progress=cb)
            except Exception as exc:  # run_pass already marked the pass failed
                cb(f"pass failed: {exc}")

        thread = threading.Thread(target=worker, daemon=True,
                                  name=f"scripty-pass-{project_id}")
        state.pass_threads[project_id] = thread
        thread.start()
    return {"started": True}


@router.get("/api/projects/{project_id}/progress")
def pass_progress(project_id: int, request: Request) -> dict:
    db = _db(request)
    _project_or_404(db, project_id)
    lines = request.app.state.progress.get(project_id) or []
    return {"running": _pass_running(db, project_id),
            "lines": list(lines[-PROGRESS_TAIL:])}
