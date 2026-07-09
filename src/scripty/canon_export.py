"""Export a Scripty pass as a director-bot / canon work bundle JSON.

This is the pipe from labeling → decision corpus. The JSON can be imported
with `director-bot canon import <file>`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from scripty.core.db import Database


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "untitled"


def build_canon_export(
    db: Database,
    project_id: int,
    pass_id: Optional[int] = None,
    *,
    tier: str = "UNRANKED",
    directors: Optional[list[str]] = None,
    genres: Optional[list[str]] = None,
    theme: str = "",
    logline: str = "",
    plot_summary: str = "",
) -> dict[str, Any]:
    """Build a director-bot-compatible bundle (work + cards + shots + digests).

    Also includes a raw envelope (`project`, `pass`, `shots`, …) so tools that
    prefer Scripty-native rows can re-derive fields.
    """
    project = db.get_project(project_id)
    if project is None:
        raise ValueError(f"no such project: {project_id}")

    if pass_id is None:
        passes = db.passes_for_project(project_id)
        complete = [p for p in passes if p.get("status") == "complete"]
        if not complete:
            raise ValueError(f"no complete pass for project {project_id}")
        pass_row = complete[-1]
        pass_id = int(pass_row["id"])
    else:
        pass_row = db.get_pass(pass_id)
        if pass_row is None:
            raise ValueError(f"no such pass: {pass_id}")
        if int(pass_row["project_id"]) != int(project_id):
            raise ValueError(
                f"pass {pass_id} does not belong to project {project_id}"
            )

    shots = db.shots_for_pass(int(pass_id))
    scenes = db.scenes_for_pass(int(pass_id))
    dialogue = db.dialogue_for_pass(int(pass_id))

    # --- map dialogue ---
    dlg_by_shot: dict[Any, list[dict[str, str]]] = {}
    for line in dialogue:
        dlg_by_shot.setdefault(line.get("shot_id"), []).append({
            "character": str(line.get("character") or ""),
            "text": str(line.get("text") or ""),
            "parenthetical": str(line.get("parenthetical") or ""),
        })

    shot_id_to_scene_idx: dict[Any, int] = {}
    for sc in scenes:
        for sid in sc.get("shot_ids") or []:
            shot_id_to_scene_idx[sid] = int(sc.get("idx", 0))

    scene_cards: list[dict[str, Any]] = []
    for sc in scenes:
        slugline = (
            f"{sc.get('int_ext', 'EXT.')} "
            f"{str(sc.get('location') or 'UNKNOWN').upper()} - "
            f"{sc.get('time_of_day', 'DAY')}"
        )
        chars: list[str] = []
        for sh in shots:
            if sh.get("id") in (sc.get("shot_ids") or []):
                for c in sh.get("characters") or []:
                    name = c if isinstance(c, str) else str(c)
                    if name and name not in chars:
                        chars.append(name)
        scene_cards.append({
            "idx": int(sc.get("idx", 0)),
            "slugline": slugline,
            "title": str(sc.get("location") or f"Scene {sc.get('idx', 0)}"),
            "what_happens": str(sc.get("synopsis") or ""),
            "relationship_delta": "",
            "plot_function": "",
            "emotional_spine": "",
            "characters": chars,
            "structural_beat": "",
            "act": None,
            "tags": [],
            "meta": {"scripty_scene_id": sc.get("id")},
        })

    shot_moments: list[dict[str, Any]] = []
    digests: list[dict[str, Any]] = []
    for sh in shots:
        chars = sh.get("characters") or []
        char_names = [
            c if isinstance(c, str) else str(c.get("name", c) if isinstance(c, dict) else c)
            for c in chars
        ]
        scene_idx = shot_id_to_scene_idx.get(sh.get("id"))
        moment = {
            "idx": int(sh.get("idx", 0)),
            "scene_idx": scene_idx,
            "start_s": float(sh.get("start_s", 0)),
            "end_s": float(sh.get("end_s", 0)),
            "scale": sh.get("scale") or "MS",
            "subject": sh.get("subject") or "",
            "angle": sh.get("angle") or "EYE LEVEL",
            "move": sh.get("move") or "STATIC",
            "int_ext": sh.get("int_ext") or "EXT.",
            "location": sh.get("location") or "",
            "time_of_day": sh.get("time_of_day") or "DAY",
            "action_text": sh.get("action_text") or "",
            "characters": char_names,
            "mood": sh.get("mood") or "",
            "dialogue": dlg_by_shot.get(sh.get("id"), []),
            "confidence": float(sh.get("confidence", 0.5)),
            "keyframes": sh.get("keyframes") or [],
            "meta": {"scripty_shot_id": sh.get("id")},
        }
        shot_moments.append(moment)
        digests.append({
            "shot_idx": int(sh.get("idx", 0)),
            "scene_idx": scene_idx,
            "situation": (
                f"{moment['int_ext']} {moment['location']} - {moment['time_of_day']}: "
                f"{moment['action_text'] or moment['subject']}"
            ),
            "decision": (
                f"Shot as {moment['scale']} {moment['subject']} "
                f"({moment['angle']}, {moment['move']})"
            ),
            "rationale": moment["mood"] or "Labeled by Scripty supervision pass.",
            "director": (directors or [""])[0] if directors else "",
            "tags": [str(moment["scale"]), str(moment["move"])],
            "phase": "shotlist",
        })

    title = str(project.get("name") or project.get("slug") or "untitled")
    slug = _slugify(str(project.get("slug") or title))

    work = {
        "slug": slug,
        "title": title,
        "year": None,
        "directors": list(directors or []),
        "genres": list(genres or []),
        "medium": "film",
        "tier": tier,
        "theme": theme,
        "logline": logline,
        "plot_summary": plot_summary,
        "source": (
            f"scripty:project:{project_id}:pass:{pass_id}"
            f":n{pass_row.get('number')}"
        ),
        "meta": {
            "scripty_project_id": project_id,
            "scripty_pass_id": pass_id,
            "video_path": project.get("video_path"),
        },
    }

    return {
        # director-bot import shape
        "work": work,
        "scene_cards": scene_cards,
        "shot_moments": shot_moments,
        "decision_digests": digests,
        # raw envelope for re-processing
        "project": dict(project),
        "pass": dict(pass_row),
        "shots": shots,
        "scenes": scenes,
        "dialogue": dialogue,
        "tier": tier,
        "directors": list(directors or []),
        "genres": list(genres or []),
        "theme": theme,
        "logline": logline,
        "plot_summary": plot_summary,
    }


def write_canon_export(bundle: dict[str, Any], path: Path | str) -> Path:
    p = Path(path).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(bundle, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    return p
