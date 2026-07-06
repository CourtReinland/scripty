"""Offline tests for scripty.audio (transcribe + assign). No network, no whisper."""
from __future__ import annotations

import sys
import types
from unittest import mock

import pytest

from scripty.audio.assign import (
    DEFAULT_CHARACTER,
    GAP_FLIP_SECONDS,
    assign_speakers,
    dialogue_from_segments,
)
from scripty.audio.transcribe import (
    MockTranscriber,
    NullTranscriber,
    WhisperTranscriber,
    get_transcriber,
)
from scripty.core.interfaces import Transcriber
from scripty.core.models import TranscriptSegment


def seg(start: float, end: float, text: str = "hi", speaker: str = "") -> TranscriptSegment:
    return TranscriptSegment(start_s=start, end_s=end, text=text, speaker=speaker)


# --------------------------------------------------------------------------- #
# Transcribers
# --------------------------------------------------------------------------- #

class TestNullTranscriber:
    def test_returns_empty(self, tmp_path):
        t = NullTranscriber()
        assert t.name == "none"
        assert t.transcribe(tmp_path / "anything.wav") == []

    def test_satisfies_protocol(self):
        assert isinstance(NullTranscriber(), Transcriber)


class TestMockTranscriber:
    def test_replays_seeded_segments(self, tmp_path):
        segments = [seg(0.0, 1.0, "hello"), seg(1.5, 2.0, "world")]
        t = MockTranscriber(segments)
        assert t.name == "mock"
        assert t.transcribe(tmp_path / "x.wav") == segments

    def test_returns_copy_not_internal_list(self, tmp_path):
        segments = [seg(0.0, 1.0)]
        t = MockTranscriber(segments)
        got = t.transcribe(tmp_path / "x.wav")
        got.append(seg(9.0, 9.5))
        assert t.transcribe(tmp_path / "x.wav") == segments

    def test_defaults_to_empty(self, tmp_path):
        assert MockTranscriber().transcribe(tmp_path / "x.wav") == []

    def test_satisfies_protocol(self):
        assert isinstance(MockTranscriber([]), Transcriber)


class _FakeWhisperSegment:
    def __init__(self, start: float, end: float, text: str):
        self.start, self.end, self.text = start, end, text


def _fake_faster_whisper(segments, captured: dict) -> types.ModuleType:
    module = types.ModuleType("faster_whisper")

    class WhisperModel:
        def __init__(self, model_size, **kwargs):
            captured["model_size"] = model_size

        def transcribe(self, path, **kwargs):
            captured["path"] = path
            return iter(segments), {"language": "en"}

    module.WhisperModel = WhisperModel
    return module


class TestWhisperTranscriber:
    def test_raises_helpful_error_when_not_installed(self):
        with mock.patch.dict(sys.modules, {"faster_whisper": None}):
            with pytest.raises(RuntimeError, match="faster-whisper"):
                WhisperTranscriber()

    def test_transcribes_via_faster_whisper(self, tmp_path):
        wav = tmp_path / "audio.wav"
        wav.write_bytes(b"RIFF0000WAVE")
        captured: dict = {}
        fake = _fake_faster_whisper(
            [_FakeWhisperSegment(0.0, 1.2, "  Hello there.  "),
             _FakeWhisperSegment(1.5, 2.0, "   "),          # blank: dropped
             _FakeWhisperSegment(2.0, 3.4, "Good night.")],
            captured,
        )
        with mock.patch.dict(sys.modules, {"faster_whisper": fake}):
            t = WhisperTranscriber()
            got = t.transcribe(wav)
        assert t.name == "whisper"
        assert captured["model_size"] == "base"
        assert captured["path"] == str(wav.resolve())
        assert got == [
            TranscriptSegment(start_s=0.0, end_s=1.2, text="Hello there."),
            TranscriptSegment(start_s=2.0, end_s=3.4, text="Good night."),
        ]

    def test_missing_file_rejected(self, tmp_path):
        fake = _fake_faster_whisper([], {})
        with mock.patch.dict(sys.modules, {"faster_whisper": fake}):
            t = WhisperTranscriber()
            with pytest.raises(FileNotFoundError):
                t.transcribe(tmp_path / "nope.wav")


class TestGetTranscriber:
    def test_by_name(self):
        assert isinstance(get_transcriber("none"), NullTranscriber)
        assert isinstance(get_transcriber("mock"), MockTranscriber)
        assert isinstance(get_transcriber(" NONE "), NullTranscriber)  # lenient

    def test_none_defers_to_config(self):
        with mock.patch("scripty.core.config.default_transcriber",
                        return_value="none") as default:
            assert isinstance(get_transcriber(None), NullTranscriber)
            default.assert_called_once_with()

    def test_whisper_name_constructs_whisper(self):
        fake = _fake_faster_whisper([], {})
        with mock.patch.dict(sys.modules, {"faster_whisper": fake}):
            assert isinstance(get_transcriber("whisper"), WhisperTranscriber)

    def test_unknown_name_rejected(self):
        with pytest.raises(ValueError, match="unknown transcriber"):
            get_transcriber("siri")


# --------------------------------------------------------------------------- #
# assign_speakers
# --------------------------------------------------------------------------- #

class TestAssignSpeakers:
    def test_empty(self):
        assert assign_speakers([]) == []

    def test_starts_with_speaker_1(self):
        got = assign_speakers([seg(0.0, 1.0)])
        assert got[0].speaker == "SPEAKER 1"

    def test_small_gap_keeps_speaker(self):
        got = assign_speakers([seg(0.0, 1.0), seg(1.5, 2.0)])  # gap 0.5 <= 1.2
        assert [s.speaker for s in got] == ["SPEAKER 1", "SPEAKER 1"]

    def test_big_gap_flips_speaker(self):
        got = assign_speakers([seg(0.0, 1.0), seg(2.5, 3.0)])  # gap 1.5 > 1.2
        assert [s.speaker for s in got] == ["SPEAKER 1", "SPEAKER 2"]

    def test_flips_back_and_forth(self):
        got = assign_speakers([
            seg(0.0, 1.0),      # SPEAKER 1
            seg(2.5, 3.0),      # gap 1.5 -> SPEAKER 2
            seg(3.2, 4.0),      # gap 0.2 -> SPEAKER 2
            seg(6.0, 7.0),      # gap 2.0 -> SPEAKER 1
        ])
        assert [s.speaker for s in got] == [
            "SPEAKER 1", "SPEAKER 2", "SPEAKER 2", "SPEAKER 1"]

    def test_preset_speaker_kept_and_tracked(self):
        got = assign_speakers([
            seg(0.0, 1.0, speaker="MARA"),          # already set: untouched
            seg(1.1, 2.0),                          # small gap -> SPEAKER 1
            seg(1.1 + 2.0, 4.5, speaker="SPEAKER 2"),  # set: kept, becomes current
            seg(4.6, 5.0),                          # small gap -> stays SPEAKER 2
        ])
        assert [s.speaker for s in got] == [
            "MARA", "SPEAKER 1", "SPEAKER 2", "SPEAKER 2"]

    def test_input_not_mutated(self):
        original = [seg(0.0, 1.0)]
        assign_speakers(original)
        assert original[0].speaker == ""

    def test_gap_constant_is_1_2(self):
        assert GAP_FLIP_SECONDS == pytest.approx(1.2)

    def test_unsorted_input_ordered_by_start(self):
        got = assign_speakers([seg(5.0, 6.0), seg(0.0, 1.0)])
        assert [s.start_s for s in got] == [0.0, 5.0]
        assert [s.speaker for s in got] == ["SPEAKER 1", "SPEAKER 2"]


# --------------------------------------------------------------------------- #
# dialogue_from_segments
# --------------------------------------------------------------------------- #

def _shots() -> list[dict]:
    return [
        {"id": 11, "idx": 0, "start_s": 0.0, "end_s": 2.0},
        {"id": 12, "idx": 1, "start_s": 2.0, "end_s": 4.0},
    ]


class TestDialogueFromSegments:
    def test_maps_by_midpoint(self):
        rows = dialogue_from_segments(
            [seg(0.5, 1.5, "one", "SPEAKER 1"), seg(2.5, 3.5, "two", "SPEAKER 2")],
            _shots())
        assert [r["shot_id"] for r in rows] == [11, 12]
        assert [r["character"] for r in rows] == ["SPEAKER 1", "SPEAKER 2"]
        assert [r["text"] for r in rows] == ["one", "two"]

    def test_row_keys_ready_for_add_dialogue(self):
        rows = dialogue_from_segments([seg(0.5, 1.5, "one", "SPEAKER 1")], _shots())
        assert set(rows[0]) == {
            "shot_id", "character", "text", "parenthetical", "start_s", "end_s"}
        assert rows[0]["parenthetical"] == ""
        assert rows[0]["start_s"] == 0.5
        assert rows[0]["end_s"] == 1.5

    def test_boundary_midpoint_goes_to_containing_shot(self):
        # midpoint exactly 2.0: [2.0, 4.0) contains it, [0.0, 2.0) does not
        rows = dialogue_from_segments([seg(1.0, 3.0, "edge")], _shots())
        assert rows[0]["shot_id"] == 12

    def test_unknown_speaker_becomes_voice_os(self):
        rows = dialogue_from_segments([seg(0.5, 1.5, "who")], _shots())
        assert rows[0]["character"] == DEFAULT_CHARACTER == "VOICE (O.S.)"

    def test_midpoint_outside_all_shots_gets_none(self):
        rows = dialogue_from_segments([seg(10.0, 11.0, "off the end")], _shots())
        assert rows[0]["shot_id"] is None

    def test_blank_text_dropped(self):
        rows = dialogue_from_segments(
            [seg(0.5, 1.5, "   "), seg(0.5, 1.5, "kept")], _shots())
        assert [r["text"] for r in rows] == ["kept"]

    def test_no_shots(self):
        rows = dialogue_from_segments([seg(0.5, 1.5, "hello")], [])
        assert rows[0]["shot_id"] is None
