"""Transcriber implementations behind the `Transcriber` protocol.

`WhisperTranscriber` wraps faster-whisper (optional dependency, import
guarded); `NullTranscriber` is the silent fallback; `MockTranscriber`
replays canned segments for offline tests and demos.
"""
from __future__ import annotations

from pathlib import Path

from ..core import config
from ..core.interfaces import Transcriber
from ..core.models import TranscriptSegment

WHISPER_MODEL_SIZE = "base"


class WhisperTranscriber:
    """Local speech-to-text via faster-whisper (model "base" by default)."""

    name = "whisper"

    def __init__(self, model_size: str = WHISPER_MODEL_SIZE) -> None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover - exercised via tests
            raise RuntimeError(
                "faster-whisper is not installed; install it or use "
                "get_transcriber('none') / get_transcriber('mock')."
            ) from exc
        self.model_size = model_size
        self._model = WhisperModel(model_size, device="cpu", compute_type="int8")

    def transcribe(self, wav: Path) -> list[TranscriptSegment]:
        """Transcribe a wav file into timed segments (speaker left blank)."""
        path = Path(wav).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"audio file not found: {path}")
        segments, _info = self._model.transcribe(str(path))
        out: list[TranscriptSegment] = []
        for seg in segments:
            text = (getattr(seg, "text", "") or "").strip()
            if not text:
                continue
            out.append(TranscriptSegment(
                start_s=float(seg.start),
                end_s=float(seg.end),
                text=text,
            ))
        return out


class NullTranscriber:
    """No-op transcriber for silent films or when whisper is unavailable."""

    name = "none"

    def transcribe(self, wav: Path) -> list[TranscriptSegment]:
        return []


class MockTranscriber:
    """Deterministic offline twin: returns pre-seeded segments."""

    name = "mock"

    def __init__(self, segments: list[TranscriptSegment] | None = None) -> None:
        self._segments: list[TranscriptSegment] = list(segments or [])

    def transcribe(self, wav: Path) -> list[TranscriptSegment]:
        return list(self._segments)


def get_transcriber(name: str | None = None) -> Transcriber:
    """Resolve a transcriber by name; None defers to config.default_transcriber()."""
    resolved = (name or config.default_transcriber()).strip().lower()
    if resolved == "whisper":
        return WhisperTranscriber()
    if resolved == "none":
        return NullTranscriber()
    if resolved == "mock":
        return MockTranscriber()
    raise ValueError(
        f"unknown transcriber {resolved!r}; expected one of: whisper, none, mock"
    )
