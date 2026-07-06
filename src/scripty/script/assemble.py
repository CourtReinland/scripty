"""Shot-to-scene grouping and the classic script-supervisor cut log.

Operates on plain db dicts (as returned by `Database.shots_for_pass`)
so the pipeline and the dashboard server can share it directly.
"""
from __future__ import annotations

from typing import Any

from scripty.core.models import timecode


def _shot_key(shot: dict) -> Any:
    """Stable identifier for a shot dict: db id when present, else idx."""
    sid = shot.get("id")
    return sid if sid is not None else shot.get("idx")


def _scene_dict(idx: int, int_ext: str, location: str, time_of_day: str) -> dict:
    return {
        "idx": idx,
        "int_ext": int_ext,
        "location": location,
        "time_of_day": time_of_day,
        "shot_ids": [],
        "synopsis": "",
    }


def group_scenes(shots: list[dict]) -> list[dict]:
    """Group ordered shot dicts into scenes.

    A new scene starts whenever the (int_ext, location, time_of_day)
    triple changes. Location comparison is case-insensitive. Shots with
    an empty location never open a scene boundary: they inherit the
    current scene (and, if the current scene has no location yet, the
    first located shot donates its heading to it).

    Returns scene dicts WITHOUT pass_id (the pipeline adds it): keys
    idx, int_ext, location, time_of_day, shot_ids, synopsis.
    """
    scenes: list[dict] = []
    current: dict | None = None
    for shot in shots:
        location = str(shot.get("location") or "").strip()
        int_ext = str(shot.get("int_ext") or "EXT.").strip()
        tod = str(shot.get("time_of_day") or "DAY").strip()
        if current is None:
            current = _scene_dict(len(scenes), int_ext, location, tod)
            scenes.append(current)
        elif location:
            if not current["location"]:
                current["int_ext"] = int_ext
                current["location"] = location
                current["time_of_day"] = tod
            elif (
                (int_ext.upper(), location.upper(), tod.upper())
                != (current["int_ext"].upper(), current["location"].upper(),
                    current["time_of_day"].upper())
            ):
                current = _scene_dict(len(scenes), int_ext, location, tod)
                scenes.append(current)
        current["shot_ids"].append(_shot_key(shot))
        if not current["synopsis"]:
            action = str(shot.get("action_text") or "").strip()
            if action:
                current["synopsis"] = action[:200]
    return scenes


def cut_log(shots: list[dict], fps: float = 24.0) -> str:
    """Classic cut log: one line per shot.

    Example line: ``0001  00:00:00:00 - 00:00:03:12  MS CAMP FIRE``
    """
    lines: list[str] = []
    for pos, shot in enumerate(shots):
        idx = int(shot.get("idx", pos))
        start = float(shot.get("start_s") or 0.0)
        end = float(shot.get("end_s") or 0.0)
        scale = str(shot.get("scale") or "MS").strip() or "MS"
        subject = (str(shot.get("subject") or "").strip() or "UNKNOWN").upper()
        lines.append(
            f"{idx + 1:04d}  {timecode(start, fps)} - "
            f"{timecode(end, fps)}  {scale} {subject}"
        )
    return "\n".join(lines)
