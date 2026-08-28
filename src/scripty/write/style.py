"""Abstract style notes from private reference text.

Reference chunks stay on disk for the desk. Callers may use these notes
to shape a challenger. Raw sentences never go to the UI, drafts, or logs.
"""
from __future__ import annotations

import re
from typing import Any

from scripty.write.signals import word_count

_WORD = re.compile(r"[A-Za-z0-9']+")
_SENT = re.compile(r"(?<=[.!?])\s+")
_DIALOGUE = re.compile(r'"[^"]+"')


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT.split((text or "").strip()) if s.strip()]


def _words(text: str) -> list[str]:
    return [m.group(0).lower() for m in _WORD.finditer(text or "")]


def opening_move(text: str) -> str:
    first = (_sentences(text) or [""])[0].lstrip()
    if first.startswith('"') or first.startswith("“"):
        return "opens on speech"
    if re.match(r"^(I|We|He|She|They|A|An|The)\b", first):
        return "opens on a person or object"
    return "opens on a situation"


def digest(text: str) -> dict[str, Any]:
    """Cheap features spoken only in our words — never a quotation."""
    words = _words(text)
    sents = _sentences(text)
    n = max(len(words), 1)
    avg = (len(words) / max(len(sents), 1))
    if avg < 10:
        pace = "short-sentenced"
    elif avg > 22:
        pace = "long-lined"
    else:
        pace = "medium-paced"
    dial = sum(len(_WORD.findall(m.group(0))) for m in _DIALOGUE.finditer(text or ""))
    variety = len(set(words)) / n
    notes = [
        f"{pace} (about {avg:.0f} words a sentence)",
        "more quoted speech" if dial / n > 0.12 else "little quoted speech",
        "varied diction" if variety > 0.55 else "tighter diction",
        opening_move(text),
    ]
    return {
        "word_count": word_count(text),
        "avg_sentence_words": round(avg, 1),
        "dialogue_share": round(dial / n, 3),
        "lexical_variety": round(variety, 3),
        "pace": pace,
        "opening_move": opening_move(text),
        "notes": notes,
    }


def digest_block(digests: list[dict[str, Any]]) -> str:
    if not digests:
        return ""
    lines = [
        "STYLE NOTES from private desk references (abstract only —",
        "do not quote, paraphrase closely, or invent a source author):",
    ]
    for i, d in enumerate(digests, start=1):
        bits = d.get("notes") or []
        lines.append(f"- ref {i}: " + "; ".join(bits))
    lines.append("END_STYLE_NOTES")
    return "\n".join(lines)


def contrast_to_digest(draft: str, target: dict[str, Any] | None) -> list[str]:
    """Advisory notes in our words. Never a citation or excerpt."""
    if not target:
        return []
    here = digest(draft)
    notes: list[str] = []
    if here["pace"] != target.get("pace"):
        notes.append(f"pacing {here['pace']} vs stored {target.get('pace')}")
    else:
        notes.append(f"pacing near stored {here['pace']}")
    return notes


def ngrams(text: str, n: int = 6) -> set[tuple[str, ...]]:
    words = _words(text)
    if len(words) < n:
        return set()
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def overlapping_span(draft: str, sources: list[str], n: int = 6) -> bool:
    draft_grams = ngrams(draft, n)
    if not draft_grams:
        return False
    for src in sources:
        if draft_grams & ngrams(src, n):
            return True
    return False


def scrub_draft(draft: str, sources: list[str], n: int = 6) -> str:
    """Drop any 6-word span that also appears in a private reference."""
    if not draft or not sources:
        return draft
    banned: set[tuple[str, ...]] = set()
    for src in sources:
        banned |= ngrams(src, n)
    if not banned:
        return draft
    words = list(_WORD.finditer(draft))
    drop: set[int] = set()
    tokens = [m.group(0).lower() for m in words]
    for i in range(len(tokens) - n + 1):
        gram = tuple(tokens[i:i + n])
        if gram in banned:
            drop.update(range(i, i + n))
    if not drop:
        return draft
    keep: list[str] = []
    last = 0
    for i, match in enumerate(words):
        if i in drop:
            if last < match.start():
                keep.append(draft[last:match.start()])
            last = match.end()
            continue
        keep.append(draft[last:match.end()])
        last = match.end()
    keep.append(draft[last:])
    cleaned = re.sub(r"\s+", " ", "".join(keep)).strip()
    return cleaned or draft
