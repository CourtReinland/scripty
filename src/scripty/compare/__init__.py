"""Compare module: original-vs-generated video metrics, critique, re-prompting."""
from __future__ import annotations

from scripty.compare.video import (
    compare_videos,
    critique_shots,
    revise_prompts,
    run_comparison,
)

__all__ = [
    "compare_videos",
    "critique_shots",
    "revise_prompts",
    "run_comparison",
]
