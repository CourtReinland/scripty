"""Fountain screenplay generation and a tolerant fountain/plain-text parser.

`classify_lines` is the single line-classification routine shared by the
parser here and by both renderers in `scripty.script.render`, so text and
HTML output always agree on what each line is.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from scripty.core.models import IntExt, TimeOfDay, parse_enum

from .assemble import _shot_key

SLUG_RE = re.compile(r"^(INT\.?/EXT\.?|I/E|INT|EXT)[. ]", re.IGNORECASE)

_TITLE_PAGE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9 _-]*):\s?(.*)$")
_TITLE_PAGE_KEYS = {
    "title", "credit", "author", "authors", "source", "draft date",
    "date", "contact", "copyright", "notes",
}


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #

def _canon_time(raw: str) -> str:
    """Canonicalize a time-of-day string, keeping unknown values verbatim."""
    raw = raw.strip()
    if not raw:
        return TimeOfDay.DAY.value
    member = parse_enum(TimeOfDay, raw, None)  # type: ignore[arg-type]
    return member.value if member is not None else raw.upper()


def _slugline(scene: dict) -> str:
    int_ext = parse_enum(IntExt, scene.get("int_ext"), IntExt.EXT).value
    location = str(scene.get("location") or "").strip().upper() or "UNKNOWN"
    tod = _canon_time(str(scene.get("time_of_day") or ""))
    return f"{int_ext} {location} - {tod}"


def to_fountain(scenes: list[dict], shots: list[dict], dialogue: list[dict],
                title: str = "UNTITLED", author: str = "Scripty") -> str:
    """Assemble scenes/shots/dialogue db dicts into a fountain document.

    Dialogue is matched to its shot by ``shot_id`` and inserted directly
    after that shot's action paragraph.
    """
    shot_index: dict[Any, dict] = {_shot_key(s): s for s in shots}
    by_shot: dict[Any, list[dict]] = {}
    for line in dialogue:
        by_shot.setdefault(line.get("shot_id"), []).append(line)

    parts: list[str] = [f"Title: {title}", f"Author: {author}",
                        "Draft date:", ""]

    def emit_dialogue(lines: list[dict]) -> None:
        for spoken in sorted(lines, key=lambda d: float(d.get("start_s") or 0.0)):
            text = str(spoken.get("text") or "").strip()
            if not text:
                continue
            parts.append(str(spoken.get("character") or "VOICE").strip().upper())
            paren = str(spoken.get("parenthetical") or "").strip()
            if paren:
                if not paren.startswith("("):
                    paren = f"({paren})"
                parts.append(paren)
            parts.append(text)
            parts.append("")

    for scene in scenes:
        parts.append(_slugline(scene))
        parts.append("")
        for sid in scene.get("shot_ids") or []:
            shot = shot_index.get(sid)
            if shot is None:
                continue
            action = str(shot.get("action_text") or "").strip()
            if action:
                parts.append(action)
                parts.append("")
            emit_dialogue(list(by_shot.get(sid, ())))
    # Dialogue that mapped to no shot (shot_id None) must not silently
    # vanish from the screenplay: emit it after the final scene.
    emit_dialogue(list(by_shot.get(None, ())))
    return "\n".join(parts).rstrip("\n") + "\n"


# --------------------------------------------------------------------------- #
# Line classification (shared with render.py)
# --------------------------------------------------------------------------- #

def _is_character(line: str) -> bool:
    if line.startswith("@"):
        return True
    if line.endswith(":") or len(line) > 60:
        return False
    core = re.sub(r"\(.*?\)", "", line).strip()
    if not core or core != core.upper():
        return False
    return any(c.isalpha() for c in core)


def classify_lines(text: str) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Split fountain text into (title_page, [(kind, stripped_line)]).

    Kinds: ``blank | slug | action | character | paren | dialogue``.
    The title page dict has lower-cased keys ("title", "author", ...).
    """
    lines = text.splitlines()
    i = 0
    title_page: dict[str, str] = {}
    first = _TITLE_PAGE_RE.match(lines[0]) if lines else None
    if first and first.group(1).strip().lower() in _TITLE_PAGE_KEYS:
        while i < len(lines) and lines[i].strip():
            match = _TITLE_PAGE_RE.match(lines[i])
            if not match:
                break
            title_page[match.group(1).strip().lower()] = match.group(2).strip()
            i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1

    items: list[tuple[str, str]] = []
    in_dialogue = False
    prev_blank = True
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            items.append(("blank", ""))
            in_dialogue = False
            prev_blank = True
        elif SLUG_RE.match(stripped):
            items.append(("slug", stripped))
            in_dialogue = False
            prev_blank = False
        elif in_dialogue:
            if stripped.startswith("(") and stripped.endswith(")"):
                items.append(("paren", stripped))
            else:
                items.append(("dialogue", stripped))
            prev_blank = False
        elif (
            prev_blank
            and _is_character(stripped)
            and i + 1 < len(lines)
            and lines[i + 1].strip()
            and not SLUG_RE.match(lines[i + 1].strip())
        ):
            items.append(("character", stripped))
            in_dialogue = True
            prev_blank = False
        else:
            items.append(("action", stripped))
            prev_blank = False
        i += 1
    return title_page, items


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

@dataclass
class ParsedScene:
    idx: int
    int_ext: str
    location: str
    time_of_day: str
    action: list[str] = field(default_factory=list)
    dialogue: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class ParsedScript:
    title: str
    scenes: list[ParsedScene] = field(default_factory=list)


def _canon_int_ext(prefix: str) -> str:
    p = prefix.upper()
    if "/" in p:
        return IntExt.INT_EXT.value
    if p.startswith("INT"):
        return IntExt.INT.value
    return IntExt.EXT.value


def _parse_slugline(line: str) -> tuple[str, str, str]:
    s = re.sub(r"\s*#[^#]*#\s*$", "", line.strip())   # drop scene numbers
    match = SLUG_RE.match(s)
    if match:
        prefix, rest = match.group(1), s[match.end():].strip()
    else:                                             # pragma: no cover
        prefix, rest = "EXT", s
    if " - " in rest:
        loc, tod = rest.rsplit(" - ", 1)
    else:
        loc, tod = rest, ""
    location = loc.strip().upper() or "UNKNOWN"
    return _canon_int_ext(prefix), location, _canon_time(tod)


def parse_fountain(text: str) -> ParsedScript:
    """Tolerant fountain (or plain-text screenplay) parser.

    Sluglines match ``^(INT|EXT|INT./EXT|I/E)[. ]`` (case-insensitive).
    A CHARACTER is an uppercase line followed by non-empty line(s);
    parentheticals are folded into the following dialogue text.
    Everything else is action. Body text before the first slugline is
    collected into an implicit UNKNOWN scene.
    """
    title_page, items = classify_lines(text)
    title = (title_page.get("title") or "").strip() or "UNTITLED"

    scenes: list[ParsedScene] = []
    current: ParsedScene | None = None
    action_buf: list[str] = []
    char: str | None = None
    paren = ""
    speech: list[str] = []

    def ensure_scene() -> ParsedScene:
        nonlocal current
        if current is None:
            current = ParsedScene(idx=len(scenes), int_ext=IntExt.EXT.value,
                                  location="UNKNOWN",
                                  time_of_day=TimeOfDay.DAY.value)
            scenes.append(current)
        return current

    def flush_action() -> None:
        nonlocal action_buf
        if action_buf:
            ensure_scene().action.append(" ".join(action_buf))
            action_buf = []

    def flush_speech() -> None:
        nonlocal char, paren, speech
        if char is not None and speech:
            spoken = " ".join(speech)
            if paren:
                spoken = f"{paren} {spoken}".strip()
            ensure_scene().dialogue.append((char, spoken))
        char, paren, speech = None, "", []

    for kind, line in items:
        if kind == "blank":
            flush_speech()
            flush_action()
        elif kind == "slug":
            flush_speech()
            flush_action()
            int_ext, location, tod = _parse_slugline(line)
            current = ParsedScene(idx=len(scenes), int_ext=int_ext,
                                  location=location, time_of_day=tod)
            scenes.append(current)
        elif kind == "character":
            flush_speech()
            flush_action()
            char = line.lstrip("@").strip()
        elif kind == "paren":
            paren = line
        elif kind == "dialogue":
            speech.append(line)
        else:  # action
            flush_speech()
            action_buf.append(line)
    flush_speech()
    flush_action()
    return ParsedScript(title=title, scenes=scenes)
