"""ffmpeg/ffprobe wrappers: probing, cut detection, keyframe & audio extraction.

Everything shells out to the ffmpeg binaries on PATH (no third-party
bindings). All subprocess traffic goes through the single seam ``_run`` so
tests can mock one function and stay fully offline.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from scripty.core import config
from scripty.core.models import MediaInfo

_PTS_TIME = re.compile(r"pts_time:\s*(\d+(?:\.\d+)?)")


# --------------------------------------------------------------------------- #
# internals
# --------------------------------------------------------------------------- #

def _run(cmd: list[str], *, timeout: float = 600.0) -> subprocess.CompletedProcess[str]:
    """Run one ffmpeg/ffprobe command; raise RuntimeError on failure."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError(f"executable not found: {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{cmd[0]} timed out after {timeout}s") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-2000:]
        raise RuntimeError(f"{cmd[0]} failed (exit {proc.returncode}): {tail}")
    return proc


def _validate_input(video: Path) -> Path:
    """Resolve a user-supplied media path and require it to be a real file."""
    path = Path(video).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"video file not found: {path}")
    return path


def _to_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _fraction(text: str) -> float:
    """Parse ffprobe rate strings like '24000/1001' or '24'."""
    text = (text or "").strip()
    if not text:
        return 0.0
    if "/" in text:
        num_s, _, den_s = text.partition("/")
        num, den = _to_float(num_s), _to_float(den_s)
        return num / den if den else 0.0
    return _to_float(text)


def _frame_fractions(per_shot: int) -> list[float]:
    """Positions within a shot: 15%..85%, evenly spaced (50% for a single frame)."""
    if per_shot <= 0:
        return []
    if per_shot == 1:
        return [0.5]
    step = 0.70 / (per_shot - 1)
    return [0.15 + i * step for i in range(per_shot)]


# --------------------------------------------------------------------------- #
# public API (pinned)
# --------------------------------------------------------------------------- #

def probe(video: Path) -> MediaInfo:
    """Inspect a media file via ``ffprobe -print_format json``."""
    path = _validate_input(video)
    proc = _run(["ffprobe", "-v", "error", "-print_format", "json",
                 "-show_format", "-show_streams", str(path)])
    try:
        data = json.loads(proc.stdout or "{}")
    except ValueError as exc:
        raise RuntimeError(f"unparseable ffprobe output for {path}") from exc
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    vstream = next((s for s in streams if s.get("codec_type") == "video"), {})
    astream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = _to_float(fmt.get("duration")) or _to_float(vstream.get("duration"))
    fps = (_fraction(str(vstream.get("r_frame_rate", "")))
           or _fraction(str(vstream.get("avg_frame_rate", ""))))
    return MediaInfo(
        path=str(path),
        duration=duration,
        fps=fps,
        width=int(vstream.get("width") or 0),
        height=int(vstream.get("height") or 0),
        vcodec=str(vstream.get("codec_name") or ""),
        has_audio=astream is not None,
        acodec=str((astream or {}).get("codec_name") or ""),
    )


def detect_cuts(video: Path, threshold: float = config.SCENE_THRESHOLD) -> list[float]:
    """Detect hard cuts with ffmpeg's scene filter; return cut times in seconds.

    Cuts closer than ``config.MIN_SHOT_SECONDS`` to the previous kept cut
    (or to t=0) are dropped so downstream shots stay usable.
    """
    path = _validate_input(video)
    vf = f"select='gt(scene,{threshold})',metadata=print:file=-"
    proc = _run(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(path),
                 "-vf", vf, "-an", "-f", "null", "-"])
    times = sorted({float(m.group(1))
                    for m in _PTS_TIME.finditer(proc.stdout + "\n" + proc.stderr)})
    min_gap = config.MIN_SHOT_SECONDS
    cuts: list[float] = []
    prev = 0.0
    for t in times:
        if t - prev < min_gap:
            continue
        cuts.append(t)
        prev = t
    return cuts


def cuts_to_shots(cuts: list[float], duration: float) -> list[tuple[float, float]]:
    """Turn cut times into [start, end) shot spans covering [0, duration)."""
    if duration <= 0:
        return []
    bounds = [0.0]
    for cut in sorted(cuts):
        if 0.0 < cut < duration:
            bounds.append(cut)
    bounds.append(duration)
    return [(a, b) for a, b in zip(bounds, bounds[1:]) if b - a > 1e-9]


def extract_keyframes(video: Path, shots: list[tuple[float, float]], out_dir: Path,
                      per_shot: int = config.FRAMES_PER_SHOT) -> list[list[Path]]:
    """Grab representative JPEG frames (15%/50%/85%) for each shot.

    Files are named ``shot{idx:04d}_f{n}.jpg``, quality ~q:v 3, scaled to a
    max width of 960. ``-ss`` precedes ``-i`` for fast seeking.
    """
    path = _validate_input(video)
    dest = Path(out_dir).expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)
    fractions = _frame_fractions(per_shot)
    result: list[list[Path]] = []
    for idx, (start, end) in enumerate(shots):
        span = max(end - start, 0.0)
        frames: list[Path] = []
        for n, frac in enumerate(fractions):
            t = start + frac * span
            if span > 0.1:
                t = min(t, end - 0.05)
            t = max(t, 0.0)
            frame_path = dest / f"shot{idx:04d}_f{n}.jpg"
            _run(["ffmpeg", "-hide_banner", "-nostdin", "-y",
                  "-ss", f"{t:.3f}", "-i", str(path),
                  "-frames:v", "1", "-q:v", "3",
                  "-vf", "scale='min(960,iw)':-2",
                  str(frame_path)])
            frames.append(frame_path)
        result.append(frames)
    return result


def extract_audio(video: Path, out_wav: Path) -> Path | None:
    """Extract the audio track as 16kHz mono pcm_s16le WAV.

    Returns None (and runs no extraction) when probing reports no audio.
    """
    path = _validate_input(video)
    info = probe(path)
    if not info.has_audio:
        return None
    dest = Path(out_wav).expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", str(path),
          "-vn", "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le",
          str(dest)])
    return dest
