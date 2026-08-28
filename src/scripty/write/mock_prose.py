"""Seed-driven offline fiction. No corpus, no network, no living authors.

Used by MockBrain so pytest and `scripty write` work without API keys.
Two different seeds must produce different text.
"""
from __future__ import annotations

import random
import re

WRITER_MARKER = "SCRIPTY_WRITER"

_OPENINGS = (
    "The door stuck on the swollen jamb.",
    "Someone had left the radio on in the other room.",
    "She counted the steps from the sink to the window.",
    "The envelope was already open when he found it.",
    "A moth kept hitting the same pane.",
    "The kettle clicked off and nobody moved.",
    "Mud on the hallway runner was still wet.",
    "He wrote the name once, then crossed it out.",
)

_MIDDLES = (
    "Nothing in the room had moved, which was the problem.",
    "The clock on the stove was six minutes fast and they both knew it.",
    "Outside, a car idled, then thought better of it.",
    "She kept her voice at the volume you use for a sleeping child.",
    "He tasted metal and blamed the tap.",
    "The photograph on the fridge had a new crease.",
    "They had agreed not to talk about last winter.",
    "A floorboard answered from the hall.",
)

_CLOSES = (
    "She left the light on anyway.",
    "He put his hand on the knob and did not turn it.",
    "The sentence on the pad stopped in the middle of a verb.",
    "Whatever was in the yard waited for the next sound.",
    "They sat until the ice in the glass finished its work.",
    "The phone lit, then went dark.",
    "She locked the door from the inside, which was new.",
    "He did not look at the window a second time.",
)

_NAMES = (
    "Mara", "Eli", "June", "Harris", "Nia", "Cole", "Ruth", "Abe",
    "Lina", "Theo", "Pat", "Ivy",
)


def is_writer_prompt(system: str, user: str) -> bool:
    blob = f"{system}\n{user}"
    return WRITER_MARKER in blob


def _clip(text: str, start: str, end: str) -> str:
    if start not in text or end not in text:
        return ""
    return text.split(start, 1)[1].split(end, 1)[0].strip()


def mock_fiction(system: str, user: str, *,
                 seed: int | None, temperature: float | None) -> str:
    """Assemble a short original draft that tracks the session spec."""
    rng = random.Random(int(seed) if seed is not None else 0)
    genre = _clip(user, "GENRE:", "\n") or "horror"
    tone = _clip(user, "TONE:", "\n") or "measured"
    length = _clip(user, "LENGTH:", "\n") or "short"
    unit = _clip(user, "UNIT:", "\n") or "story"
    mutation = _clip(user, "MUTATION:", "\n") or "stay close to the body"
    summary = _clip(user, "SUMMARY:", "\nEND_SUMMARY") or "A person waits in a house."
    lessons = _clip(user, "DESK LESSONS:", "\nEND_LESSONS")
    prior = _clip(user, "CHAMPION DRAFT:", "\nEND_CHAMPION")

    a, b = rng.sample(_NAMES, 2)
    opening = rng.choice(_OPENINGS)
    middle = rng.choice(_MIDDLES)
    close = rng.choice(_CLOSES)
    # Guarantee seed uniqueness even if pools collide.
    stamp = f"[{genre}/{int(seed or 0)}]"

    summary_bit = " ".join(summary.split())[:180]
    short_sents = "shorter" in lessons.lower() or "cut" in lessons.lower()

    if prior:
        rewrite = (
            f"{opening} {a} tried the same room again, differently. "
            f"{middle} The last version had leaned the wrong way; this one "
            f"follows the note: {mutation.rstrip('.')}. "
            f"{b} is still in the story, but farther from the explanation. "
            f"{close} {stamp}"
        )
        if short_sents:
            rewrite = rewrite.replace(", ", ". ").replace("; ", ". ")
        return rewrite

    kind = "short story" if "story" in unit else f"{unit}"
    target = {
        "short": "a complete piece",
        "medium": "this chapter",
        "long": "this chapter of a longer work",
    }.get(length, "this draft")

    body = (
        f"{opening} {a} had come here because of the story as given: "
        f"{summary_bit} The tone asked for {tone}, and the desk was {genre}. "
        f"{middle} {b} said something that did not help and did not leave. "
        f"This is {target} — a {kind} — written under the mutation "
        f"“{mutation.rstrip('.')}”. "
    )
    if lessons:
        body += f"The desk remembers: {lessons.splitlines()[0][:160]} "
    body += f"{close} {stamp}"
    if short_sents:
        body = body.replace("; ", ". ")
    return " ".join(body.split())
