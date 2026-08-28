"""Persistence helpers for the writer loop (desks, sessions, drafts, lessons)."""
from __future__ import annotations

from typing import Any, Optional

from scripty.core.db import Database
from scripty.write.desks import DESKS, DESK_BY_SLUG


def ensure_desks(db: Database) -> None:
    """Idempotently seed built-in genre desks and their style-card refs."""
    existing = {row["slug"]: row for row in db.rows(
        "SELECT * FROM write_desks ORDER BY id")}
    for spec in DESKS:
        row = existing.get(spec["slug"])
        if row is None:
            desk_id = db.insert(
                "write_desks", slug=spec["slug"], name=spec["name"],
                hint=spec["hint"], style_card=spec["style_card"])
        else:
            desk_id = int(row["id"])
            if (row.get("style_card") != spec["style_card"]
                    or row.get("hint") != spec["hint"]
                    or row.get("name") != spec["name"]):
                db.update("write_desks", desk_id, name=spec["name"],
                          hint=spec["hint"], style_card=spec["style_card"])
        refs = db.rows(
            "SELECT id FROM write_refs WHERE desk_id = ? AND kind = 'style_card'",
            (desk_id,))
        if not refs:
            db.insert("write_refs", desk_id=desk_id, kind="style_card",
                      title=f"{spec['name']} style card",
                      text=spec["style_card"])


def desk_by_slug(db: Database, slug: str) -> dict:
    ensure_desks(db)
    row = db.row("SELECT * FROM write_desks WHERE slug = ?", (slug,))
    if row is None:  # pragma: no cover — ensure_desks should have created it
        spec = DESK_BY_SLUG[slug]
        desk_id = db.insert(
            "write_desks", slug=spec["slug"], name=spec["name"],
            hint=spec["hint"], style_card=spec["style_card"])
        row = db.row("SELECT * FROM write_desks WHERE id = ?", (desk_id,))
    assert row is not None
    return row


def list_desks(db: Database) -> list[dict]:
    ensure_desks(db)
    return db.rows("SELECT * FROM write_desks ORDER BY slug")


def create_session(db: Database, *, desk_id: int, genre: str, tone: str,
                   length: str, summary: str, unit_kind: str,
                   provider: str) -> int:
    sid = db.insert(
        "write_sessions", desk_id=desk_id, genre=genre, tone=tone,
        length=length, summary=summary, unit_index=1, unit_kind=unit_kind,
        provider=provider, status="active")
    db.emit("write_session_started", {"session_id": sid, "genre": genre})
    return sid


def get_session(db: Database, session_id: int) -> Optional[dict]:
    return db.row("SELECT * FROM write_sessions WHERE id = ?", (session_id,))


def list_sessions(db: Database, limit: int = 50) -> list[dict]:
    return db.rows(
        "SELECT s.*, d.slug AS desk_slug, d.name AS desk_name "
        "FROM write_sessions s JOIN write_desks d ON d.id = s.desk_id "
        "ORDER BY s.id DESC LIMIT ?",
        (limit,))


def update_session(db: Database, session_id: int, **cols: Any) -> None:
    db.update("write_sessions", session_id, **cols)


def add_draft(db: Database, *, session_id: int, unit_index: int, role: str,
              text: str, seed: int, temperature: float, mutation: str,
              parent_id: int | None, signals: dict) -> int:
    did = db.insert(
        "write_drafts", session_id=session_id, unit_index=unit_index,
        role=role, text=text, seed=int(seed), temperature=float(temperature),
        mutation=mutation, parent_id=parent_id, signals=signals)
    db.emit("write_draft_saved", {
        "draft_id": did, "session_id": session_id, "role": role,
        "seed": seed, "mutation": mutation,
    })
    return did


def get_draft(db: Database, draft_id: int) -> Optional[dict]:
    return db.row("SELECT * FROM write_drafts WHERE id = ?",
                  (draft_id,), "write_drafts")


def drafts_for_session(db: Database, session_id: int,
                       unit_index: int | None = None) -> list[dict]:
    if unit_index is None:
        return db.rows(
            "SELECT * FROM write_drafts WHERE session_id = ? ORDER BY id",
            (session_id,), "write_drafts")
    return db.rows(
        "SELECT * FROM write_drafts WHERE session_id = ? AND unit_index = ? "
        "ORDER BY id",
        (session_id, unit_index), "write_drafts")


def set_draft_role(db: Database, draft_id: int, role: str) -> None:
    db.update("write_drafts", draft_id, role=role)


def add_verdict(db: Database, *, session_id: int, desk_id: int,
                champion_id: int, challenger_id: int, result: str,
                note: str) -> int:
    vid = db.insert(
        "write_verdicts", session_id=session_id, desk_id=desk_id,
        champion_id=champion_id, challenger_id=challenger_id,
        result=result, note=note)
    db.emit("write_verdict", {
        "verdict_id": vid, "session_id": session_id, "result": result,
    })
    return vid


def verdicts_for_desk(db: Database, desk_id: int, limit: int = 200) -> list[dict]:
    return db.rows(
        "SELECT * FROM write_verdicts WHERE desk_id = ? ORDER BY id DESC LIMIT ?",
        (desk_id, limit))


def add_lesson(db: Database, *, desk_id: int, rule: str,
               source_verdict_ids: list[int], weight: float = 1.0) -> int:
    lid = db.insert(
        "write_lessons", desk_id=desk_id, rule=rule,
        source_verdict_ids=source_verdict_ids, weight=weight, active=1)
    db.emit("write_lesson", {"lesson_id": lid, "desk_id": desk_id, "rule": rule})
    return lid


def lessons_for_desk(db: Database, desk_id: int, limit: int = 12) -> list[dict]:
    return db.rows(
        "SELECT * FROM write_lessons WHERE desk_id = ? AND active = 1 "
        "ORDER BY weight DESC, id DESC LIMIT ?",
        (desk_id, limit), "write_lessons")


def cited_verdict_ids(db: Database, desk_id: int) -> set[int]:
    cited: set[int] = set()
    for row in db.rows(
            "SELECT source_verdict_ids FROM write_lessons WHERE desk_id = ?",
            (desk_id,), "write_lessons"):
        for vid in row.get("source_verdict_ids") or []:
            try:
                cited.add(int(vid))
            except (TypeError, ValueError):
                continue
    return cited


def add_ref(db: Database, *, desk_id: int, kind: str,
            title: str, text: str) -> int:
    if kind not in ("style_card", "user_excerpt", "public_domain"):
        raise ValueError(
            "kind must be style_card, user_excerpt, or public_domain")
    rid = db.insert("write_refs", desk_id=desk_id, kind=kind,
                    title=title, text=text)
    db.emit("write_ref_added", {"ref_id": rid, "desk_id": desk_id, "kind": kind})
    return rid


def refs_for_desk(db: Database, desk_id: int) -> list[dict]:
    return db.rows(
        "SELECT * FROM write_refs WHERE desk_id = ? ORDER BY id",
        (desk_id,))
