"""Tests for scripty.ingest.

Unit tests mock the single subprocess seam (scripty.ingest.ffmpeg._run) so
they run fully offline. One integration test builds a tiny real film with
ffmpeg (available on PATH) and runs the whole ingest chain on it.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

import scripty.ingest.ffmpeg as ff
import scripty.ingest.testfilm as tf
from scripty.core import config
from scripty.core.models import MediaInfo
from scripty.ingest import (
    cuts_to_shots,
    detect_cuts,
    extract_audio,
    extract_keyframes,
    make_test_film,
    probe,
)

ROOT = Path(__file__).resolve().parents[1]

FFPROBE_JSON = json.dumps({
    "streams": [
        {"codec_type": "video", "codec_name": "h264", "width": 1920,
         "height": 1080, "r_frame_rate": "24000/1001",
         "avg_frame_rate": "24000/1001", "duration": "120.5"},
        {"codec_type": "audio", "codec_name": "aac"},
    ],
    "format": {"duration": "121.0"},
})

FFPROBE_JSON_SILENT = json.dumps({
    "streams": [
        {"codec_type": "video", "codec_name": "h264", "width": 640,
         "height": 360, "r_frame_rate": "24/1"},
    ],
    "format": {"duration": "10.0"},
})

SCENE_OUTPUT = """\
frame:0    pts:7680   pts_time:0.2
lavfi.scene_score=0.91
frame:1    pts:24576  pts_time:1.0
lavfi.scene_score=0.55
frame:2    pts:29491  pts_time:1.2
lavfi.scene_score=0.42
frame:3    pts:73728  pts_time:3.0
lavfi.scene_score=0.61
"""


def _cp(cmd: list[str], stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr=stderr)


@pytest.fixture
def video_file(tmp_path: Path) -> Path:
    v = tmp_path / "clip.mp4"
    v.write_bytes(b"\x00\x00\x00\x18ftypmp42 fake")
    return v


@pytest.fixture
def run_recorder(monkeypatch: pytest.MonkeyPatch):
    """Mock ff._run, recording commands; stdout selected per executable."""
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls.append(list(cmd))
        if cmd[0] == "ffprobe":
            return _cp(cmd, stdout=FFPROBE_JSON)
        return _cp(cmd, stdout=SCENE_OUTPUT)

    monkeypatch.setattr(ff, "_run", fake_run)
    return calls


# --------------------------------------------------------------------------- #
# probe
# --------------------------------------------------------------------------- #

def test_probe_parses_media_info(video_file: Path, run_recorder: list[list[str]]) -> None:
    info = probe(video_file)
    assert isinstance(info, MediaInfo)
    assert info.path == str(video_file.resolve())
    assert info.duration == pytest.approx(121.0)
    assert info.fps == pytest.approx(24000 / 1001)
    assert (info.width, info.height) == (1920, 1080)
    assert info.vcodec == "h264"
    assert info.has_audio is True
    assert info.acodec == "aac"
    cmd = run_recorder[0]
    assert cmd[0] == "ffprobe"
    assert "-print_format" in cmd and "json" in cmd


def test_probe_no_audio(video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ff, "_run",
                        lambda cmd, **kw: _cp(cmd, stdout=FFPROBE_JSON_SILENT))
    info = probe(video_file)
    assert info.has_audio is False
    assert info.acodec == ""
    assert info.fps == pytest.approx(24.0)
    assert info.duration == pytest.approx(10.0)


def test_probe_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        probe(tmp_path / "nope.mp4")


# --------------------------------------------------------------------------- #
# detect_cuts
# --------------------------------------------------------------------------- #

def test_detect_cuts_filters_close_cuts(video_file: Path,
                                        run_recorder: list[list[str]],
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MIN_SHOT_SECONDS", 0.4)
    # raw pts_times: 0.2 (too close to t=0), 1.0, 1.2 (too close to 1.0), 3.0
    assert detect_cuts(video_file) == [1.0, 3.0]


def test_detect_cuts_threshold_in_command(video_file: Path,
                                          run_recorder: list[list[str]]) -> None:
    detect_cuts(video_file, threshold=0.25)
    cmd = run_recorder[0]
    vf = cmd[cmd.index("-vf") + 1]
    assert vf == "select='gt(scene,0.25)',metadata=print:file=-"
    assert "-an" in cmd
    assert cmd[-3:] == ["-f", "null", "-"]


def test_detect_cuts_empty_output(video_file: Path,
                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ff, "_run", lambda cmd, **kw: _cp(cmd))
    assert detect_cuts(video_file) == []


# --------------------------------------------------------------------------- #
# cuts_to_shots
# --------------------------------------------------------------------------- #

def test_cuts_to_shots_basic() -> None:
    assert cuts_to_shots([1.0, 3.0], 5.0) == [(0.0, 1.0), (1.0, 3.0), (3.0, 5.0)]


def test_cuts_to_shots_no_cuts() -> None:
    assert cuts_to_shots([], 4.2) == [(0.0, 4.2)]


def test_cuts_to_shots_drops_bad_bounds() -> None:
    # unsorted input, cut at 0, beyond duration, and a duplicate (zero-length)
    assert cuts_to_shots([6.0, 2.0, 0.0, 2.0], 4.0) == [(0.0, 2.0), (2.0, 4.0)]
    assert cuts_to_shots([1.0], 0.0) == []


# --------------------------------------------------------------------------- #
# extract_keyframes
# --------------------------------------------------------------------------- #

def test_extract_keyframes_names_and_commands(video_file: Path, tmp_path: Path,
                                              run_recorder: list[list[str]]) -> None:
    out_dir = tmp_path / "kf"
    shots = [(0.0, 2.0), (2.0, 4.0)]
    frames = extract_keyframes(video_file, shots, out_dir, per_shot=3)

    assert [[p.name for p in group] for group in frames] == [
        ["shot0000_f0.jpg", "shot0000_f1.jpg", "shot0000_f2.jpg"],
        ["shot0001_f0.jpg", "shot0001_f1.jpg", "shot0001_f2.jpg"],
    ]
    assert all(p.parent == out_dir.resolve() for group in frames for p in group)
    assert len(run_recorder) == 6

    first = run_recorder[0]
    assert first.index("-ss") < first.index("-i")  # fast seek before input
    assert first[first.index("-q:v") + 1] == "3"
    assert first[first.index("-vf") + 1] == "scale='min(960,iw)':-2"
    assert "-frames:v" in first

    # 15% / 50% / 85% of shot 0 (0.0-2.0)
    seek_times = [cmd[cmd.index("-ss") + 1] for cmd in run_recorder[:3]]
    assert seek_times == ["0.300", "1.000", "1.700"]


def test_extract_keyframes_single_frame_midpoint(video_file: Path, tmp_path: Path,
                                                 run_recorder: list[list[str]]) -> None:
    frames = extract_keyframes(video_file, [(4.0, 6.0)], tmp_path / "kf", per_shot=1)
    assert [p.name for p in frames[0]] == ["shot0000_f0.jpg"]
    cmd = run_recorder[0]
    assert cmd[cmd.index("-ss") + 1] == "5.000"


# --------------------------------------------------------------------------- #
# extract_audio
# --------------------------------------------------------------------------- #

def test_extract_audio_returns_none_without_audio(video_file: Path, tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ff, "probe", lambda v: MediaInfo(
        path=str(v), duration=1.0, fps=24.0, width=640, height=360,
        has_audio=False))
    calls: list[list[str]] = []
    monkeypatch.setattr(ff, "_run", lambda cmd, **kw: calls.append(list(cmd)) or _cp(cmd))
    assert extract_audio(video_file, tmp_path / "a.wav") is None
    assert calls == []  # no ffmpeg invocation when there is nothing to extract


def test_extract_audio_command(video_file: Path, tmp_path: Path,
                               monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ff, "probe", lambda v: MediaInfo(
        path=str(v), duration=1.0, fps=24.0, width=640, height=360,
        has_audio=True, acodec="aac"))
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess:
        calls.append(list(cmd))
        return _cp(cmd)

    monkeypatch.setattr(ff, "_run", fake_run)
    out = extract_audio(video_file, tmp_path / "audio" / "a.wav")
    assert out == (tmp_path / "audio" / "a.wav").resolve()
    assert out.parent.is_dir()
    cmd = calls[0]
    assert cmd[cmd.index("-ar") + 1] == "16000"
    assert cmd[cmd.index("-ac") + 1] == "1"
    assert cmd[cmd.index("-acodec") + 1] == "pcm_s16le"
    assert "-vn" in cmd


# --------------------------------------------------------------------------- #
# make_test_film (unit, mocked ffmpeg)
# --------------------------------------------------------------------------- #

def test_make_test_film_validates_args(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        make_test_film(tmp_path / "f.mp4", shots=0)
    with pytest.raises(ValueError):
        make_test_film(tmp_path / "f.mp4", shots=2, shot_seconds=0.0)


def test_make_test_film_mocked_commands(tmp_path: Path,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess:
        calls.append(list(cmd))
        Path(cmd[-1]).write_bytes(b"fake-mp4")  # every command writes its output
        return _cp(cmd)

    monkeypatch.setattr(ff, "_run", fake_run)
    out = make_test_film(tmp_path / "film", shots=4, shot_seconds=1.5)
    assert out == (tmp_path / "film.mp4").resolve()  # suffix defaulted
    assert out.is_file()
    assert len(calls) == 5  # 4 segments + 1 concat

    seg_cmds, concat_cmd = calls[:4], calls[4]
    tones = [arg for cmd in seg_cmds for arg in cmd if arg.startswith("sine=")]
    assert len(set(tones)) == 4  # a distinct frequency per shot
    for cmd in seg_cmds:
        assert cmd[cmd.index("-t") + 1] == "1.500"
        assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
        assert cmd[cmd.index("-r") + 1] == "24"
    sources = [cmd[cmd.index("-i") + 1] for cmd in seg_cmds]
    assert sources[0].startswith("testsrc2=")
    assert sources[1].startswith("color=")
    assert sources[2].startswith("smptebars=")
    assert sources[3].startswith("gradients=")
    assert any("drawtext" in arg for arg in seg_cmds[1])
    assert "concat" in concat_cmd and concat_cmd[-1] == str(out)


def test_script_main_invokes_builder(tmp_path: Path,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake(out: Path, shots: int = 5, shot_seconds: float = 3.0) -> Path:
        seen["args"] = (Path(out), shots, shot_seconds)
        return Path(out)

    monkeypatch.setattr(tf, "make_test_film", fake)
    spec = importlib.util.spec_from_file_location(
        "make_test_film_script", ROOT / "scripts" / "make_test_film.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rc = mod.main([str(tmp_path / "film.mp4"), "--shots", "3", "--seconds", "1.5"])
    assert rc == 0
    assert seen["args"] == (tmp_path / "film.mp4", 3, 1.5)


# --------------------------------------------------------------------------- #
# integration: real ffmpeg on a tiny synthetic film (< 20s)
# --------------------------------------------------------------------------- #

@pytest.mark.integration
def test_real_film_ingest_chain(tmp_path: Path) -> None:
    film = make_test_film(tmp_path / "film.mp4", shots=2, shot_seconds=1.0)
    assert film.is_file() and film.stat().st_size > 0

    info = probe(film)
    assert (info.width, info.height) == (640, 360)
    assert info.fps == pytest.approx(24.0, abs=0.5)
    assert 1.6 <= info.duration <= 2.6
    assert info.has_audio

    cuts = detect_cuts(film, threshold=0.3)
    assert cuts, "expected at least one detected cut"
    assert any(abs(c - 1.0) < 0.35 for c in cuts), f"no cut near 1.0s: {cuts}"

    spans = cuts_to_shots(cuts, info.duration)
    assert len(spans) >= 2
    assert spans[0][0] == 0.0
    assert spans[-1][1] == pytest.approx(info.duration)

    frames = extract_keyframes(film, spans[:2], tmp_path / "kf", per_shot=2)
    assert len(frames) == 2
    for group in frames:
        assert len(group) == 2
        for p in group:
            assert p.is_file() and p.stat().st_size > 0

    wav = extract_audio(film, tmp_path / "audio.wav")
    assert wav is not None and wav.is_file()
    assert wav.stat().st_size > 16000  # > 0.5s of 16kHz 16-bit mono
