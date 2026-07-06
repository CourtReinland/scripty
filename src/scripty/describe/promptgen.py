"""Describe-track prompt generation.

Turns the shot/scene analysis of a pass into a parallel track of gen-AI
video prompts: one prompt per shot plus one umbrella prompt per scene,
threaded with a continuity block (characters, locations, global style) so
a generative video model keeps wardrobe/lighting/geography consistent.

Deterministic by default; optionally polished through a TextBrain, but a
brain failure NEVER fails the pass (falls back to the template).
"""
from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any

from scripty.core.models import (
    CameraAngle,
    CameraMove,
    IntExt,
    ShotScale,
    TimeOfDay,
    parse_enum,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from scripty.core.interfaces import TextBrain


# --------------------------------------------------------------------------- #
# Vocabulary: canonical enum -> natural-language phrasing for gen-AI prompts
# --------------------------------------------------------------------------- #

FRAMING_WORDS: dict[ShotScale, str] = {
    ShotScale.ECU: "Extreme close-up",
    ShotScale.CU: "Close-up",
    ShotScale.MCU: "Medium close-up",
    ShotScale.MS: "Medium shot",
    ShotScale.MWS: "Medium-wide shot",
    ShotScale.WS: "Wide shot",
    ShotScale.EWS: "Extreme wide shot",
    ShotScale.EST: "Establishing shot",
    ShotScale.OTS: "Over-the-shoulder shot",
    ShotScale.POV: "Point-of-view shot",
    ShotScale.INSERT: "Insert detail shot",
    ShotScale.TWO_SHOT: "Two-shot",
    ShotScale.AERIAL: "Aerial shot",
}

ANGLE_WORDS: dict[CameraAngle, str] = {
    CameraAngle.EYE: "at eye level",
    CameraAngle.HIGH: "from a high angle",
    CameraAngle.LOW: "from a low angle",
    CameraAngle.DUTCH: "with a dutch tilt",
    CameraAngle.OVERHEAD: "from directly overhead",
    CameraAngle.WORMS_EYE: "from a worm's-eye view",
}

MOVE_WORDS: dict[CameraMove, str] = {
    CameraMove.STATIC: "locked-off static camera",
    CameraMove.PAN: "slow panning camera",
    CameraMove.TILT: "tilting camera",
    CameraMove.DOLLY: "smooth dolly move",
    CameraMove.TRACK: "tracking camera following the subject",
    CameraMove.ZOOM: "gradual zoom",
    CameraMove.PUSH_IN: "camera slowly pushing in",
    CameraMove.PULL_OUT: "camera slowly pulling out",
    CameraMove.HANDHELD: "handheld camera with natural shake",
    CameraMove.STEADICAM: "gliding steadicam move",
    CameraMove.CRANE: "sweeping crane move",
    CameraMove.WHIP_PAN: "fast whip pan",
}

LIGHTING_WORDS: dict[TimeOfDay, str] = {
    TimeOfDay.DAY: "bright natural daylight",
    TimeOfDay.NIGHT: "low-key night lighting with deep shadows",
    TimeOfDay.DAWN: "soft pale dawn light",
    TimeOfDay.DUSK: "warm fading dusk light",
    TimeOfDay.MORNING: "cool clear morning light",
    TimeOfDay.EVENING: "golden evening light",
    TimeOfDay.CONTINUOUS: "lighting continuous with the previous scene",
    TimeOfDay.LATER: "same lighting as before, slightly later",
}

SETTING_WORDS: dict[IntExt, str] = {
    IntExt.INT: "interior",
    IntExt.EXT: "exterior",
    IntExt.INT_EXT: "interior/exterior",
}

POLISH_SYSTEM = (
    "You polish draft prompts for generative video models. Rewrite the "
    "draft into one vivid, production-ready prompt paragraph. Preserve "
    "every concrete fact: framing, camera angle and movement, setting, "
    "lighting, characters and their appearance, action, mood, and the "
    "duration hint. Do not invent new plot facts. Return ONLY the "
    "rewritten prompt text, no preamble."
)


# --------------------------------------------------------------------------- #
# Continuity
# --------------------------------------------------------------------------- #

def _iter_character_sightings(shot: dict) -> list[tuple[str, str]]:
    """Yield (name, description) pairs from a shot db dict.

    Names live in shot["characters"] (list of strings); richer
    descriptions live in shot["raw"]["characters"] when the vision
    provider supplied them.
    """
    pairs: list[tuple[str, str]] = []
    raw = shot.get("raw")
    if isinstance(raw, dict):
        for entry in raw.get("characters") or []:
            if isinstance(entry, dict) and str(entry.get("name", "")).strip():
                pairs.append((str(entry["name"]).strip(),
                              str(entry.get("description", "") or "").strip()))
            elif isinstance(entry, str) and entry.strip():
                pairs.append((entry.strip(), ""))
    chars = shot.get("characters")
    if isinstance(chars, list):
        for entry in chars:
            if isinstance(entry, dict) and str(entry.get("name", "")).strip():
                pairs.append((str(entry["name"]).strip(),
                              str(entry.get("description", "") or "").strip()))
            elif isinstance(entry, str) and entry.strip():
                pairs.append((entry.strip(), ""))
    return pairs


def build_continuity(shots: list[dict], scenes: list[dict]) -> dict:
    """Distill a continuity bible from one pass's shots and scenes.

    Returns ``{"characters": {name: best description}, "locations":
    {location: {"int_ext", "time_of_day", "mood"}}, "style": str}``.
    "Best" description = the longest non-empty one seen for that name.
    """
    characters: dict[str, str] = {}
    canon: dict[str, str] = {}  # UPPER name -> canonical key
    for shot in shots:
        for name, desc in _iter_character_sightings(shot):
            key = canon.setdefault(name.upper(), name)
            if len(desc) > len(characters.get(key, "")):
                characters[key] = desc
            characters.setdefault(key, "")

    locations: dict[str, dict[str, str]] = {}
    loc_canon: dict[str, str] = {}
    for shot in shots:
        loc = str(shot.get("location", "") or "").strip()
        if not loc:
            continue
        key = loc_canon.setdefault(loc.upper(), loc)
        entry = locations.setdefault(key, {
            "int_ext": str(shot.get("int_ext", "") or ""),
            "time_of_day": str(shot.get("time_of_day", "") or ""),
            "mood": "",
        })
        if not entry["mood"] and str(shot.get("mood", "") or "").strip():
            entry["mood"] = str(shot["mood"]).strip()
    for scene in scenes:
        loc = str(scene.get("location", "") or "").strip()
        if not loc or loc.upper() in loc_canon:
            continue
        loc_canon[loc.upper()] = loc
        locations[loc] = {
            "int_ext": str(scene.get("int_ext", "") or ""),
            "time_of_day": str(scene.get("time_of_day", "") or ""),
            "mood": "",
        }

    style = _derive_style(shots)
    return {"characters": characters, "locations": locations, "style": style}


def _derive_style(shots: list[dict]) -> str:
    """A one-line global style directive from dominant lighting + mood."""
    times = Counter(
        str(s.get("time_of_day", "") or "").strip().upper()
        for s in shots if str(s.get("time_of_day", "") or "").strip()
    )
    moods = Counter(
        str(s.get("mood", "") or "").strip()
        for s in shots if str(s.get("mood", "") or "").strip()
    )
    bits = ["Cinematic live-action"]
    if times:
        tod = parse_enum(TimeOfDay, times.most_common(1)[0][0], TimeOfDay.DAY)
        bits.append(LIGHTING_WORDS[tod])
    if moods:
        bits.append(f"{moods.most_common(1)[0][0]} tone")
    bits.append("consistent wardrobe, props and geography across all shots")
    return ", ".join(bits) + "."


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #

def _continuity_lines(continuity: dict, names: list[str]) -> list[str]:
    lines: list[str] = []
    style = continuity.get("style", "")
    if style:
        lines.append(f"Continuity/style: {style}")
    known = continuity.get("characters", {}) or {}
    for name in names:
        desc = ""
        for key, value in known.items():
            if key.upper() == name.upper():
                desc = value
                break
        if desc:
            lines.append(f"Keep {name} consistent: {desc}.")
    return lines


def _rule_lines(rules: list[str]) -> list[str]:
    if not rules:
        return []
    return ["Prompt rules (learned from supervisor corrections):"] + [
        f"- {rule}" for rule in rules
    ]


def _shot_template(shot: dict, continuity: dict, lines: list[dict],
                   rules: list[str]) -> str:
    scale = parse_enum(ShotScale, shot.get("scale"), ShotScale.MS)
    angle = parse_enum(CameraAngle, shot.get("angle"), CameraAngle.EYE)
    move = parse_enum(CameraMove, shot.get("move"), CameraMove.STATIC)
    int_ext = parse_enum(IntExt, shot.get("int_ext"), IntExt.EXT)
    tod = parse_enum(TimeOfDay, shot.get("time_of_day"), TimeOfDay.DAY)
    subject = str(shot.get("subject", "") or "").strip() or "the scene"
    location = str(shot.get("location", "") or "").strip() or "the established location"
    duration = max(float(shot.get("end_s", 0.0)) - float(shot.get("start_s", 0.0)), 0.0)

    names = [n for n in (shot.get("characters") or []) if isinstance(n, str)]
    out = [
        f"{FRAMING_WORDS[scale]} of {subject}, {ANGLE_WORDS[angle]}, "
        f"{MOVE_WORDS[move]}.",
        f"Setting: {SETTING_WORDS[int_ext]}, {location}, {LIGHTING_WORDS[tod]}.",
    ]
    mood = str(shot.get("mood", "") or "").strip()
    if mood:
        out.append(f"Mood: {mood}.")
    if names:
        descs = []
        for name in names:
            desc = (continuity.get("characters", {}) or {}).get(name, "")
            descs.append(f"{name} ({desc})" if desc else name)
        out.append("Characters in frame: " + "; ".join(descs) + ".")
    action = str(shot.get("action_text", "") or "").strip()
    if action:
        out.append(f"Action: {action}")
    for line in lines:
        text = str(line.get("text", "") or "").strip()
        if not text:
            continue
        who = str(line.get("character", "") or "VOICE").strip()
        out.append(f'Spoken dialogue — {who}: "{text}"')
    out.append(f"Duration: approximately {duration:.1f}s.")
    out.extend(_continuity_lines(continuity, names))
    out.extend(_rule_lines(rules))
    return "\n".join(out)


def _scene_template(scene: dict, member_shots: list[dict], lines: list[dict],
                    continuity: dict, rules: list[str]) -> str:
    int_ext = parse_enum(IntExt, scene.get("int_ext"), IntExt.EXT)
    tod = parse_enum(TimeOfDay, scene.get("time_of_day"), TimeOfDay.DAY)
    location = str(scene.get("location", "") or "").strip() or "the established location"
    total = sum(
        max(float(s.get("end_s", 0.0)) - float(s.get("start_s", 0.0)), 0.0)
        for s in member_shots
    )
    names: list[str] = []
    for shot in member_shots:
        for name in shot.get("characters") or []:
            if isinstance(name, str) and name not in names:
                names.append(name)

    out = [
        f"Scene {int(scene.get('idx', 0)) + 1} umbrella prompt — "
        f"{SETTING_WORDS[int_ext]}, {location}, {LIGHTING_WORDS[tod]}.",
    ]
    synopsis = str(scene.get("synopsis", "") or "").strip()
    if synopsis:
        out.append(f"Synopsis: {synopsis}")
    out.append(
        f"Covers {len(member_shots)} shot(s), total duration approximately "
        f"{total:.1f}s."
    )
    if names:
        descs = []
        for name in names:
            desc = (continuity.get("characters", {}) or {}).get(name, "")
            descs.append(f"{name} ({desc})" if desc else name)
        out.append("Characters appearing: " + "; ".join(descs) + ".")
    for line in lines[:3]:
        text = str(line.get("text", "") or "").strip()
        if not text:
            continue
        who = str(line.get("character", "") or "VOICE").strip()
        out.append(f'Key dialogue — {who}: "{text}"')
    out.extend(_continuity_lines(continuity, names))
    out.extend(_rule_lines(rules))
    return "\n".join(out)


def _polish(brain: TextBrain | None, template: str) -> str:
    """Polish a template through the brain; NEVER raise — fall back."""
    if brain is None:
        return template
    try:
        polished = brain.complete(POLISH_SYSTEM, template)
    except Exception:
        return template
    if isinstance(polished, str) and polished.strip():
        return polished.strip()
    return template


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def generate_prompts(scenes: list[dict], shots: list[dict], dialogue: list[dict],
                     brain: TextBrain | None = None,
                     lessons: list[dict] | None = None,
                     target: str = "generic") -> list[dict]:
    """One prompt per shot plus one umbrella prompt per scene.

    Deterministic templates when ``brain`` is None; otherwise each template
    is polished via ``brain.complete`` (falling back to the template on any
    brain error). Returns dicts ready for ``db.add_describe_prompt(**d)``
    WITHOUT ``pass_id``: keys ``scene_id``, ``shot_id``, ``revision`` (=1),
    ``target``, ``prompt``, ``continuity``.
    """
    continuity = build_continuity(shots, scenes)
    rules = [
        str(l.get("rule", "") or "").strip()
        for l in (lessons or [])
        if l.get("field") == "prompt" and l.get("active", 1)
        and str(l.get("rule", "") or "").strip()
    ]

    shots_sorted = sorted(shots, key=lambda s: int(s.get("idx", 0)))
    shot_by_id = {s.get("id"): s for s in shots_sorted if s.get("id") is not None}
    scene_of_shot: dict[Any, Any] = {}
    for scene in scenes:
        for sid in scene.get("shot_ids") or []:
            scene_of_shot[sid] = scene.get("id")

    dialogue_by_shot: dict[Any, list[dict]] = {}
    dialogue_by_scene: dict[Any, list[dict]] = {}
    for line in dialogue:
        if line.get("shot_id") is not None:
            dialogue_by_shot.setdefault(line["shot_id"], []).append(line)
        if line.get("scene_id") is not None:
            dialogue_by_scene.setdefault(line["scene_id"], []).append(line)

    def shot_prompt(shot: dict) -> dict:
        lines = dialogue_by_shot.get(shot.get("id"), [])
        template = _shot_template(shot, continuity, lines, rules)
        return {
            "scene_id": scene_of_shot.get(shot.get("id")),
            "shot_id": shot.get("id"),
            "revision": 1,
            "target": target,
            "prompt": _polish(brain, template),
            "continuity": continuity,
        }

    prompts: list[dict] = []
    covered: set[Any] = set()
    for scene in sorted(scenes, key=lambda s: int(s.get("idx", 0))):
        member_ids = [sid for sid in (scene.get("shot_ids") or [])]
        members = [shot_by_id[sid] for sid in member_ids if sid in shot_by_id]
        # Pipeline rows carry BOTH scene_id and shot_id; dedupe so a line
        # indexed under both never appears twice in the umbrella prompt.
        scene_lines: list[dict] = []
        seen_lines: set[Any] = set()
        candidates = list(dialogue_by_scene.get(scene.get("id"), []))
        for sid in member_ids:
            candidates.extend(dialogue_by_shot.get(sid, []))
        for line in candidates:
            key = line.get("id") if line.get("id") is not None else id(line)
            if key in seen_lines:
                continue
            seen_lines.add(key)
            scene_lines.append(line)
        template = _scene_template(scene, members, scene_lines, continuity, rules)
        prompts.append({
            "scene_id": scene.get("id"),
            "shot_id": None,
            "revision": 1,
            "target": target,
            "prompt": _polish(brain, template),
            "continuity": continuity,
        })
        for member in members:
            prompts.append(shot_prompt(member))
            covered.add(member.get("id"))

    for shot in shots_sorted:  # shots outside any scene still get a prompt
        if shot.get("id") not in covered:
            prompts.append(shot_prompt(shot))
    return prompts


def export_text(prompts: list[dict]) -> str:
    """Human-readable numbered prompt sheet for the describe track."""
    if not prompts:
        return "SCRIPTY DESCRIBE SHEET\n(no prompts)\n"
    target = str(prompts[0].get("target", "generic") or "generic")
    out = [
        "SCRIPTY DESCRIBE SHEET",
        f"target: {target}   prompts: {len(prompts)}",
        "=" * 60,
        "",
    ]
    for n, p in enumerate(prompts, start=1):
        if p.get("shot_id") is not None:
            kind = f"SHOT PROMPT (shot id {p['shot_id']})"
        elif p.get("scene_id") is not None:
            kind = f"SCENE PROMPT (scene id {p['scene_id']})"
        else:
            kind = "PROMPT"
        rev = p.get("revision", 1)
        out.append(f"[{n:03d}] {kind}  rev {rev}")
        out.append("-" * 60)
        out.append(str(p.get("prompt", "") or ""))
        out.append("")
    return "\n".join(out)
