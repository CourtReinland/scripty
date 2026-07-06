"""Correction-driven learning: record fixes, recall them into prompts,
measure pass-over-pass agreement, and distill corrections into lessons.
"""
from __future__ import annotations

from . import memory  # noqa: F401  (pipeline/server address learn.memory.*)
from .distill import DISTILL_SYSTEM, distill
from .memory import agreement_metrics, recall, record_correction

__all__ = [
    "DISTILL_SYSTEM",
    "agreement_metrics",
    "distill",
    "memory",
    "recall",
    "record_correction",
]
