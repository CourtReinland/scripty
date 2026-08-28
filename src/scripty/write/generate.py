"""Draft generation: champion-or-challenger with explicit exploration noise."""
from __future__ import annotations

import inspect
import random
import secrets
from typing import Any

from scripty.core.interfaces import TextBrain
from scripty.write.desks import MUTATIONS, PLAN_MOVES
from scripty.write.mock_prose import WRITER_MARKER

SYSTEM = (
    f"{WRITER_MARKER}\n"
    "You are a fiction desk writer. Write original prose for the unit "
    "requested (a complete short piece, or one chapter). Think in plot, "
    "character, tone, and pacing. Do not score yourself against any living "
    "author and do not reproduce anyone else's sentences. If STYLE NOTES "
    "are present they are abstract (pace, speech, opening move) — absorb "
    "them; never quote a source. Honor desk lessons from human verdicts. "
    "Output the draft only — no preface, no analysis, no citations."
)


def roll_randomness(
    rng: random.Random | None = None, *,
    explore: bool = False,
    avoid_seeds: set[int] | None = None,
    avoid_mutations: set[str] | None = None,
) -> tuple[int, float, str]:
    """Seed, temperature, and a combined plan+prose mutation.

    Challenger generates *explore*: higher temperature and a mutation the
    desk has not just tried. This is how the system searches a genre it
    does not yet understand — not decoration.
    """
    rng = rng or random.Random(secrets.randbits(64))
    avoid_seeds = avoid_seeds or set()
    seed = rng.randint(1, 2_147_483_646)
    for _ in range(8):
        if seed not in avoid_seeds:
            break
        seed = rng.randint(1, 2_147_483_646)
    if explore:
        temperature = round(0.86 + rng.random() * 0.14, 2)  # 0.86–1.00
    else:
        temperature = round(0.70 + rng.random() * 0.18, 2)  # 0.70–0.88
    prose = _pick(rng, MUTATIONS, avoid_mutations)
    plan = _pick(rng, PLAN_MOVES, avoid_mutations)
    mutation = f"{prose} / {plan}"
    if avoid_mutations and mutation in avoid_mutations:
        mutation = f"{_pick(rng, MUTATIONS, None)} / {_pick(rng, PLAN_MOVES, None)}"
    return seed, temperature, mutation


def _pick(rng: random.Random, pool: tuple[str, ...],
          avoid: set[str] | None) -> str:
    choices = [p for p in pool if not avoid or p not in avoid]
    return rng.choice(choices or list(pool))


def build_prompt(*, genre: str, tone: str, length: str, summary: str,
                 unit_kind: str, unit_index: int, mutation: str,
                 lessons_block: str, style_block: str,
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
    if style_block:
        parts.append(style_block)
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
            "Apply the mutation and beat-plan change. This is exploration, "
            "not a polish pass. Do not copy the champion sentence-for-sentence.",
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
