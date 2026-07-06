#!/usr/bin/env python
"""Build a synthetic multi-shot test film with ffmpeg (no source footage).

Usage:
    python scripts/make_test_film.py out.mp4 --shots 5 --seconds 3.0
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _load_builder():  # type: ignore[no-untyped-def]
    """Import make_test_film, adding src/ to sys.path when not installed."""
    try:
        from scripty.ingest.testfilm import make_test_film
    except ImportError:
        src = Path(__file__).resolve().parents[1] / "src"
        sys.path.insert(0, str(src))
        from scripty.ingest.testfilm import make_test_film
    return make_test_film


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a synthetic multi-shot film (640x360, 24fps, "
                    "distinct look + sine tone per shot).")
    parser.add_argument("out", type=Path, help="output video path (.mp4)")
    parser.add_argument("--shots", type=int, default=5,
                        help="number of shots (default: 5)")
    parser.add_argument("--seconds", type=float, default=3.0,
                        help="seconds per shot (default: 3.0)")
    args = parser.parse_args(argv)

    make_test_film = _load_builder()
    path = make_test_film(args.out, shots=args.shots, shot_seconds=args.seconds)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
