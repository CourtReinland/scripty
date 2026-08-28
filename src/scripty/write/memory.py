"""Desk memory: human Better/Worse verdicts become lessons the next draft sees.

Reuses the film stack's idea (retrieve, don't fine-tune) without its
shot-field vocabulary. Lessons are scoped to a genre desk so a horror
verdict does not leak into the literary author.
"""
from __future__ import annotations

from scripty.core.db import Database
from scripty.core.interfaces import TextBrain
from scripty.write import store
from scripty.write.signals import word_count

_DISTILL_SYSTEM = (
    "You write one short imperative lesson for a fiction desk, based on a "
    "human pairwise verdict. The human said Better (challenger wins) or "
    "Worse (champion stays). Do not quote more than a few words of either "
    "draft. Do not name living authors as a corpus. One sentence, no "
    "numbering."
)

_FALLBACK = (
    "When a similar {genre} beat recurs, prefer the draft that {winner} "
    "over the one that {loser}."
)


def _trait(text: str, mutation: str) -> str:
    words = word_count(text)
    mut = (mutation or "stayed closer to the brief").rstrip(".")
    return f"used “{mut}” ({words} words)"


def _fallback_rule(session: dict, champ: dict, chall: dict,
                   result: str) -> str:
    if result == "better":
        winner, loser = _trait(chall["text"], chall.get("mutation") or ""), \
            _trait(champ["text"], champ.get("mutation") or "")
    else:
        winner, loser = _trait(champ["text"], champ.get("mutation") or ""), \
            _trait(chall["text"], chall.get("mutation") or "")
    return _FALLBACK.format(
        genre=session.get("genre") or "this", winner=winner, loser=loser)


def _brain_rule(brain: TextBrain | None, session: dict, champ: dict,
                chall: dict, result: str, note: str) -> str | None:
    if brain is None:
        return None
    user = (
        f"Genre: {session.get('genre')}\nTone: {session.get('tone')}\n"
        f"Verdict: {result}\nHuman note: {note or '(none)'}\n"
        f"Champion mutation: {champ.get('mutation')}\n"
        f"Challenger mutation: {chall.get('mutation')}\n"
        f"Champion words: {word_count(champ.get('text') or '')}\n"
        f"Challenger words: {word_count(chall.get('text') or '')}\n"
        "Write one imperative lesson."
    )
    try:
        line = (brain.complete(_DISTILL_SYSTEM, user) or "").strip()
    except Exception:
        return None
    line = line.splitlines()[0].strip().strip("-* ").strip('"')
    if not line or line.lower().startswith("genre:"):
        return None
    return line[:400]


def record_lesson(db: Database, *, session: dict, champ: dict, chall: dict,
                  verdict_id: int, result: str, note: str,
                  brain: TextBrain | None) -> int:
    """Turn one verdict into a desk lesson (idempotent per verdict)."""
    desk_id = int(session["desk_id"])
    if verdict_id in store.cited_verdict_ids(db, desk_id):
        existing = store.lessons_for_desk(db, desk_id, limit=50)
        for row in existing:
            if verdict_id in (row.get("source_verdict_ids") or []):
                return int(row["id"])
    rule = _brain_rule(brain, session, champ, chall, result, note)
    if not rule:
        rule = _fallback_rule(session, champ, chall, result)
    return store.add_lesson(
        db, desk_id=desk_id, rule=rule, source_verdict_ids=[verdict_id])


def format_lessons(rows: list[dict]) -> str:
    if not rows:
        return ""
    lines = ["DESK LESSONS (from human Better/Worse — honor these):"]
    for row in rows:
        lines.append(f"- {row.get('rule')}")
    lines.append("END_LESSONS")
    return "\n".join(lines)


def format_style_notes(rows: list[dict]) -> str:
    """Abstract notes only. Raw reference sentences never enter a prompt."""
    from scripty.write.style import digest, digest_block

    digests = []
    for row in rows:
        if row.get("kind") not in ("user_excerpt", "public_domain"):
            continue
        text = str(row.get("text") or "").strip()
        if text:
            digests.append(digest(text))
    return digest_block(digests)


def public_refs(rows: list[dict]) -> list[dict]:
    """Safe metadata for UI/API — no text, no excerpts."""
    from scripty.write.style import digest

    out: list[dict] = []
    for row in rows:
        kind = row.get("kind")
        if kind == "style_card":
            continue  # style cards already live on the desk row
        item = {
            "id": row.get("id"),
            "kind": kind,
            "title": row.get("title") or "desk reference",
            "char_count": len(str(row.get("text") or "")),
        }
        text = str(row.get("text") or "").strip()
        if text:
            item["notes"] = digest(text).get("notes") or []
        out.append(item)
    return out
