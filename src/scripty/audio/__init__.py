"""Audio track: transcription providers + speaker/shot assignment."""
from __future__ import annotations

from .assign import assign_speakers, dialogue_from_segments
from .transcribe import (
    MockTranscriber,
    NullTranscriber,
    WhisperTranscriber,
    get_transcriber,
)

__all__ = [
    "MockTranscriber",
    "NullTranscriber",
    "WhisperTranscriber",
    "get_transcriber",
    "assign_speakers",
    "dialogue_from_segments",
]
