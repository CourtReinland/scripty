"""Shot-labelling taxonomy, vision-prompt construction, and defensive parsing.

The taxonomy follows working script-supervisor convention: every shot is
labelled ``<SCALE> <SUBJECT>`` (e.g. ``MS CAMP FIRE``) and every scene heading
is ``<INT/EXT>. <LOCATION> - <TIME>``.
"""
from __future__ import annotations

import json
from typing import Any

from scripty.core.models import (
    CameraAngle,
    CameraMove,
    CharacterSighting,
    IntExt,
    RecallBundle,
    ShotAnalysis,
    ShotContext,
    ShotScale,
    TimeOfDay,
    parse_enum,
    timecode,
)

SHOT_SCALE_GUIDE = """SHOT SCALE & LABEL CONVENTIONS (working script-supervisor standard)

Every shot is labelled "<SCALE> <SUBJECT>", e.g. "MS CAMP FIRE" or
"CU WINDOW PANE". SUBJECT is a terse capitalized noun phrase, never a sentence.

Scales, tightest to widest:
- ECU (extreme close-up): a single detail fills the frame — an eye, a trigger, a ring.
- CU (close-up): a face or object from roughly the shoulders up.
- MCU (medium close-up): chest up; the standard dialogue single.
- MS (medium shot): waist up; gesture and body language read clearly.
- MWS (medium-wide): knees up (the "cowboy"); actor plus some environment.
- WS (wide shot): full figure(s) with the surrounding space.
- EWS (extreme wide): landscape scale; people are small in the frame.
- EST (establishing): a wide that introduces a new location, usually scene-opening.

Special framings (use INSTEAD of a plain scale when they apply):
- OTS (over-the-shoulder): camera looks past one character's shoulder at another.
- POV (point of view): the camera IS a character's eyes.
- INSERT: a cut-in detail of an object that matters to the action (a note, a dial).
- 2-SHOT: two characters share the frame with equal weight.
- AERIAL: shot from far above (drone / helicopter).

Angles: EYE LEVEL, HIGH ANGLE, LOW ANGLE, DUTCH, OVERHEAD, WORM'S EYE.
Moves: STATIC, PAN, TILT, DOLLY, TRACKING, ZOOM, PUSH IN, PULL OUT,
HANDHELD, STEADICAM, CRANE, WHIP PAN.
Sluglines: INT. or EXT. (or INT./EXT.), LOCATION in caps, then time of day:
DAY, NIGHT, DAWN, DUSK, MORNING, EVENING, CONTINUOUS, LATER.
"""


def _enum_values(cls: type) -> str:
    return ", ".join(member.value for member in cls)


def build_shot_prompt(ctx: ShotContext, recall: RecallBundle) -> str:
    """Compose the user prompt for one shot's keyframe analysis.

    Includes the taxonomy guide, film context, previous-shot summaries, the
    transcript excerpt, and the recall bundle (which must outrank instinct).
    """
    duration = max(ctx.end_s - ctx.start_s, 0.0)
    lines: list[str] = [
        "You are logging ONE shot of a film from the attached keyframes "
        "(sampled early / middle / late within the shot).",
        "",
        SHOT_SCALE_GUIDE.strip(),
        "",
        "FILM CONTEXT:",
        f"- Title: {ctx.film_title or 'UNTITLED'}",
        f"- Pass number: {ctx.pass_number}",
        f"- Shot {ctx.shot_index + 1} of {ctx.total_shots}",
        f"- Runs {timecode(ctx.start_s)} to {timecode(ctx.end_s)} "
        f"({duration:.1f}s)",
    ]
    if ctx.prev_summaries:
        lines += ["", "PREVIOUS SHOTS (most recent last):"]
        lines += [f"- {summary}" for summary in ctx.prev_summaries]
    if ctx.transcript_excerpt:
        lines += ["", "TRANSCRIPT EXCERPT (audio heard during this shot):",
                  ctx.transcript_excerpt.strip()]
    notes = recall.format_for_prompt()
    if notes:
        lines += ["", notes, "",
                  "The supervisor notes above OUTRANK your own first "
                  "impression: when a rule or prior correction applies to "
                  "what you see, follow it even if your instinct disagrees."]
    lines += [
        "",
        "Answer with a single JSON object and nothing else. Keys:",
        f'- "scale": one of {_enum_values(ShotScale)}',
        '- "subject": short capitalized noun phrase for the shot label',
        f'- "angle": one of {_enum_values(CameraAngle)}',
        f'- "move": one of {_enum_values(CameraMove)}',
        f'- "int_ext": one of {_enum_values(IntExt)}',
        '- "location": capitalized location name for the slugline',
        f'- "time_of_day": one of {_enum_values(TimeOfDay)}',
        '- "action_text": 1-3 present-tense screenplay action lines for this shot',
        '- "characters": array of {"name": ..., "description": ...} for each '
        'visible person (name unknowns like "MAN IN RED JACKET"; description '
        'is appearance notes for continuity)',
        '- "mood": lighting / tone notes',
        '- "confidence": your 0.0-1.0 self-assessment',
    ]
    return "\n".join(lines)


def _extract_json_object(text: str) -> dict | None:
    """Find and parse the first JSON object embedded in ``text``."""
    if not text:
        return None
    start = text.find("{")
    if start == -1:
        return None
    end = text.rfind("}")
    if end > start:
        try:
            data = json.loads(text[start:end + 1])
            if isinstance(data, dict):
                return data
        except ValueError:
            pass
    depth = 0  # fallback: first balanced-brace span
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    data = json.loads(text[start:i + 1])
                    return data if isinstance(data, dict) else None
                except ValueError:
                    return None
    return None


def _clean_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


def _parse_characters(value: Any) -> list[CharacterSighting]:
    if isinstance(value, str):
        value = [part for part in value.split(",")]
    if not isinstance(value, list):
        return []
    sightings: list[CharacterSighting] = []
    for item in value:
        if isinstance(item, dict):
            name = _clean_str(item.get("name"))
            description = _clean_str(item.get("description"))
        else:
            name, description = _clean_str(item), ""
        if name:
            sightings.append(CharacterSighting(name=name, description=description))
    return sightings


def _parse_confidence(value: Any) -> float:
    try:
        return min(max(float(value), 0.0), 1.0)
    except (TypeError, ValueError):
        return 0.5


def parse_shot_json(text: str) -> ShotAnalysis:
    """Defensively parse a model reply into a ShotAnalysis.

    Extracts the first ``{...}`` span, coerces every enum field through
    :func:`scripty.core.models.parse_enum`, and degrades (confidence 0.1,
    error recorded in ``raw``) instead of raising on garbage.
    """
    data = _extract_json_object(text or "")
    if data is None:
        return ShotAnalysis(confidence=0.1, raw={
            "error": "no parseable JSON object in model reply",
            "text": (text or "")[:1000],
        })
    return ShotAnalysis(
        scale=parse_enum(ShotScale, data.get("scale"), ShotScale.MS),
        subject=_clean_str(data.get("subject")),
        angle=parse_enum(CameraAngle, data.get("angle"), CameraAngle.EYE),
        move=parse_enum(CameraMove, data.get("move"), CameraMove.STATIC),
        int_ext=parse_enum(IntExt, data.get("int_ext"), IntExt.EXT),
        location=_clean_str(data.get("location")),
        time_of_day=parse_enum(TimeOfDay, data.get("time_of_day"), TimeOfDay.DAY),
        action_text=_clean_str(data.get("action_text")),
        characters=_parse_characters(data.get("characters")),
        mood=_clean_str(data.get("mood")),
        confidence=_parse_confidence(data.get("confidence")),
        raw=data,
    )
