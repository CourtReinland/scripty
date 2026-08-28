"""Human-in-the-loop fiction trainer: pairwise drafts, human as only judge."""
from __future__ import annotations

from .desks import DESKS, MUTATIONS, normalize_genre, normalize_length
from .loop import (
    add_reference, advance, desk_view, generate, judge, session_view,
    start_session,
)
from .store import list_desks, list_sessions

__all__ = [
    "DESKS",
    "MUTATIONS",
    "add_reference",
    "advance",
    "desk_view",
    "generate",
    "judge",
    "list_desks",
    "list_sessions",
    "normalize_genre",
    "normalize_length",
    "session_view",
    "start_session",
]
