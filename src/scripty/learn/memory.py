"""Correction memory: record human fixes, recall them into provider prompts,
and measure pass-over-pass agreement (the headline learning-curve number).
"""
from __future__ import annotations

import difflib
import re
from typing import Any

from scripty.core import config
from scripty.core.db import Database
from scripty.core.models import (
    CORRECTABLE_FIELDS,
    CameraAngle,
    CameraMove,
    Correction,
    IntExt,
    Lesson,
    RecallBundle,
    ShotScale,
    TimeOfDay,
    parse_enum,
)

try:  # rapidfuzz is a declared dependency; degrade gracefully if absent
    from rapidfuzz import fuzz as _fuzz
except ImportError:  # pragma: no cover
    _fuzz = None


# --------------------------------------------------------------------------- #
# Entity plumbing
# --------------------------------------------------------------------------- #

_ENTITY_TABLES: dict[str, str] = {
    "shot": "shots", "shots": "shots",
    "scene": "scenes", "scenes": "scenes",
    "dialogue": "dialogue",
    "describe_prompt": "describe_prompts", "describe_prompts": "describe_prompts",
}

_CANONICAL_ENTITY: dict[str, str] = {
    "shots": "shot", "scenes": "scene",
    "dialogue": "dialogue", "describe_prompts": "describe_prompt",
}

_ENUM_FIELDS: dict[str, tuple[type, Any]] = {
    "scale": (ShotScale, ShotScale.MS),
    "angle": (CameraAngle, CameraAngle.EYE),
    "move": (CameraMove, CameraMove.STATIC),
    "int_ext": (IntExt, IntExt.EXT),
    "time_of_day": (TimeOfDay, TimeOfDay.DAY),
}

_TABLE_FIELDS: dict[str, frozenset[str]] = {
    "shots": frozenset({"scale", "subject", "angle", "move", "int_ext",
                        "location", "time_of_day", "action_text",
                        "characters", "mood"}),
    "scenes": frozenset({"int_ext", "location", "time_of_day",
                         "synopsis", "slugline"}),
    "dialogue": frozenset({"character", "text", "parenthetical"}),
    "describe_prompts": frozenset({"prompt"}),
}

_SLUG_RE = re.compile(r"^\s*(INT\.?\s*/\s*EXT\.?|I/E\.?|INT\.?|EXT\.?)\s+(.+)$",
                      re.IGNORECASE)


def _parse_slugline(text: str) -> tuple[IntExt, str, TimeOfDay]:
    """Parse 'INT. KITCHEN - NIGHT' back into heading fields."""
    body = str(text).strip()
    match = _SLUG_RE.match(body)
    prefix, rest = (match.group(1), match.group(2)) if match else ("EXT.", body)
    if re.sub(r"[^A-Z]", "", prefix.upper()) == "IE":
        prefix = "INT./EXT."
    if " - " in rest:
        location, _, time_part = rest.rpartition(" - ")
    elif "-" in rest:
        location, _, time_part = rest.rpartition("-")
    else:
        location, time_part = rest, ""
    int_ext = parse_enum(IntExt, prefix, IntExt.EXT)
    time_of_day = parse_enum(TimeOfDay, time_part.strip(), TimeOfDay.DAY)
    return int_ext, location.strip().upper(), time_of_day


def _split_characters(value: str) -> list[str]:
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _apply_correction(db: Database, table: str, entity_id: int,
                      field: str, human_value: str) -> bool:
    """Best-effort write of the corrected value onto the live projection row
    so the dashboard reflects the fix immediately."""
    if field not in _TABLE_FIELDS.get(table, frozenset()):
        return False
    row = db.row(f"SELECT * FROM {table} WHERE id = ?", (entity_id,), table)
    if row is None:
        return False
    if table == "scenes" and field == "slugline":
        int_ext, location, time_of_day = _parse_slugline(human_value)
        db.update(table, entity_id, int_ext=int_ext, location=location,
                  time_of_day=time_of_day)
        return True
    if table == "shots" and field == "characters":
        db.update(table, entity_id, characters=_split_characters(human_value))
        return True
    if table in ("shots", "scenes") and field in _ENUM_FIELDS:
        enum_cls, hard_default = _ENUM_FIELDS[field]
        current = parse_enum(enum_cls, row.get(field), hard_default)
        db.update(table, entity_id,
                  **{field: parse_enum(enum_cls, human_value, current)})
        return True
    db.update(table, entity_id, **{field: str(human_value)})
    return True


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def record_correction(db: Database, *, project_id: int, pass_id: int,
                      entity_type: str, entity_id: int, field: str,
                      model_value: str, human_value: str, note: str = "",
                      source: str = "human", scope: str = "project") -> int:
    """Persist one field-level correction and apply it to the live row."""
    if field not in CORRECTABLE_FIELDS:
        raise ValueError(
            f"uncorrectable field {field!r}; expected one of {CORRECTABLE_FIELDS}")
    table = _ENTITY_TABLES.get(str(entity_type).strip().lower())
    if table is None:
        raise ValueError(f"unknown entity_type {entity_type!r}")
    if scope not in ("project", "global"):
        raise ValueError(f"scope must be 'project' or 'global', got {scope!r}")
    _apply_correction(db, table, int(entity_id), field, str(human_value))
    return db.add_correction(
        project_id=int(project_id), pass_id=int(pass_id),
        entity_type=_CANONICAL_ENTITY[table], entity_id=int(entity_id),
        field=field, model_value=str(model_value),
        human_value=str(human_value), note=str(note),
        source=str(source), scope=scope,
    )


def _similarity(a: str, b: str) -> float:
    if _fuzz is not None:
        return float(_fuzz.token_set_ratio(a, b))
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() * 100.0


def _lesson_from_row(row: dict) -> Lesson:
    return Lesson(
        id=int(row["id"]), scope=row.get("scope") or "project",
        project_id=row.get("project_id"), field=row.get("field") or "*",
        rule=row.get("rule") or "", source_ids=list(row.get("source_ids") or []),
        weight=float(row.get("weight", 1.0)), active=bool(row.get("active", 1)),
        created_at=row.get("created_at") or "",
    )


def _correction_from_row(row: dict) -> Correction:
    return Correction(
        id=int(row["id"]), project_id=int(row["project_id"]),
        pass_id=int(row["pass_id"]), entity_type=row.get("entity_type") or "",
        entity_id=int(row["entity_id"]), field=row.get("field") or "",
        model_value=row.get("model_value") or "",
        human_value=row.get("human_value") or "", note=row.get("note") or "",
        source=row.get("source") or "human", scope=row.get("scope") or "project",
        created_at=row.get("created_at") or "",
    )


def recall(db: Database, *, project_id: int, field: str, query_text: str = "",
           k_lessons: int = config.RECALL_LESSONS,
           k_examples: int = config.RECALL_EXAMPLES) -> RecallBundle:
    """Assemble the lessons + example corrections a provider must obey."""
    rows = db.lessons(project_id=project_id, field=field, limit=max(k_lessons, 1))
    if field != "*":
        rows += db.lessons(project_id=project_id, field="*",
                           limit=max(k_lessons, 1))
    seen: set[int] = set()
    unique = [r for r in rows if r["id"] not in seen and not seen.add(r["id"])]
    unique.sort(key=lambda r: (-float(r.get("weight", 1.0)), -int(r["id"])))
    lessons = [_lesson_from_row(r) for r in unique[:max(k_lessons, 0)]]

    corr_rows = db.corrections(project_id=project_id, field=field,
                               limit=max(k_examples * 20, 50))
    if query_text.strip():
        corr_rows = sorted(
            corr_rows,
            key=lambda r: _similarity(
                query_text,
                f"{r.get('model_value', '')} {r.get('human_value', '')} "
                f"{r.get('note', '')}"),
            reverse=True,
        )
    examples = [_correction_from_row(r) for r in corr_rows[:max(k_examples, 0)]]
    return RecallBundle(lessons=lessons, examples=examples)


def _entity_value(table: str, row: dict, field: str) -> str | None:
    """Read the current value of a correctable field off a projection row."""
    if field not in _TABLE_FIELDS.get(table, frozenset()):
        return None
    if table == "scenes" and field == "slugline":
        return (f"{row.get('int_ext', '')} "
                f"{str(row.get('location', '')).upper()} - "
                f"{row.get('time_of_day', '')}")
    if table == "shots" and field == "characters":
        chars = row.get("characters") or []
        if isinstance(chars, str):
            return chars
        return ", ".join(str(c) for c in chars)
    value = row.get(field)
    return None if value is None else str(value)


def _values_agree(field: str, current: str, human: str) -> bool:
    if field == "characters":
        return ({p.lower() for p in _split_characters(current)}
                == {p.lower() for p in _split_characters(human)})
    return current.strip().lower() == str(human).strip().lower()


def agreement_metrics(db: Database, *, project_id: int, pass_id: int) -> dict:
    """Of the fields humans corrected on earlier passes of this project, how
    many does THIS pass now get right on its own? (Learning-curve headline.)

    Entities correspond across passes by shot idx, scene idx, dialogue order.
    """
    def empty() -> dict:
        return {"corrections_checked": 0, "now_agreeing": 0,
                "agreement_rate": None, "by_field": {}}

    current = db.get_pass(pass_id)
    if current is None:
        return empty()
    earlier_ids = {p["id"] for p in db.passes_for_project(project_id)
                   if int(p["number"]) < int(current["number"])}
    if not earlier_ids:
        return empty()
    prior = [c for c in db.corrections(project_id=project_id, limit=1000)
             if c["pass_id"] in earlier_ids]
    if not prior:
        return empty()

    shots_by_idx = {s["idx"]: s for s in db.shots_for_pass(pass_id)}
    scenes_by_idx = {s["idx"]: s for s in db.scenes_for_pass(pass_id)}
    cur_dialogue = db.dialogue_for_pass(pass_id)
    orig_dialogue: dict[int, list[dict]] = {}

    checked = agreeing = 0
    by_field: dict[str, dict[str, Any]] = {}
    for c in prior:
        table = _ENTITY_TABLES.get(str(c["entity_type"]).strip().lower())
        target: dict | None = None
        if table == "shots":
            orig = db.get_shot(int(c["entity_id"]))
            if orig is not None:
                target = shots_by_idx.get(orig["idx"])
        elif table == "scenes":
            orig = db.row("SELECT * FROM scenes WHERE id = ?",
                          (int(c["entity_id"]),), "scenes")
            if orig is not None:
                target = scenes_by_idx.get(orig["idx"])
        elif table == "dialogue":
            source_pass = int(c["pass_id"])
            if source_pass not in orig_dialogue:
                orig_dialogue[source_pass] = db.dialogue_for_pass(source_pass)
            order = next((i for i, r in enumerate(orig_dialogue[source_pass])
                          if r["id"] == c["entity_id"]), None)
            if order is not None and order < len(cur_dialogue):
                target = cur_dialogue[order]
        if target is None:
            continue
        value = _entity_value(table or "", target, c["field"])
        if value is None:
            continue
        agree = _values_agree(c["field"], value, c["human_value"])
        checked += 1
        agreeing += int(agree)
        slot = by_field.setdefault(
            c["field"], {"checked": 0, "agreeing": 0, "rate": None})
        slot["checked"] += 1
        slot["agreeing"] += int(agree)

    for slot in by_field.values():
        slot["rate"] = slot["agreeing"] / slot["checked"] if slot["checked"] else None
    return {
        "corrections_checked": checked,
        "now_agreeing": agreeing,
        "agreement_rate": (agreeing / checked) if checked else None,
        "by_field": by_field,
    }
