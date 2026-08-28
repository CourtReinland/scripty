"""Genre desks: one 'AI author' per genre, with a style card in our words.

Cards are tropes, beat shapes, and tone notes — never verbatim prose from
a living author's books. Mentions of well-known writers are tone hints,
not a corpus.
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
        "hint": "Quiet dread in ordinary rooms. (A King-ish small-town unease "
                "is a tone hint, not a reading list.)",
        "style_card": (
            "Prefer a recognizable place that starts to refuse the character. "
            "Make the threat specific and physical before it is metaphysical. "
            "Keep sentences close to the body — breath, hands, a door that "
            "should stay shut. Delay explanation. Spend the page on what the "
            "character notices and what they refuse to notice. End a beat on "
            "an unfinished action, not a thesis."
        ),
    },
    {
        "slug": "literary",
        "name": "Literary Desk",
        "hint": "Interior weather, precise objects, unshowy sentences.",
        "style_card": (
            "Let a small social or private pressure carry the plot. Privilege "
            "concrete nouns over abstract mood. Allow dialogue to miss the "
            "point the speaker meant. Do not announce theme. A scene earns "
            "its keep when a relationship cannot return to the previous room."
        ),
    },
    {
        "slug": "science_fiction",
        "name": "Science Fiction Desk",
        "hint": "A changed rule of the world, lived in rather than lectured.",
        "style_card": (
            "Invent one pressure (a law, a scarcity, a tool) and let people "
            "collide with it. Show the world through errands and work, not "
            "tour-guide paragraphs. Keep the speculative idea answerable to "
            "a human cost in this scene. Avoid gadget catalogs."
        ),
    },
    {
        "slug": "fantasy",
        "name": "Fantasy Desk",
        "hint": "Wonder with weight; magic that costs something.",
        "style_card": (
            "Treat the strange as local weather: someone already lives here. "
            "Give wonder a bill — time, loyalty, a body, a name. Prefer one "
            "vivid custom over a map dump. Let the character want something "
            "ordinary (home, a debt paid) inside the marvelous."
        ),
    },
    {
        "slug": "mystery",
        "name": "Mystery Desk",
        "hint": "A question that rearranges the room as facts arrive.",
        "style_card": (
            "Open with a disturbance that cannot be ignored. Plant facts the "
            "reader can hold, not red herrings that insult them. Let each "
            "beat change what a character can safely believe. Clues should "
            "be visible in retrospect. Keep the investigator fallible."
        ),
    },
    {
        "slug": "romance",
        "name": "Romance Desk",
        "hint": "Two people, a real obstacle, and a change in how they speak.",
        "style_card": (
            "Give each person a life that does not exist only for the other. "
            "The obstacle should be a value or a history, not a calendar "
            "glitch. Charge small gestures. Let the turning point be a "
            "choice, not a coincidence. Desire is specific; banter is not "
            "the whole engine."
        ),
    },
)

DESK_BY_SLUG: dict[str, DeskSpec] = {d["slug"]: d for d in DESKS}

LENGTHS = ("short", "medium", "long")

#: prompt mutations — one is rolled every generate so the challenger is
#: a real alternative, not a paraphrase with different adjectives.
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
        "whodunnit": "mystery",
        "whodunit": "mystery",
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
