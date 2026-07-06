"""Synthetic multi-shot test film generator (pure ffmpeg lavfi).

Each shot gets a visually distinct look (testsrc2 / solid color with a
drawtext SHOT label / smptebars / gradients) and a sine tone at a distinct
frequency, so cut detection and audio extraction have real material to chew
on — fully offline, no source footage needed.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from . import ffmpeg as _ff

_SIZE = "640x360"
_RATE = 24
_COLORS = ("0x28502E", "0x7A1F2B", "0x1F3A5F", "0x6B5B1E", "0x4A2A6A")


def _shot_source(i: int) -> tuple[str, str]:
    """Return (lavfi video source, optional -vf filter) for shot ``i``."""
    kind = i % 4
    if kind == 0:
        return f"testsrc2=size={_SIZE}:rate={_RATE}", ""
    if kind == 1:
        color = _COLORS[i % len(_COLORS)]
        vf = (f"drawtext=text='SHOT {i + 1}':fontsize=72:fontcolor=white:"
              "x=(w-text_w)/2:y=(h-text_h)/2")
        return f"color=c={color}:size={_SIZE}:rate={_RATE}", vf
    if kind == 2:
        return f"smptebars=size={_SIZE}:rate={_RATE}", ""
    return f"gradients=size={_SIZE}:rate={_RATE}", ""


def _build_segment(i: int, seconds: float, dest: Path) -> None:
    """Encode one shot segment; retry without drawtext if fonts are missing."""
    vsrc, vf = _shot_source(i)
    freq = 220 + 130 * i
    head = ["ffmpeg", "-hide_banner", "-nostdin", "-y",
            "-f", "lavfi", "-i", vsrc,
            "-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=48000",
            "-t", f"{seconds:.3f}"]
    tail = ["-r", str(_RATE), "-pix_fmt", "yuv420p",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac", "-b:a", "96k",
            str(dest)]
    try:
        _ff._run(head + (["-vf", vf] if vf else []) + tail)
    except RuntimeError:
        if not vf:
            raise
        _ff._run(head + tail)  # font machinery unavailable: plain solid color


def make_test_film(out: Path, shots: int = 5, shot_seconds: float = 3.0) -> Path:
    """Build a synthetic multi-shot film (640x360, yuv420p, 24fps) at ``out``.

    Segments are encoded identically and joined with the concat demuxer so
    the result has clean, continuous timestamps.
    """
    if shots < 1:
        raise ValueError("shots must be >= 1")
    if shot_seconds <= 0:
        raise ValueError("shot_seconds must be > 0")
    out_path = Path(out).expanduser().resolve()
    if not out_path.suffix:
        out_path = out_path.with_suffix(".mp4")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="scripty-testfilm-") as tmp:
        tmpdir = Path(tmp)
        segments: list[Path] = []
        for i in range(shots):
            seg = tmpdir / f"seg{i:03d}.mp4"
            _build_segment(i, shot_seconds, seg)
            segments.append(seg)
        concat = tmpdir / "concat.txt"
        concat.write_text(
            "".join(f"file '{p.as_posix()}'\n" for p in segments),
            encoding="utf-8")
        _ff._run(["ffmpeg", "-hide_banner", "-nostdin", "-y",
                  "-f", "concat", "-safe", "0", "-i", str(concat),
                  "-c", "copy", str(out_path)])

    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise RuntimeError(f"test film was not created: {out_path}")
    return out_path
