"""Distill accumulated corrections into short imperative lessons.

A lesson is a rule the vision/describe passes must obey on every future
pass ("When trees line both sides of a visible road, label the location
WOODED ROAD, not WOODS"). Lessons are stored in the global database so
they transfer across films when scoped 'global'.
"""
from __future__ import annotations

import re

from scripty.core.db import Database
from scripty.core.interfaces import TextBrain

DISTILL_SYSTEM: str = (
    "You are the distilled memory of a veteran script supervisor. You are "
    "given field-by-field corrections a human supervisor made to a "
    "machine-generated shot log, screenplay, or describe prompt: for each, "
    "the model wrote one value and the human replaced it with another. "
    "Write 1-3 short imperative rules the machine should follow on future "
    "passes so the same mistakes are not repeated. Rules must be concrete, "
    "generalizable, and phrased as commands, e.g. \"When trees line both "
    "sides of a visible road, label the location WOODED ROAD, not WOODS\". "
    "Answer with one rule per line. No numbering, no bullets, no commentary."
)

_FALLBACK_RULE = "Prefer '{human}' over '{model}' when similar context recurs."

_RULE_PREFIX = re.compile(r"^[\s\-\*•>]*(?:\d+[.)]\s*)?")


def _cited_ids(db: Database) -> set[int]:
    """Correction ids already cited by ANY lesson (active or not)."""
    cited: set[int] = set()
    for row in db.rows("SELECT source_ids FROM lessons", (), "lessons"):
        for cid in row.get("source_ids") or []:
            try:
                cited.add(int(cid))
            except (TypeError, ValueError):
                continue
    return cited


def _format_group(field: str, corrections: list[dict]) -> str:
    lines = [f"Field being corrected: {field}", "",
             "Corrections (model value -> human value):"]
    for c in corrections:
        note = f" note: {c['note']}" if c.get("note") else ""
        lines.append(f"- model said {str(c.get('model_value', ''))!r}; human "
                     f"corrected to {str(c.get('human_value', ''))!r}.{note}")
    lines += ["", "Write 1-3 short imperative rules, one per line."]
    return "\n".join(lines)


def _parse_rules(text: str) -> list[str]:
    rules: list[str] = []
    for line in str(text or "").splitlines():
        cleaned = _RULE_PREFIX.sub("", line).strip().strip('"').strip()
        if not cleaned or cleaned.endswith(":"):
            continue
        rules.append(cleaned)
    return rules[:3]


def _brain_rules(brain: TextBrain | None, field: str,
                 group: list[dict]) -> list[str]:
    if brain is None:
        return []
    user = _format_group(field, group)
    try:
        rules = _parse_rules(brain.complete(DISTILL_SYSTEM, user))
    except Exception:  # never let a flaky brain block distillation
        return []
    # Reject lines that merely echo the prompt (e.g. MockBrain's summary of
    # the user text) — those are not rules, and storing them would inject
    # prompt junk into every future recall. Falling through to the
    # deterministic fallback is strictly better.
    prompt_norm = " ".join(user.split()).lower()
    return [r for r in rules
            if " ".join(r.split()).lower() not in prompt_norm]


def _lesson_scope(group: list[dict],
                  project_id: int | None) -> tuple[str, int | None]:
    if any(str(c.get("scope", "project")) == "global" for c in group):
        return "global", None
    pid = project_id if project_id is not None else group[0].get("project_id")
    return "project", pid


def _fallback_lessons(db: Database, field: str, group: list[dict],
                      project_id: int | None) -> list[int]:
    """Deterministic distillation: one lesson per distinct value pair."""
    pairs: dict[tuple[str, str], list[dict]] = {}
    for c in group:
        key = (str(c.get("model_value", "")).strip(),
               str(c.get("human_value", "")).strip())
        pairs.setdefault(key, []).append(c)
    ids: list[int] = []
    for (model, human), members in pairs.items():
        scope, pid = _lesson_scope(members, project_id)
        ids.append(db.add_lesson(
            scope=scope, project_id=pid, field=field,
            rule=_FALLBACK_RULE.format(human=human, model=model),
            source_ids=[int(m["id"]) for m in members]))
    return ids


def distill(db: Database, brain: TextBrain | None,
            project_id: int | None = None,
            min_corrections: int = 1) -> list[int]:
    """Group fresh (never-yet-cited) corrections by field and turn each group
    into 1-3 stored lessons. Returns the new lesson ids."""
    cited = _cited_ids(db)
    fresh = [c for c in db.corrections(project_id=project_id, limit=1000)
             if int(c["id"]) not in cited]
    # Key groups by (project, field) so a no-filter distill never blends
    # corrections from different films into one misattributed lesson.
    groups: dict[tuple[int, str], list[dict]] = {}
    for c in fresh:
        key = (int(c.get("project_id") or 0), str(c.get("field", "")))
        groups.setdefault(key, []).append(c)

    new_ids: list[int] = []
    for (_, field), group in sorted(groups.items()):
        if not field or len(group) < max(min_corrections, 1):
            continue
        group = sorted(group, key=lambda c: int(c["id"]))
        rules = _brain_rules(brain, field, group)
        if rules:
            scope, pid = _lesson_scope(group, project_id)
            source_ids = [int(c["id"]) for c in group]
            for rule in rules:
                new_ids.append(db.add_lesson(
                    scope=scope, project_id=pid, field=field,
                    rule=rule, source_ids=source_ids))
        else:
            new_ids.extend(_fallback_lessons(db, field, group, project_id))
    return new_ids
