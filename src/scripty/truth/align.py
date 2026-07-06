"""Ground-truth mode: link a known script, align it to a pass, auto-correct.

`link_script` parses a known (ground-truth) screenplay and stores it against
the project. `align_pass` runs a Needleman-Wunsch alignment between the
scenes Scripty generated for a pass and the truth scenes, plus a fuzzy
dialogue match, and stores the report. `emit_auto_corrections` turns scene
heading disagreements into `source='ground_truth'` corrections so the
learning loop can consume them exactly like human dashboard edits.
"""
from __future__ import annotations

import math
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from scripty.core.db import Database
from scripty.core.models import ArtifactKind, IntExt, SceneHeading, TimeOfDay, parse_enum

_HEADING_FIELDS = ("int_ext", "location", "time_of_day")
_GAP = -1.0
_DIALOGUE_HIT = 70.0


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def link_script(db: Database, *, project_id: int, script_path: Path) -> int:
    """Attach a known (ground-truth) script to a project.

    Reads the file (fountain or plain text), parses it via
    ``scripty.script.fountain.parse_fountain``, stores the parsed structure
    in ``truth_links`` and registers a KNOWN_SCRIPT artifact. Returns the
    new truth-link id.
    """
    from scripty.script.fountain import parse_fountain  # built in parallel

    project = db.get_project(project_id)
    if project is None:
        raise ValueError(f"unknown project id: {project_id}")

    path = Path(script_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"script not found: {path}")
    if not path.is_file():
        raise ValueError(f"script path is not a file: {path}")

    text = path.read_text(encoding="utf-8", errors="replace")
    parsed = _parsed_to_dict(parse_fountain(text))
    truth_id = db.add_truth(project_id=project_id, script_path=str(path),
                            parsed=parsed)
    db.add_artifact(project_id=project_id,
                    kind=ArtifactKind.KNOWN_SCRIPT.value, ref=str(path))
    return truth_id


def align_pass(db: Database, *, pass_id: int) -> dict:
    """Align one pass against the project's linked ground-truth script.

    Scene sequences are aligned with Needleman-Wunsch (gap = -1) using
    similarity = 0.5*fuzz(location) + 0.25*(int_ext match) + 0.25*(time
    match). Dialogue is matched line-by-line with partial_ratio (> 70 is a
    hit). The report is stored via ``db.add_alignment`` and returned.
    """
    pass_row = db.get_pass(pass_id)
    if pass_row is None:
        raise ValueError(f"unknown pass id: {pass_id}")
    project_id = int(pass_row["project_id"])
    truth = db.truth_for_project(project_id)
    if truth is None:
        raise ValueError(f"no ground-truth script linked to project {project_id}")

    parsed = truth.get("parsed") or {}
    truth_scenes: list[dict] = list(parsed.get("scenes") or [])
    model_scenes = db.scenes_for_pass(pass_id)

    pairs, gap_model, gap_truth = _needleman_wunsch(model_scenes, truth_scenes)

    scene_pairs: list[dict] = []
    exact = 0
    for mi, ti, score in pairs:
        mscene, tscene = model_scenes[mi], truth_scenes[ti]
        diffs = _heading_diffs(mscene, tscene)
        if not diffs:
            exact += 1
        scene_pairs.append({
            "model_idx": int(mscene.get("idx", mi)),
            "truth_idx": int(tscene.get("idx", ti)),
            "score": round(float(score), 4),
            "diffs": diffs,
        })

    report = {
        "scene_pairs": scene_pairs,
        "unmatched_model": [int(model_scenes[i].get("idx", i)) for i in gap_model],
        "unmatched_truth": [int(truth_scenes[j].get("idx", j)) for j in gap_truth],
        "dialogue": _dialogue_report(truth_scenes, db.dialogue_for_pass(pass_id)),
        "slugline_accuracy": (exact / len(scene_pairs)) if scene_pairs else 0.0,
    }
    db.add_alignment(pass_id=pass_id, truth_id=int(truth["id"]), report=report)
    return report


def emit_auto_corrections(db: Database, *, pass_id: int, report: dict) -> int:
    """Turn scene diffs from an alignment report into ground-truth corrections.

    One heading field wrong -> a correction on that specific field; two or
    more wrong -> a single 'slugline' correction carrying the full heading.
    Returns the number of corrections recorded.
    """
    from scripty.learn.memory import record_correction  # built in parallel

    pass_row = db.get_pass(pass_id)
    if pass_row is None:
        raise ValueError(f"unknown pass id: {pass_id}")
    project_id = int(pass_row["project_id"])
    scenes_by_idx = {int(s["idx"]): s for s in db.scenes_for_pass(pass_id)}

    count = 0
    for pair in report.get("scene_pairs") or []:
        diffs = dict(pair.get("diffs") or {})
        if not diffs:
            continue
        scene = scenes_by_idx.get(int(pair.get("model_idx", -1)))
        if scene is None:
            continue
        note = f"auto-emitted from ground-truth alignment (truth scene {pair.get('truth_idx')})"
        heading_diffs = {f: v for f, v in diffs.items() if f in _HEADING_FIELDS}
        if len(heading_diffs) >= 2:
            merged = {f: (heading_diffs[f]["truth"] if f in heading_diffs
                          else scene.get(f))
                      for f in _HEADING_FIELDS}
            record_correction(
                db, project_id=project_id, pass_id=pass_id,
                entity_type="scene", entity_id=int(scene["id"]),
                field="slugline",
                model_value=_slugline(scene.get("int_ext"),
                                      scene.get("location"),
                                      scene.get("time_of_day")),
                human_value=_slugline(merged["int_ext"], merged["location"],
                                      merged["time_of_day"]),
                note=note, source="ground_truth",
            )
            count += 1
        else:
            for field, vals in heading_diffs.items():
                record_correction(
                    db, project_id=project_id, pass_id=pass_id,
                    entity_type="scene", entity_id=int(scene["id"]),
                    field=field,
                    model_value=str(vals.get("model", "")),
                    human_value=str(vals.get("truth", "")),
                    note=note, source="ground_truth",
                )
                count += 1
    return count


# --------------------------------------------------------------------------- #
# Parsed-script normalization
# --------------------------------------------------------------------------- #

def _parsed_to_dict(parsed: Any) -> dict:
    """Coerce a ParsedScript (dataclass), dict, or duck-typed object to a dict."""
    if is_dataclass(parsed) and not isinstance(parsed, type):
        return asdict(parsed)
    if isinstance(parsed, dict):
        return parsed
    scenes: list[dict] = []
    for sc in getattr(parsed, "scenes", None) or []:
        if is_dataclass(sc) and not isinstance(sc, type):
            scenes.append(asdict(sc))
        elif isinstance(sc, dict):
            scenes.append(sc)
        else:
            scenes.append({
                "idx": int(getattr(sc, "idx", len(scenes))),
                "int_ext": str(getattr(sc, "int_ext", "") or ""),
                "location": str(getattr(sc, "location", "") or ""),
                "time_of_day": str(getattr(sc, "time_of_day", "") or ""),
                "action": list(getattr(sc, "action", None) or []),
                "dialogue": [list(d) for d in getattr(sc, "dialogue", None) or []],
            })
    return {"title": str(getattr(parsed, "title", "") or ""), "scenes": scenes}


# --------------------------------------------------------------------------- #
# Scene alignment (Needleman-Wunsch)
# --------------------------------------------------------------------------- #

def _canon(cls: type, value: Any) -> str:
    """Canonical string for an enum-ish field ('INT' -> 'INT.'); raw upper otherwise."""
    member = parse_enum(cls, value, None)  # type: ignore[arg-type]
    if member is not None:
        return str(member.value)
    return str(value or "").strip().upper()


def _values_equal(field: str, model_value: str, truth_value: str) -> bool:
    if field == "int_ext":
        return _canon(IntExt, model_value) == _canon(IntExt, truth_value)
    if field == "time_of_day":
        return _canon(TimeOfDay, model_value) == _canon(TimeOfDay, truth_value)
    return model_value.strip().upper() == truth_value.strip().upper()


def _heading_diffs(model: dict, truth: dict) -> dict:
    diffs: dict[str, dict[str, str]] = {}
    for field in _HEADING_FIELDS:
        mv = str(model.get(field) or "").strip()
        tv = str(truth.get(field) or "").strip()
        if not _values_equal(field, mv, tv):
            diffs[field] = {"model": mv, "truth": tv}
    return diffs


def _scene_similarity(model: dict, truth: dict) -> float:
    loc_m = str(model.get("location") or "").strip().upper()
    loc_t = str(truth.get("location") or "").strip().upper()
    loc = fuzz.ratio(loc_m, loc_t) / 100.0
    ie = 1.0 if _values_equal("int_ext", str(model.get("int_ext") or ""),
                              str(truth.get("int_ext") or "")) else 0.0
    tod = 1.0 if _values_equal("time_of_day", str(model.get("time_of_day") or ""),
                               str(truth.get("time_of_day") or "")) else 0.0
    return 0.5 * loc + 0.25 * ie + 0.25 * tod


def _needleman_wunsch(
    model: list[dict], truth: list[dict], gap: float = _GAP,
) -> tuple[list[tuple[int, int, float]], list[int], list[int]]:
    """Global alignment. Returns (pairs [(mi, ti, sim)], gapped model, gapped truth)."""
    n, m = len(model), len(truth)
    sims = [[_scene_similarity(model[i], truth[j]) for j in range(m)]
            for i in range(n)]
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        score[i][0] = score[i - 1][0] + gap
    for j in range(1, m + 1):
        score[0][j] = score[0][j - 1] + gap
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            score[i][j] = max(score[i - 1][j - 1] + sims[i - 1][j - 1],
                              score[i - 1][j] + gap,
                              score[i][j - 1] + gap)

    pairs: list[tuple[int, int, float]] = []
    gap_model: list[int] = []
    gap_truth: list[int] = []
    i, j = n, m
    while i > 0 and j > 0:
        if math.isclose(score[i][j], score[i - 1][j - 1] + sims[i - 1][j - 1],
                        abs_tol=1e-9):
            pairs.append((i - 1, j - 1, sims[i - 1][j - 1]))
            i, j = i - 1, j - 1
        elif math.isclose(score[i][j], score[i - 1][j] + gap, abs_tol=1e-9):
            gap_model.append(i - 1)
            i -= 1
        else:
            gap_truth.append(j - 1)
            j -= 1
    while i > 0:
        gap_model.append(i - 1)
        i -= 1
    while j > 0:
        gap_truth.append(j - 1)
        j -= 1
    pairs.reverse()
    gap_model.reverse()
    gap_truth.reverse()
    return pairs, gap_model, gap_truth


# --------------------------------------------------------------------------- #
# Dialogue alignment
# --------------------------------------------------------------------------- #

def _dialogue_entry(entry: Any) -> tuple[str, str]:
    """Normalize a truth dialogue entry ((char, text) pair or dict) to strings."""
    if isinstance(entry, dict):
        return (str(entry.get("character") or ""), str(entry.get("text") or ""))
    if isinstance(entry, (list, tuple)) and len(entry) >= 2:
        return (str(entry[0] or ""), str(entry[1] or ""))
    return ("", str(entry or ""))


def _norm_character(name: str) -> str:
    """Uppercase, strip a trailing extension like (O.S.)/(V.O.)/(CONT'D)."""
    text = str(name or "").strip().upper()
    while text.endswith(")") and "(" in text:
        text = text[: text.rfind("(")].strip()
    return text


def _dialogue_report(truth_scenes: list[dict], model_dialogue: list[dict]) -> dict:
    truth_lines: list[tuple[str, str]] = []
    for scene in truth_scenes:
        for entry in scene.get("dialogue") or []:
            char, text = _dialogue_entry(entry)
            if text.strip():
                truth_lines.append((char, text))
    model_lines = [(str(d.get("character") or ""), str(d.get("text") or ""))
                   for d in model_dialogue if str(d.get("text") or "").strip()]

    matched = 0
    char_hits = 0
    best_scores: list[float] = []
    for char, text in truth_lines:
        best_score, best_char = 0.0, ""
        for mchar, mtext in model_lines:
            s = float(fuzz.partial_ratio(text, mtext))
            if s > best_score:
                best_score, best_char = s, mchar
        best_scores.append(best_score)
        if best_score > _DIALOGUE_HIT:
            matched += 1
            if _norm_character(best_char) == _norm_character(char):
                char_hits += 1

    n = len(truth_lines)
    return {
        "truth_lines": n,
        "matched": matched,
        "avg_score": round(sum(best_scores) / n, 2) if n else 0.0,
        "character_accuracy": round(char_hits / matched, 4) if matched else None,
    }


# --------------------------------------------------------------------------- #
# Slugline rendering
# --------------------------------------------------------------------------- #

def _slugline(int_ext: Any, location: Any, time_of_day: Any) -> str:
    heading = SceneHeading(
        int_ext=parse_enum(IntExt, int_ext, IntExt.EXT),
        location=str(location or "").strip() or "UNKNOWN",
        time_of_day=parse_enum(TimeOfDay, time_of_day, TimeOfDay.DAY),
    )
    return heading.slugline()
