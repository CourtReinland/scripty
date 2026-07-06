"""Vision providers: the Anthropic-backed eye and its deterministic mock twin."""
from __future__ import annotations

import base64
import hashlib
import re
from enum import Enum
from pathlib import Path
from typing import Any

from scripty.core import config
from scripty.core.interfaces import VisionProvider
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
)
from scripty.vision.taxonomy import build_shot_prompt, parse_shot_json

try:
    import anthropic
except ImportError:  # pragma: no cover - SDK is an install-time optional
    anthropic = None  # type: ignore[assignment]

_SYSTEM = (
    "You are Scripty, a meticulous film script supervisor. You watch keyframes "
    "and log shots exactly the way a working supervisor would: terse, "
    "conventional, continuity-obsessed. Learned supervisor notes always "
    "outrank your first impression."
)

# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

_ENUM_FIELDS: dict[str, type] = {
    "scale": ShotScale, "angle": CameraAngle, "move": CameraMove,
    "int_ext": IntExt, "time_of_day": TimeOfDay,
}
_TEXT_FIELDS = ("subject", "location", "action_text", "mood")
_PREFER_PATTERNS = (
    re.compile(r"prefer\s+['\"]([^'\"]+)['\"]\s+over\s+['\"]([^'\"]+)['\"]",
               re.IGNORECASE),
    re.compile(r"prefer\s+(.+?)\s+over\s+(.+?)(?:\s+when\b|[.,;]|$)",
               re.IGNORECASE),
)


def _resolve_frames(frames: list[Path]) -> list[Path]:
    """Resolve and validate frame paths; silently drop anything unreadable."""
    resolved: list[Path] = []
    for frame in frames or []:
        try:
            path = Path(frame).expanduser().resolve()
        except (OSError, ValueError):
            continue
        if path.is_file():
            resolved.append(path)
    return resolved


def _read_all_bytes(frames: list[Path]) -> bytes:
    chunks: list[bytes] = []
    for path in _resolve_frames(frames):
        try:
            chunks.append(path.read_bytes())
        except OSError:
            continue
    return b"".join(chunks)


def _image_blocks(frames: list[Path]) -> list[dict]:
    blocks: list[dict] = []
    for path in _resolve_frames(frames):
        try:
            data = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError:
            continue
        blocks.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg", "data": data}})
    return blocks


def _response_text(response: Any) -> str:
    parts = [getattr(block, "text", "") or ""
             for block in (getattr(response, "content", None) or [])
             if getattr(block, "type", "") == "text"]
    return "\n".join(parts)


def _field_as_text(analysis: ShotAnalysis, field: str) -> str:
    value = getattr(analysis, field, "")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return ", ".join(getattr(item, "name", str(item)) for item in value)
    return str(value)


def _set_field(analysis: ShotAnalysis, field: str, value: Any) -> None:
    if field in _ENUM_FIELDS:
        setattr(analysis, field,
                parse_enum(_ENUM_FIELDS[field], value, getattr(analysis, field)))
    elif field == "characters":
        names = ([str(n).strip() for n in value] if isinstance(value, list)
                 else [n.strip() for n in str(value).split(",")])
        analysis.characters = [CharacterSighting(name=n) for n in names if n]
    elif field in _TEXT_FIELDS:
        setattr(analysis, field, str(value).strip())


def _parse_preference(rule: str) -> tuple[str, str] | None:
    for pattern in _PREFER_PATTERNS:
        match = pattern.search(rule)
        if match:
            return match.group(1).strip(), match.group(2).strip()
    return None


def _apply_recall(analysis: ShotAnalysis, recall: RecallBundle) -> ShotAnalysis:
    """Make recall outrank instinct: lessons rewrite matches, examples win outright."""
    applied: list[str] = []
    for lesson in recall.lessons:
        pair = _parse_preference(lesson.rule)
        if not pair:
            continue
        preferred, over = pair
        known = lesson.field in _ENUM_FIELDS or lesson.field in _TEXT_FIELDS
        fields = [lesson.field] if known else \
            list(_TEXT_FIELDS) + list(_ENUM_FIELDS)
        for field in fields:
            current = _field_as_text(analysis, field)
            if current and over.lower() in current.lower():
                _set_field(analysis, field, preferred)
                applied.append(f"lesson:{lesson.id}:{field}")
    corrected_fields: set[str] = set()
    for example in recall.examples:  # ranked best-first: first one per field wins
        if example.field in corrected_fields:
            continue
        if (example.field in _ENUM_FIELDS or example.field in _TEXT_FIELDS
                or example.field == "characters"):
            corrected_fields.add(example.field)
            _set_field(analysis, example.field, example.human_value)
            applied.append(f"correction:{example.id}:{example.field}")
    if applied:
        analysis.raw = dict(analysis.raw)
        analysis.raw["recall_applied"] = applied
    return analysis


def _critique_prompt(context: str) -> str:
    return (
        "You are comparing keyframes of an ORIGINAL film shot against "
        "keyframes of a shot GENERATED from a text prompt.\n"
        f"SHOT CONTEXT: {context}\n\n"
        "In one short paragraph, name the concrete visual differences "
        "(framing, subject, lighting, palette, motion cues, set/costume "
        "continuity). Then start a new paragraph with 'PROMPT FIXES:' giving "
        "specific describe-prompt revisions that would close the gap. If the "
        "shots are effectively identical, answer with the single word "
        "IDENTICAL."
    )


# ---------------------------------------------------------------------------
# providers
# ---------------------------------------------------------------------------

class AnthropicVision:
    """VisionProvider backed by the Anthropic Messages API."""

    name = "anthropic"

    def __init__(self, model: str | None = None) -> None:
        if anthropic is None:
            raise RuntimeError(
                "the 'anthropic' SDK is not installed; use the 'mock' provider")
        self.model = model or config.VISION_MODEL
        self._client = anthropic.Anthropic()

    def _create(self, content: list[dict]) -> Any:
        return self._client.messages.create(
            model=self.model,
            max_tokens=2048,
            thinking={"type": "adaptive"},
            system=_SYSTEM,
            messages=[{"role": "user", "content": content}],
        )

    def analyze_shot(self, frames: list[Path], ctx: ShotContext,
                     recall: RecallBundle) -> ShotAnalysis:
        content = _image_blocks(frames)
        content.append({"type": "text", "text": build_shot_prompt(ctx, recall)})
        try:
            response = self._create(content)
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            return ShotAnalysis(confidence=0.1, raw={"error": str(exc)})
        if getattr(response, "stop_reason", None) == "refusal":
            return ShotAnalysis(confidence=0.1, raw={
                "error": "model refused to analyze this shot"})
        return parse_shot_json(_response_text(response))

    def critique_frames(self, original: list[Path], generated: list[Path],
                        context: str) -> str:
        content: list[dict] = [{"type": "text", "text": "ORIGINAL SHOT KEYFRAMES:"}]
        content += _image_blocks(original)
        content.append({"type": "text", "text": "GENERATED SHOT KEYFRAMES:"})
        content += _image_blocks(generated)
        content.append({"type": "text", "text": _critique_prompt(context)})
        try:
            response = self._create(content)
        except (anthropic.APIStatusError, anthropic.APIConnectionError):
            return ""
        if getattr(response, "stop_reason", None) == "refusal":
            return ""
        return _response_text(response).strip()


_SCALES = (ShotScale.WS, ShotScale.MS, ShotScale.CU, ShotScale.MCU,
           ShotScale.EWS, ShotScale.ECU)
_SUBJECTS = ("CAMP FIRE", "WINDOW PANE", "OLD TRUCK", "RIVER BEND",
             "LONE FIGURE", "KITCHEN TABLE", "NEON SIGN", "MOUNTAIN PASS")
_LOCATIONS = ("WOODED ROAD", "DINER", "RIVERSIDE", "WAREHOUSE", "MOTEL ROOM",
              "CITY STREET", "MEADOW", "PARKING LOT")
_MOODS = ("Low ember glow, long shadows", "Flat overcast light, muted palette",
          "Hard noon sun, deep contrast", "Sodium-vapor haze, wet asphalt sheen",
          "Cold blue hour, breath visible", "Warm practicals, dust in the beam")
_ANGLES = (CameraAngle.EYE, CameraAngle.HIGH, CameraAngle.LOW)
_MOVES = (CameraMove.STATIC, CameraMove.PAN, CameraMove.PUSH_IN,
          CameraMove.HANDHELD)
_CHARACTERS = (("MARA", "woman in a gray coat"),
               ("DEL", "wiry man, denim jacket"),
               ("THE STRANGER", "tall figure, face in shadow"),
               ("KID", "child with a red backpack"))


class MockVision:
    """Deterministic offline twin: pseudo-analysis from frame bytes.

    Derives every field from a hash of the keyframe bytes plus a brightness
    proxy (mean byte value), stdlib only. Honors the recall bundle so the
    learning loop is demonstrable offline.
    """

    name = "mock"

    def analyze_shot(self, frames: list[Path], ctx: ShotContext,
                     recall: RecallBundle) -> ShotAnalysis:
        payload = _read_all_bytes(frames)
        if not payload:
            payload = f"shot-{ctx.shot_index}".encode()
        digest = hashlib.sha256(payload).digest()
        brightness = sum(payload) / len(payload)
        subject = _SUBJECTS[digest[1] % len(_SUBJECTS)]
        mood = _MOODS[digest[3] % len(_MOODS)]
        characters: list[CharacterSighting] = []
        for i in range(digest[7] % 3):
            name, description = _CHARACTERS[digest[8 + i] % len(_CHARACTERS)]
            if all(existing.name != name for existing in characters):
                characters.append(
                    CharacterSighting(name=name, description=description))
        if characters:
            action = (f"{' and '.join(c.name for c in characters)} hold near "
                      f"the {subject.lower()}. {mood}.")
        else:
            action = f"The {subject.lower()} sits alone in frame. {mood}."
        analysis = ShotAnalysis(
            scale=_SCALES[digest[0] % len(_SCALES)],
            subject=subject,
            angle=_ANGLES[digest[4] % len(_ANGLES)],
            move=_MOVES[digest[5] % len(_MOVES)],
            int_ext=IntExt.EXT if digest[6] % 2 == 0 else IntExt.INT,
            location=_LOCATIONS[digest[2] % len(_LOCATIONS)],
            time_of_day=TimeOfDay.DAY if brightness >= 96 else TimeOfDay.NIGHT,
            action_text=action,
            characters=characters,
            mood=mood,
            confidence=round(0.5 + (digest[9] % 30) / 100, 2),
            raw={"provider": self.name, "digest": digest.hex()[:12],
                 "brightness": round(brightness, 1),
                 "frames": [str(p) for p in _resolve_frames(frames)]},
        )
        return _apply_recall(analysis, recall)

    def critique_frames(self, original: list[Path], generated: list[Path],
                        context: str) -> str:
        original_bytes = _read_all_bytes(original)
        generated_bytes = _read_all_bytes(generated)
        if original_bytes == generated_bytes:
            return ""
        sig_o = hashlib.sha256(original_bytes).hexdigest()[:8]
        sig_g = hashlib.sha256(generated_bytes).hexdigest()[:8]
        excerpt = (context or "").strip()[:120]
        return (
            f"Generated shot diverges from the original (signature {sig_o} vs "
            f"{sig_g}): framing drifts wider, palette runs warmer, and subject "
            f"placement wanders from the source. Context: {excerpt}\n\n"
            "PROMPT FIXES: restate the original scale, subject, and camera "
            "move explicitly; pin the lighting and palette of the source shot; "
            "name each character and costume so continuity holds."
        )


def get_provider(name: str | None = None) -> VisionProvider:
    """Resolve a VisionProvider by name; ``None`` uses config.default_provider()."""
    resolved = (name or config.default_provider()).strip().lower()
    if resolved == "anthropic":
        return AnthropicVision()
    if resolved == "mock":
        return MockVision()
    raise ValueError(f"unknown vision provider: {resolved!r}")
