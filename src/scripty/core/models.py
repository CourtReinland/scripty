"""Typed domain models shared by every Scripty module.

Naming follows working script-supervisor convention: a shot is labelled
"<SCALE> <SUBJECT>" (e.g. "MS CAMP FIRE"), a scene heading is
"<INT/EXT>. <LOCATION> - <TIME>" (e.g. "EXT. WOODED ROAD - NIGHT").
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional


# --------------------------------------------------------------------------- #
# Enums (canonical vocabulary)
# --------------------------------------------------------------------------- #

class ShotScale(str, Enum):
    ECU = "ECU"            # extreme close-up
    CU = "CU"              # close-up
    MCU = "MCU"            # medium close-up
    MS = "MS"              # medium shot
    MWS = "MWS"            # medium-wide
    WS = "WS"              # wide shot
    EWS = "EWS"            # extreme wide
    EST = "EST"            # establishing
    OTS = "OTS"            # over-the-shoulder
    POV = "POV"            # point of view
    INSERT = "INSERT"      # detail insert
    TWO_SHOT = "2-SHOT"
    AERIAL = "AERIAL"


class CameraAngle(str, Enum):
    EYE = "EYE LEVEL"
    HIGH = "HIGH ANGLE"
    LOW = "LOW ANGLE"
    DUTCH = "DUTCH"
    OVERHEAD = "OVERHEAD"
    WORMS_EYE = "WORM'S EYE"


class CameraMove(str, Enum):
    STATIC = "STATIC"
    PAN = "PAN"
    TILT = "TILT"
    DOLLY = "DOLLY"
    TRACK = "TRACKING"
    ZOOM = "ZOOM"
    PUSH_IN = "PUSH IN"
    PULL_OUT = "PULL OUT"
    HANDHELD = "HANDHELD"
    STEADICAM = "STEADICAM"
    CRANE = "CRANE"
    WHIP_PAN = "WHIP PAN"


class IntExt(str, Enum):
    INT = "INT."
    EXT = "EXT."
    INT_EXT = "INT./EXT."


class TimeOfDay(str, Enum):
    DAY = "DAY"
    NIGHT = "NIGHT"
    DAWN = "DAWN"
    DUSK = "DUSK"
    MORNING = "MORNING"
    EVENING = "EVENING"
    CONTINUOUS = "CONTINUOUS"
    LATER = "LATER"


class ArtifactKind(str, Enum):
    SOURCE_VIDEO = "source_video"          # 1: the actual video
    KNOWN_SCRIPT = "known_script"          # 2: the actual (ground-truth) script
    GENERATED_SCRIPT = "generated_script"  # 3: scripty's attempted script
    DESCRIBE_SET = "describe_set"          # 4: the gen-AI prompt series
    GENERATED_VIDEO = "generated_video"    # 5: video produced from the prompts
    COMPARISON_REPORT = "comparison_report"


def parse_enum(cls: type[Enum], value: Any, default: Enum) -> Enum:
    """Leniently coerce model output into an enum member.

    Accepts member names ("TWO_SHOT"), values ("2-SHOT"), and common
    punctuation/case drift ("int", "Ext.", "worms eye").
    """
    if isinstance(value, cls):
        return value
    if value is None:
        return default
    text = str(value).strip().upper()
    for member in cls:
        if text in (member.name, member.value):
            return member
    norm = re.sub(r"[^A-Z0-9]", "", text)
    for member in cls:
        if norm in (re.sub(r"[^A-Z0-9]", "", member.name),
                    re.sub(r"[^A-Z0-9]", "", member.value)):
            return member
    if norm:  # initialisms of compound members: "I/E" -> IntExt.INT_EXT
        for member in cls:
            parts = [p for p in member.name.split("_") if p]
            if len(parts) > 1 and norm == "".join(p[0] for p in parts):
                return member
    return default


def timecode(seconds: float, fps: float = 24.0) -> str:
    """Render seconds as HH:MM:SS:FF."""
    if seconds < 0:
        seconds = 0.0
    fps_int = max(int(round(fps)), 1)
    total, frames = divmod(int(round(seconds * fps)), fps_int)
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}:{frames:02d}"


# --------------------------------------------------------------------------- #
# Media / project
# --------------------------------------------------------------------------- #

@dataclass
class MediaInfo:
    path: str
    duration: float
    fps: float
    width: int
    height: int
    vcodec: str = ""
    has_audio: bool = False
    acodec: str = ""


@dataclass
class Project:
    id: int
    name: str
    slug: str
    video_path: str
    duration: float
    fps: float
    width: int
    height: int
    created_at: str = ""


@dataclass
class Pass:
    id: int
    project_id: int
    number: int
    provider: str
    transcriber: str = "none"
    status: str = "pending"        # pending | running | complete | failed
    params: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    created_at: str = ""


# --------------------------------------------------------------------------- #
# Shots / scenes / dialogue
# --------------------------------------------------------------------------- #

@dataclass
class CharacterSighting:
    name: str                      # "MARA" or "MAN IN RED JACKET" if unnamed
    description: str = ""          # appearance notes for continuity


@dataclass
class ShotAnalysis:
    """What the vision pass believes about one shot. All fields correctable."""
    scale: ShotScale = ShotScale.MS
    subject: str = ""              # "CAMP FIRE", "WINDOW PANE"
    angle: CameraAngle = CameraAngle.EYE
    move: CameraMove = CameraMove.STATIC
    int_ext: IntExt = IntExt.EXT
    location: str = ""             # "WOODED ROAD"
    time_of_day: TimeOfDay = TimeOfDay.DAY
    action_text: str = ""          # screenplay action lines for this shot
    characters: list[CharacterSighting] = field(default_factory=list)
    mood: str = ""                 # lighting/tone notes, feeds describe prompts
    confidence: float = 0.5
    raw: dict = field(default_factory=dict)   # provider's full JSON answer


@dataclass
class Shot:
    id: int
    pass_id: int
    idx: int
    start_s: float
    end_s: float
    scale: ShotScale = ShotScale.MS
    subject: str = ""
    angle: CameraAngle = CameraAngle.EYE
    move: CameraMove = CameraMove.STATIC
    int_ext: IntExt = IntExt.EXT
    location: str = ""
    time_of_day: TimeOfDay = TimeOfDay.DAY
    action_text: str = ""
    characters: list[str] = field(default_factory=list)
    mood: str = ""
    confidence: float = 0.5
    keyframes: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return max(self.end_s - self.start_s, 0.0)

    def label(self) -> str:
        """Standard cut-log label, e.g. 'MS CAMP FIRE'."""
        subject = (self.subject or "UNKNOWN").upper()
        return f"{self.scale.value} {subject}"

    def log_line(self, fps: float = 24.0) -> str:
        """One line of the cut log."""
        return (f"{self.idx + 1:04d}  {timecode(self.start_s, fps)} - "
                f"{timecode(self.end_s, fps)}  {self.label()}")


@dataclass
class SceneHeading:
    int_ext: IntExt = IntExt.EXT
    location: str = "UNKNOWN"
    time_of_day: TimeOfDay = TimeOfDay.DAY

    def slugline(self) -> str:
        return f"{self.int_ext.value} {self.location.upper()} - {self.time_of_day.value}"


@dataclass
class Scene:
    id: int
    pass_id: int
    idx: int
    heading: SceneHeading
    shot_ids: list[int] = field(default_factory=list)
    synopsis: str = ""


@dataclass
class TranscriptSegment:
    start_s: float
    end_s: float
    text: str
    speaker: str = ""              # "SPEAKER 1" until attributed


@dataclass
class DialogueLine:
    id: int
    pass_id: int
    shot_id: Optional[int]
    scene_id: Optional[int]
    character: str
    text: str
    parenthetical: str = ""
    start_s: float = 0.0
    end_s: float = 0.0


# --------------------------------------------------------------------------- #
# Learning loop
# --------------------------------------------------------------------------- #

CORRECTABLE_FIELDS = (
    "scale", "subject", "angle", "move", "int_ext", "location",
    "time_of_day", "action_text", "characters", "mood", "slugline",
    "character", "text", "synopsis", "prompt",
)


@dataclass
class Correction:
    id: int
    project_id: int
    pass_id: int
    entity_type: str               # shot | scene | dialogue | describe_prompt
    entity_id: int
    field: str                     # one of CORRECTABLE_FIELDS
    model_value: str
    human_value: str
    note: str = ""
    source: str = "human"          # human | ground_truth
    scope: str = "project"         # project | global
    created_at: str = ""


@dataclass
class Lesson:
    id: int
    scope: str                     # project | global
    project_id: Optional[int]
    field: str
    rule: str                      # imperative rule distilled from corrections
    source_ids: list[int] = field(default_factory=list)
    weight: float = 1.0
    active: bool = True
    created_at: str = ""


@dataclass
class RecallBundle:
    """Corrections + distilled lessons injected into a provider prompt."""
    lessons: list[Lesson] = field(default_factory=list)
    examples: list[Correction] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.lessons and not self.examples

    def format_for_prompt(self) -> str:
        if self.is_empty():
            return ""
        parts: list[str] = ["SUPERVISOR NOTES (learned from prior passes — apply these):"]
        for lesson in self.lessons:
            parts.append(f"- RULE [{lesson.field}]: {lesson.rule}")
        for ex in self.examples:
            note = f" ({ex.note})" if ex.note else ""
            parts.append(
                f"- CORRECTION [{ex.field}]: you previously said "
                f"{ex.model_value!r}; the supervisor corrected it to "
                f"{ex.human_value!r}{note}"
            )
        return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Describe track / comparison
# --------------------------------------------------------------------------- #

@dataclass
class DescribePrompt:
    id: int
    pass_id: int
    scene_id: Optional[int]
    shot_id: Optional[int]
    revision: int                  # bumped by recursive re-prompting
    target: str                    # "generic" | "sora" | "veo" | "runway"
    prompt: str
    continuity: dict = field(default_factory=dict)
    created_at: str = ""


@dataclass
class VideoMetrics:
    ssim: float = 0.0
    psnr: float = 0.0
    detail: dict = field(default_factory=dict)


@dataclass
class CritiqueNote:
    shot_idx: int
    differences: str               # what diverged between original & generated
    prompt_fixes: str              # how to revise the describe prompt


@dataclass
class ShotContext:
    """Context handed to a VisionProvider for one shot."""
    film_title: str = ""
    pass_number: int = 1
    shot_index: int = 0
    total_shots: int = 0
    start_s: float = 0.0
    end_s: float = 0.0
    prev_summaries: list[str] = field(default_factory=list)   # last few shots
    transcript_excerpt: str = ""


# --------------------------------------------------------------------------- #
# Serialization helpers
# --------------------------------------------------------------------------- #

def to_json(obj: Any) -> str:
    """Serialize dataclasses/enums/paths to JSON."""
    def default(o: Any) -> Any:
        if isinstance(o, Enum):
            return o.value
        if isinstance(o, Path):
            return str(o)
        if hasattr(o, "__dataclass_fields__"):
            return asdict(o)
        raise TypeError(f"not JSON serializable: {type(o)!r}")
    return json.dumps(obj, default=default, sort_keys=True)


def shot_from_analysis(shot_id: int, pass_id: int, idx: int, start_s: float,
                       end_s: float, keyframes: list[str],
                       analysis: ShotAnalysis) -> Shot:
    return Shot(
        id=shot_id, pass_id=pass_id, idx=idx, start_s=start_s, end_s=end_s,
        scale=analysis.scale, subject=analysis.subject, angle=analysis.angle,
        move=analysis.move, int_ext=analysis.int_ext,
        location=analysis.location, time_of_day=analysis.time_of_day,
        action_text=analysis.action_text,
        characters=[c.name for c in analysis.characters],
        mood=analysis.mood, confidence=analysis.confidence,
        keyframes=keyframes, raw=analysis.raw,
    )
