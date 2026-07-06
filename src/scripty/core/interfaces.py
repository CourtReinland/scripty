"""Protocols decoupling modules that are built (and tested) independently.

Vision, transcription, and text-generation are pluggable behind these
interfaces; every implementation must have a mock twin so the whole
pipeline runs offline and in tests.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from .models import RecallBundle, ShotAnalysis, ShotContext, TranscriptSegment


@runtime_checkable
class VisionProvider(Protocol):
    """Looks at keyframes and reports what the camera sees."""

    name: str

    def analyze_shot(self, frames: list[Path], ctx: ShotContext,
                     recall: RecallBundle) -> ShotAnalysis:
        """Analyze one shot from its keyframes.

        Implementations MUST honor the recall bundle: lessons and prior
        corrections take precedence over the model's own first impression.
        """
        ...

    def critique_frames(self, original: list[Path], generated: list[Path],
                        context: str) -> str:
        """Compare original vs generated keyframes; return divergence notes."""
        ...


@runtime_checkable
class TextBrain(Protocol):
    """Plain text completion used for distilling lessons & polishing prompts."""

    name: str

    def complete(self, system: str, user: str) -> str:
        ...


@runtime_checkable
class Transcriber(Protocol):
    """Turns an audio track into timed dialogue segments."""

    name: str

    def transcribe(self, wav: Path) -> list[TranscriptSegment]:
        ...
