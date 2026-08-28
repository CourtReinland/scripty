"""Draft generation: champion-or-challenger with explicit per-turn randomness."""
from __future__ import annotations

import inspect
import random
import secrets
from typing import Any

from scripty.core.interfaces import TextBrain
from scripty.write.desks import MUTATIONS
from scripty.write.mock_prose import WRITER_MARKER

SYSTEM = (
    f"{WRITER_MARKER}\n"
    "You are a fiction desk writer. Write original prose for the unit "
    "requested (a complete short piece, or one chapter). Think in plot, "
    "character, tone, and pacing, but do not score yourself against any "
    "living author. Do not reproduce copyrighted novels. If reference "
    "notes are present, absorb rhythm and beat shape only. Honor desk "
    "lessons from prior human verdicts. Output the draft only — no "
    "preface, no bullet analysis."
)


def roll_randomness(rng: random.Random | None = None) -> tuple[int, float, str]:
    """Seed, temperature, and a prompt mutation — all change every generate."""
    rng = rng or random.Random(secrets.randbits(64))
    seed = rng.randint(1, 2_147_483_646)
    temperature = round(0.72 + rng.random() * 0.28, 2)  # 0.72–1.00
    mutation = MUTATIONS[rng.randrange(len(MUTATIONS))]
    return seed, temperature, mutation


def build_prompt(*, genre: str, tone: str, length: str, summary: str,
                 unit_kind: str, unit_index: int, mutation: str,
                 lessons_block: str, refs_block: str,
                 champion_text: str | None,
                 prior_units: list[str] | None = None) -> str:
    parts = [
        f"GENRE: {genre}",
        f"TONE: {tone}",
        f"LENGTH: {length}",
        f"UNIT: {unit_kind} {unit_index}",
        f"MUTATION: {mutation}",
        "SUMMARY:",
        summary.strip(),
        "END_SUMMARY",
    ]
    if lessons_block:
        parts.append(lessons_block)
    if refs_block:
        parts.append(refs_block)
    if prior_units:
        parts.append("PRIOR UNITS (do not repeat; continue):")
        for i, text in enumerate(prior_units, start=1):
            parts.append(f"--- unit {i} ---\n{text[:1500]}")
    if champion_text:
        parts += [
            "CHAMPION DRAFT:",
            champion_text,
            "END_CHAMPION",
            "Write a challenger: same story facts, different execution. "
            "Apply the mutation. Do not copy the champion sentence-for-sentence.",
        ]
    else:
        if unit_kind == "story":
            parts.append("Write a complete short piece that fulfills the summary.")
        else:
            parts.append(f"Write chapter {unit_index} of the work described.")
    return "\n".join(parts)


def complete_brain(brain: TextBrain, system: str, user: str, *,
                   temperature: float, seed: int,
                   max_tokens: int = 4000) -> str:
    """Call TextBrain.complete, passing sampling knobs when supported."""
    kwargs: dict[str, Any] = {}
    try:
        sig = inspect.signature(brain.complete)
        params = sig.parameters
        if "temperature" in params:
            kwargs["temperature"] = temperature
        if "seed" in params:
            kwargs["seed"] = seed
        if "max_tokens" in params:
            kwargs["max_tokens"] = max_tokens
    except (TypeError, ValueError):
        kwargs = {}
    text = brain.complete(system, user, **kwargs) if kwargs \
        else brain.complete(system, user)
    return (text or "").strip()
