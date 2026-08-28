"""SQLite persistence with an append-only event log.

Every state change is recorded twice: as a row in a projection table
(fast queries for the dashboard) and as an event in `events`
(auditable history of what the system believed and when — the substrate
for recursive learning across passes).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    video_path TEXT NOT NULL,
    duration REAL NOT NULL DEFAULT 0,
    fps REAL NOT NULL DEFAULT 24,
    width INTEGER NOT NULL DEFAULT 0,
    height INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS passes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    number INTEGER NOT NULL,
    provider TEXT NOT NULL,
    transcriber TEXT NOT NULL DEFAULT 'none',
    status TEXT NOT NULL DEFAULT 'pending',
    params TEXT NOT NULL DEFAULT '{}',
    metrics TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pass_id INTEGER NOT NULL REFERENCES passes(id),
    idx INTEGER NOT NULL,
    start_s REAL NOT NULL,
    end_s REAL NOT NULL,
    scale TEXT NOT NULL DEFAULT 'MS',
    subject TEXT NOT NULL DEFAULT '',
    angle TEXT NOT NULL DEFAULT 'EYE LEVEL',
    move TEXT NOT NULL DEFAULT 'STATIC',
    int_ext TEXT NOT NULL DEFAULT 'EXT.',
    location TEXT NOT NULL DEFAULT '',
    time_of_day TEXT NOT NULL DEFAULT 'DAY',
    action_text TEXT NOT NULL DEFAULT '',
    characters TEXT NOT NULL DEFAULT '[]',
    mood TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.5,
    keyframes TEXT NOT NULL DEFAULT '[]',
    raw TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS scenes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pass_id INTEGER NOT NULL REFERENCES passes(id),
    idx INTEGER NOT NULL,
    int_ext TEXT NOT NULL DEFAULT 'EXT.',
    location TEXT NOT NULL DEFAULT '',
    time_of_day TEXT NOT NULL DEFAULT 'DAY',
    shot_ids TEXT NOT NULL DEFAULT '[]',
    synopsis TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS dialogue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pass_id INTEGER NOT NULL REFERENCES passes(id),
    shot_id INTEGER,
    scene_id INTEGER,
    character TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    parenthetical TEXT NOT NULL DEFAULT '',
    start_s REAL NOT NULL DEFAULT 0,
    end_s REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    pass_id INTEGER NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    field TEXT NOT NULL,
    model_value TEXT NOT NULL DEFAULT '',
    human_value TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'human',
    scope TEXT NOT NULL DEFAULT 'project',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL DEFAULT 'project',
    project_id INTEGER,
    field TEXT NOT NULL,
    rule TEXT NOT NULL,
    source_ids TEXT NOT NULL DEFAULT '[]',
    weight REAL NOT NULL DEFAULT 1.0,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS truth_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    script_path TEXT NOT NULL,
    parsed TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pass_id INTEGER NOT NULL REFERENCES passes(id),
    truth_id INTEGER NOT NULL REFERENCES truth_links(id),
    report TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS describe_prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pass_id INTEGER NOT NULL REFERENCES passes(id),
    scene_id INTEGER,
    shot_id INTEGER,
    revision INTEGER NOT NULL DEFAULT 1,
    target TEXT NOT NULL DEFAULT 'generic',
    prompt TEXT NOT NULL,
    continuity TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    kind TEXT NOT NULL,
    ref TEXT NOT NULL,
    pass_id INTEGER,
    meta TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shots_pass ON shots(pass_id, idx);
CREATE INDEX IF NOT EXISTS idx_corrections_proj ON corrections(project_id, field);
CREATE INDEX IF NOT EXISTS idx_lessons_field ON lessons(field, active);

CREATE TABLE IF NOT EXISTS write_desks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    hint TEXT NOT NULL DEFAULT '',
    style_card TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS write_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    desk_id INTEGER NOT NULL REFERENCES write_desks(id),
    genre TEXT NOT NULL,
    tone TEXT NOT NULL,
    length TEXT NOT NULL,
    summary TEXT NOT NULL,
    unit_index INTEGER NOT NULL DEFAULT 1,
    unit_kind TEXT NOT NULL DEFAULT 'story',
    champion_id INTEGER,
    challenger_id INTEGER,
    provider TEXT NOT NULL DEFAULT 'mock',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS write_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES write_sessions(id),
    unit_index INTEGER NOT NULL DEFAULT 1,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    seed INTEGER NOT NULL,
    temperature REAL NOT NULL,
    mutation TEXT NOT NULL DEFAULT '',
    parent_id INTEGER,
    signals TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS write_verdicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    desk_id INTEGER NOT NULL,
    champion_id INTEGER NOT NULL,
    challenger_id INTEGER NOT NULL,
    result TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS write_lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    desk_id INTEGER NOT NULL,
    rule TEXT NOT NULL,
    source_verdict_ids TEXT NOT NULL DEFAULT '[]',
    weight REAL NOT NULL DEFAULT 1.0,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS write_refs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    desk_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_write_sessions_desk ON write_sessions(desk_id);
CREATE INDEX IF NOT EXISTS idx_write_drafts_session ON write_drafts(session_id, unit_index);
CREATE INDEX IF NOT EXISTS idx_write_lessons_desk ON write_lessons(desk_id, active);
CREATE INDEX IF NOT EXISTS idx_write_refs_desk ON write_refs(desk_id);
"""

# columns stored as JSON text, decoded on read
_JSON_COLS = {
    "passes": ("params", "metrics"),
    "shots": ("characters", "keyframes", "raw"),
    "scenes": ("shot_ids",),
    "lessons": ("source_ids",),
    "truth_links": ("parsed",),
    "alignments": ("report",),
    "describe_prompts": ("continuity",),
    "artifacts": ("meta",),
    "events": ("payload",),
    "write_drafts": ("signals",),
    "write_lessons": ("source_verdict_ids",),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    """Thread-safe-enough SQLite wrapper (one connection, one lock)."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ---- primitives -------------------------------------------------------

    @staticmethod
    def _encode(value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return json.dumps(value, sort_keys=True)
        if isinstance(value, bool):
            return int(value)
        if hasattr(value, "value") and not isinstance(value, (int, float)):
            return value.value  # Enum
        return value

    def insert(self, table: str, **cols: Any) -> int:
        cols = {k: self._encode(v) for k, v in cols.items()}
        if "created_at" not in cols and table in (
            "projects", "passes", "corrections", "lessons", "truth_links",
            "alignments", "describe_prompts", "artifacts",
            "write_desks", "write_sessions", "write_drafts",
            "write_verdicts", "write_lessons", "write_refs",
        ):
            cols["created_at"] = _now()
        names = ", ".join(cols)
        marks = ", ".join("?" for _ in cols)
        with self._lock:
            cur = self._conn.execute(
                f"INSERT INTO {table} ({names}) VALUES ({marks})",
                tuple(cols.values()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def update(self, table: str, row_id: int, **cols: Any) -> None:
        cols = {k: self._encode(v) for k, v in cols.items()}
        sets = ", ".join(f"{k} = ?" for k in cols)
        with self._lock:
            self._conn.execute(
                f"UPDATE {table} SET {sets} WHERE id = ?",
                (*cols.values(), row_id),
            )
            self._conn.commit()

    def rows(self, sql: str, params: tuple = (), table: str | None = None) -> list[dict]:
        with self._lock:
            found = [dict(r) for r in self._conn.execute(sql, params).fetchall()]
        if table:
            for row in found:
                for col in _JSON_COLS.get(table, ()):
                    if col in row and isinstance(row[col], str):
                        try:
                            row[col] = json.loads(row[col])
                        except (ValueError, TypeError):
                            pass
        return found

    def row(self, sql: str, params: tuple = (), table: str | None = None) -> Optional[dict]:
        found = self.rows(sql, params, table)
        return found[0] if found else None

    def emit(self, event_type: str, payload: dict | None = None) -> int:
        return self.insert("events", ts=_now(), type=event_type,
                           payload=payload or {})

    # ---- projects / passes ------------------------------------------------

    def create_project(self, *, name: str, slug: str, video_path: str,
                       duration: float, fps: float, width: int, height: int) -> int:
        pid = self.insert("projects", name=name, slug=slug,
                          video_path=video_path, duration=duration, fps=fps,
                          width=width, height=height)
        self.emit("project_created", {"project_id": pid, "name": name})
        return pid

    def get_project(self, project_id: int) -> Optional[dict]:
        return self.row("SELECT * FROM projects WHERE id = ?", (project_id,))

    def project_by_slug(self, slug: str) -> Optional[dict]:
        return self.row("SELECT * FROM projects WHERE slug = ?", (slug,))

    def list_projects(self) -> list[dict]:
        return self.rows("SELECT * FROM projects ORDER BY id")

    def create_pass(self, *, project_id: int, provider: str,
                    transcriber: str = "none", params: dict | None = None) -> int:
        # MAX(number)+1 is computed INSIDE the insert statement so two
        # concurrent create_pass calls (even from separate processes) can
        # never read the same maximum and mint duplicate pass numbers.
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO passes (project_id, number, provider, transcriber,"
                " status, params, created_at)"
                " SELECT ?, COALESCE(MAX(number), 0) + 1, ?, ?, 'running', ?, ?"
                " FROM passes WHERE project_id = ?",
                (project_id, provider, transcriber,
                 self._encode(params or {}), _now(), project_id))
            self._conn.commit()
            pass_id = int(cur.lastrowid)
            number = int(self._conn.execute(
                "SELECT number FROM passes WHERE id = ?",
                (pass_id,)).fetchone()[0])
        self.emit("pass_started", {"pass_id": pass_id, "project_id": project_id,
                                   "number": number, "provider": provider})
        return pass_id

    def get_pass(self, pass_id: int) -> Optional[dict]:
        return self.row("SELECT * FROM passes WHERE id = ?", (pass_id,), "passes")

    def passes_for_project(self, project_id: int) -> list[dict]:
        return self.rows("SELECT * FROM passes WHERE project_id = ? ORDER BY number",
                         (project_id,), "passes")

    def finish_pass(self, pass_id: int, status: str, metrics: dict) -> None:
        self.update("passes", pass_id, status=status, metrics=metrics)
        self.emit("pass_finished", {"pass_id": pass_id, "status": status,
                                    "metrics": metrics})

    # ---- shots / scenes / dialogue ----------------------------------------

    def add_shot(self, **cols: Any) -> int:
        sid = self.insert("shots", **cols)
        self.emit("shot_analyzed", {"shot_id": sid, "pass_id": cols.get("pass_id"),
                                    "idx": cols.get("idx")})
        return sid

    def shots_for_pass(self, pass_id: int) -> list[dict]:
        return self.rows("SELECT * FROM shots WHERE pass_id = ? ORDER BY idx",
                         (pass_id,), "shots")

    def get_shot(self, shot_id: int) -> Optional[dict]:
        return self.row("SELECT * FROM shots WHERE id = ?", (shot_id,), "shots")

    def add_scene(self, **cols: Any) -> int:
        return self.insert("scenes", **cols)

    def scenes_for_pass(self, pass_id: int) -> list[dict]:
        return self.rows("SELECT * FROM scenes WHERE pass_id = ? ORDER BY idx",
                         (pass_id,), "scenes")

    def add_dialogue(self, **cols: Any) -> int:
        return self.insert("dialogue", **cols)

    def dialogue_for_pass(self, pass_id: int) -> list[dict]:
        return self.rows("SELECT * FROM dialogue WHERE pass_id = ? ORDER BY start_s",
                         (pass_id,), "dialogue")

    # ---- learning ----------------------------------------------------------

    def add_correction(self, **cols: Any) -> int:
        cid = self.insert("corrections", **cols)
        self.emit("correction_added", {"correction_id": cid, **{
            k: cols.get(k) for k in ("project_id", "pass_id", "entity_type",
                                     "entity_id", "field", "source")}})
        return cid

    def corrections(self, *, project_id: int | None = None,
                    field: str | None = None, limit: int = 200) -> list[dict]:
        sql = "SELECT * FROM corrections WHERE 1=1"
        params: list[Any] = []
        if project_id is not None:
            sql += " AND (project_id = ? OR scope = 'global')"
            params.append(project_id)
        if field:
            sql += " AND field = ?"
            params.append(field)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return self.rows(sql, tuple(params), "corrections")

    def add_lesson(self, **cols: Any) -> int:
        lid = self.insert("lessons", **cols)
        self.emit("lesson_distilled", {"lesson_id": lid,
                                       "field": cols.get("field"),
                                       "rule": cols.get("rule")})
        return lid

    def lessons(self, *, project_id: int | None = None,
                field: str | None = None, limit: int = 50) -> list[dict]:
        sql = "SELECT * FROM lessons WHERE active = 1"
        params: list[Any] = []
        if project_id is not None:
            sql += " AND (project_id = ? OR project_id IS NULL OR scope = 'global')"
            params.append(project_id)
        if field:
            sql += " AND field = ?"
            params.append(field)
        sql += " ORDER BY weight DESC, id DESC LIMIT ?"
        params.append(limit)
        return self.rows(sql, tuple(params), "lessons")

    def deactivate_lesson(self, lesson_id: int) -> None:
        self.update("lessons", lesson_id, active=0)

    # ---- ground truth / describe / artifacts -------------------------------

    def add_truth(self, *, project_id: int, script_path: str, parsed: dict) -> int:
        tid = self.insert("truth_links", project_id=project_id,
                          script_path=script_path, parsed=parsed)
        self.emit("truth_linked", {"truth_id": tid, "project_id": project_id,
                                   "script_path": script_path})
        return tid

    def truth_for_project(self, project_id: int) -> Optional[dict]:
        return self.row(
            "SELECT * FROM truth_links WHERE project_id = ? ORDER BY id DESC",
            (project_id,), "truth_links")

    def add_alignment(self, *, pass_id: int, truth_id: int, report: dict) -> int:
        return self.insert("alignments", pass_id=pass_id, truth_id=truth_id,
                           report=report)

    def alignment_for_pass(self, pass_id: int) -> Optional[dict]:
        return self.row(
            "SELECT * FROM alignments WHERE pass_id = ? ORDER BY id DESC",
            (pass_id,), "alignments")

    def add_describe_prompt(self, **cols: Any) -> int:
        return self.insert("describe_prompts", **cols)

    def prompts_for_pass(self, pass_id: int, revision: int | None = None) -> list[dict]:
        if revision is None:
            return self.rows(
                "SELECT * FROM describe_prompts WHERE pass_id = ? "
                "ORDER BY revision, scene_id, shot_id",
                (pass_id,), "describe_prompts")
        return self.rows(
            "SELECT * FROM describe_prompts WHERE pass_id = ? AND revision = ? "
            "ORDER BY scene_id, shot_id",
            (pass_id, revision), "describe_prompts")

    def add_artifact(self, *, project_id: int, kind: str, ref: str,
                     pass_id: int | None = None, meta: dict | None = None) -> int:
        aid = self.insert("artifacts", project_id=project_id, kind=kind,
                          ref=ref, pass_id=pass_id, meta=meta or {})
        self.emit("artifact_saved", {"artifact_id": aid, "kind": kind,
                                     "project_id": project_id})
        return aid

    def artifacts_for_project(self, project_id: int) -> list[dict]:
        return self.rows(
            "SELECT * FROM artifacts WHERE project_id = ? ORDER BY id",
            (project_id,), "artifacts")
