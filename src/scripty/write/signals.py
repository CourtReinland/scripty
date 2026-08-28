"""Cheap, noisy signals for the compare view. They never pick a winner."""
from __future__ import annotations

import re
from typing import Any

_WORD = re.compile(r"[A-Za-z0-9']+")
_DIALOGUE = re.compile(r'"[^"]+"')


def word_count(text: str) -> int:
    return len(_WORD.findall(text or ""))


def _tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _WORD.finditer(text or "")}


def lexical_novelty(challenger: str, champion: str) -> float | None:
    """Jaccard distance of word sets. 0 = identical bag, 1 = no overlap."""
    a, b = _tokens(challenger), _tokens(champion)
    if not a and not b:
        return 0.0
    if not a or not b:
        return 1.0
    inter = len(a & b)
    union = len(a | b)
    return round(1.0 - (inter / union), 3) if union else 0.0


def contrastive_notes(challenger: str, champion: str | None) -> list[str]:
    notes: list[str] = []
    if not champion:
        notes.append("first draft — nothing to contrast yet")
        return notes
    cw, hw = word_count(challenger), word_count(champion)
    if cw > hw + 8:
        notes.append(f"longer by {cw - hw} words")
    elif hw > cw + 8:
        notes.append(f"shorter by {hw - cw} words")
    else:
        notes.append("similar length")
    c_dial = len(_DIALOGUE.findall(challenger))
    h_dial = len(_DIALOGUE.findall(champion))
    if c_dial != h_dial:
        notes.append("different amount of quoted speech")
    c_open = (challenger or "").strip().split("\n", 1)[0][:80]
    h_open = (champion or "").strip().split("\n", 1)[0][:80]
    if c_open != h_open:
        notes.append("different opening line")
    novelty = lexical_novelty(challenger, champion)
    if novelty is not None:
        if novelty < 0.15:
            notes.append("lexically close to the champion")
        elif novelty > 0.55:
            notes.append("lexically far from the champion")
    return notes


def measure(text: str, against: str | None = None) -> dict[str, Any]:
    return {
        "word_count": word_count(text),
        "lexical_novelty": lexical_novelty(text, against) if against else None,
        "notes": contrastive_notes(text, against),
    }
