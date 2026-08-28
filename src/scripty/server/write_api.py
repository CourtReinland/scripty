"""JSON API for the human-in-the-loop fiction trainer."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from scripty.core.db import Database
from scripty.write import (
    add_reference, advance, generate, judge, list_desks, list_sessions,
    session_view, start_session,
)


class SessionIn(BaseModel):
    genre: str
    tone: str = "measured"
    length: str = "short"
    summary: str
    provider: Optional[str] = None
    draft: bool = True


class JudgeIn(BaseModel):
    result: str
    note: str = ""


class RefIn(BaseModel):
    genre: str
    kind: str
    title: str
    text: str = Field(min_length=1)


def write_router(db: Database) -> APIRouter:
    router = APIRouter(prefix="/api/write", tags=["write"])

    @router.get("/desks")
    def desks() -> list[dict]:
        return list_desks(db)

    @router.get("/sessions")
    def sessions() -> list[dict]:
        return list_sessions(db)

    @router.post("/sessions")
    def start(body: SessionIn) -> dict:
        try:
            view = start_session(
                db, genre=body.genre, tone=body.tone, length=body.length,
                summary=body.summary, provider=body.provider)
            if body.draft:
                view = generate(db, int(view["session"]["id"]))
            return view
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/sessions/{session_id}")
    def show(session_id: int) -> dict:
        try:
            return session_view(db, session_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/sessions/{session_id}/generate")
    def make_draft(session_id: int) -> dict:
        try:
            return generate(db, session_id)
        except ValueError as exc:
            code = 404 if "no write session" in str(exc) else 400
            raise HTTPException(status_code=code, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/sessions/{session_id}/judge")
    def make_judge(session_id: int, body: JudgeIn) -> dict:
        try:
            return judge(db, session_id, body.result, note=body.note)
        except ValueError as exc:
            msg = str(exc)
            code = 404 if "no write session" in msg or "no draft" in msg else 400
            raise HTTPException(status_code=code, detail=msg) from exc

    @router.post("/sessions/{session_id}/next")
    def next_unit(session_id: int) -> dict:
        try:
            return advance(db, session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/refs")
    def add_ref(body: RefIn) -> dict:
        try:
            return add_reference(
                db, desk_slug=body.genre, kind=body.kind,
                title=body.title, text=body.text)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
