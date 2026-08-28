"""Genre desks: one 'AI author' per genre, with a style card in our words.

Cards are tropes, beat shapes, and tone notes — never verbatim prose.
"""
from __future__ import annotations

from typing import TypedDict


class DeskSpec(TypedDict):
    slug: str
    name: str
    hint: str
    style_card: str


DESKS: tuple[DeskSpec, ...] = (
    {
        "slug": "horror",
        "name": "Short Horror Desk",
        "hint": "Quiet dread in ordinary rooms.",
        "style_card": (
            "Prefer a recognizable place that starts to refuse the character. "
            "Make the threat specific and physical before it is metaphysical. "
            "Keep sentences close to the body. Delay explanation. End a beat "
            "on an unfinished action, not a thesis."
        ),
    },
    {
        "slug": "literary",
        "name": "Literary Desk",
        "hint": "Interior weather, precise objects, unshowy sentences.",
        "style_card": (
            "Let a small social or private pressure carry the plot. Privilege "
            "concrete nouns over abstract mood. Allow dialogue to miss the "
            "point the speaker meant. A scene earns its keep when a "
            "relationship cannot return to the previous room."
        ),
    },
    {
        "slug": "romance",
        "name": "Romance Desk",
        "hint": "Two people, a real obstacle, and a change in how they speak.",
        "style_card": (
            "Give each person a life that does not exist only for the other. "
            "The obstacle should be a value or a history, not a calendar "
            "glitch. Charge small gestures. Let the turning point be a choice."
        ),
    },
    {
        "slug": "thriller",
        "name": "Thriller Desk",
        "hint": "A clock, a pursuit, and information that arrives too late.",
        "style_card": (
            "Start after the mistake. Keep the next danger closer than the "
            "explanation. Trade information for time. Let competence fail in "
            "a specific way. End a beat on a door that should not open."
        ),
    },
    {
        "slug": "slice_of_life",
        "name": "Slice-of-Life Desk",
        "hint": "Ordinary hours that still change someone.",
        "style_card": (
            "Stay with errands, weather, and the sentence someone almost says. "
            "Plot is a shift in what can be said at the table. Prefer one "
            "true object over a speech. Do not force a twist."
        ),
    },
    {
        "slug": "science_fiction",
        "name": "Science Fiction Desk",
        "hint": "A changed rule of the world, lived in rather than lectured.",
        "style_card": (
            "Invent one pressure and let people collide with it. Show the "
            "world through work, not a tour. Keep the idea answerable to a "
            "human cost in this scene."
        ),
    },
    {
        "slug": "custom",
        "name": "Generic / Custom Desk",
        "hint": "No house style — learn only from your verdicts and uploads.",
        "style_card": (
            "Honor the session tone and summary. Prefer concrete scenes. "
            "Do not imitate a named living author. Learn from this desk's "
            "human Better/Worse record."
        ),
    },
    {
        "slug": "fantasy",
        "name": "Fantasy Desk",
        "hint": "Wonder with weight; magic that costs something.",
        "style_card": (
            "Treat the strange as local weather. Give wonder a bill. Prefer "
            "one vivid custom over a map dump."
        ),
    },
    {
        "slug": "mystery",
        "name": "Mystery Desk",
        "hint": "A question that rearranges the room as facts arrive.",
        "style_card": (
            "Open with a disturbance. Plant facts the reader can hold. Let "
            "each beat change what a character can safely believe."
        ),
    },
)

DESK_BY_SLUG: dict[str, DeskSpec] = {d["slug"]: d for d in DESKS}

LENGTHS = ("short", "medium", "long")

#: prose-level mutations — one is rolled every generate
MUTATIONS: tuple[str, ...] = (
    "Open on a concrete object, not weather or time of day.",
    "Slow the first paragraph; delay the reveal by a few breaths.",
    "Tighten dialogue; cut one explanation the character would not say.",
    "Add one sensory contradiction (warm light, cold air).",
    "End the unit on an unfinished physical action.",
    "Move closer to one character's body — hands, throat, stance.",
    "Cut a metaphor; prefer a verb that does the work.",
    "Raise the cost of staying in this room.",
    "Let a secondary presence (neighbor, animal, radio) interrupt once.",
    "Swap the order: consequence first, then the choice that caused it.",
)

#: beat-plan mutations — how the system explores a genre it does not know yet
PLAN_MOVES: tuple[str, ...] = (
    "Start later; skip the walk-up to the room.",
    "Hold one character's limited knowledge; no omniscient aside.",
    "Want, then obstacle, then a small irreversible cost.",
    "Let the first spoken line be about the wrong subject.",
    "Compress the middle; spend words on the last image.",
    "Widen to a second location for one paragraph, then return.",
    "Keep time linear; no flashback this pass.",
    "Tell it closer to dusk than to explanation.",
)


def unit_kind_for_length(length: str) -> str:
    """Short fiction is a complete piece; longer work is chapter-by-chapter."""
    return "story" if length.strip().lower() == "short" else "chapter"


def normalize_genre(genre: str) -> str:
    slug = (genre or "").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "sci_fi": "science_fiction",
        "scifi": "science_fiction",
        "sf": "science_fiction",
        "lit": "literary",
        "scary": "horror",
        "short_horror": "horror",
        "whodunnit": "mystery",
        "whodunit": "mystery",
        "generic": "custom",
        "general": "custom",
        "other": "custom",
        "sliceoflife": "slice_of_life",
    }
    slug = aliases.get(slug, slug)
    if slug not in DESK_BY_SLUG:
        known = ", ".join(d["slug"] for d in DESKS)
        raise ValueError(f"unknown genre {genre!r}; choose one of: {known}")
    return slug


def normalize_length(length: str) -> str:
    value = (length or "short").strip().lower()
    aliases = {
        "flash": "short", "story": "short", "novelette": "medium",
        "novella": "medium", "novel": "long", "book": "long",
        "chapter": "medium",
    }
    value = aliases.get(value, value)
    if value not in LENGTHS:
        raise ValueError(f"unknown length {length!r}; choose short, medium, or long")
    return value
