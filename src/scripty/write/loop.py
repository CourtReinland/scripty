"""The human-in-the-loop writer: session → draft → challenger → Better/Worse."""
from __future__ import annotations

from typing import Any

from scripty.brain import get_brain
from scripty.core import config
from scripty.core.db import Database
from scripty.core.interfaces import TextBrain
from scripty.write import memory, store
from scripty.write.desks import (
    normalize_genre, normalize_length, unit_kind_for_length,
)
from scripty.write.generate import SYSTEM, build_prompt, complete_brain, roll_randomness
from scripty.write.signals import measure
from scripty.write.style import contrast_to_digest, digest, scrub_draft


def _brain(name: str | None) -> TextBrain:
    """Prose brain: mock for tests/offline; otherwise grok-4.6 + xhigh.

    Anthropic is never used for champion/challenger generation.
    """
    resolved = (name or config.default_writer_provider() or "mock").strip().lower()
    if resolved in ("mock", "none", ""):
        return get_brain("mock")
    try:
        return get_brain("xai")
    except Exception:
        return get_brain("mock")


def _session_or_raise(db: Database, session_id: int) -> dict:
    row = store.get_session(db, session_id)
    if row is None:
        raise ValueError(f"no write session {session_id}")
    return row


def _draft_or_raise(db: Database, draft_id: int | None) -> dict:
    if not draft_id:
        raise ValueError("draft id required")
    row = store.get_draft(db, int(draft_id))
    if row is None:
        raise ValueError(f"no draft {draft_id}")
    return row


def start_session(db: Database, *, genre: str, tone: str, length: str,
                  summary: str, provider: str | None = None,
                  reference_text: str | None = None,
                  reference_title: str | None = None,
                  reference_kind: str = "user_excerpt") -> dict:
    """Create a session on the matching genre desk. Does not generate yet."""
    summary = (summary or "").strip()
    if not summary:
        raise ValueError("summary is required")
    tone = (tone or "measured").strip() or "measured"
    genre_slug = normalize_genre(genre)
    length_slug = normalize_length(length)
    desk = store.desk_by_slug(db, genre_slug)
    provider_name = (provider or config.default_writer_provider()).strip() or "mock"
    session_id = store.create_session(
        db, desk_id=int(desk["id"]), genre=genre_slug, tone=tone,
        length=length_slug, summary=summary,
        unit_kind=unit_kind_for_length(length_slug),
        provider=provider_name)
    if (reference_text or "").strip():
        add_reference(
            db, desk_slug=genre_slug, kind=reference_kind,
            title=reference_title or "session upload",
            text=reference_text or "")
    return session_view(db, session_id)


def _prior_unit_texts(db: Database, session: dict) -> list[str]:
    current = int(session["unit_index"])
    if current <= 1:
        return []
    texts: list[str] = []
    for unit in range(1, current):
        champs = [d for d in store.drafts_for_session(db, int(session["id"]), unit)
                  if d.get("role") == "champion"]
        if champs:
            texts.append(champs[-1]["text"])
    return texts


def generate(db: Database, session_id: int, *,
             seed: int | None = None,
             temperature: float | None = None,
             mutation: str | None = None,
             brain: TextBrain | None = None) -> dict:
    """First draft (becomes champion) or an exploring challenger.

    Randomness is rolled every call unless the caller pins knobs (tests).
    Challengers use a noisier temperature and a fresh plan+prose mutation.
    """
    session = _session_or_raise(db, session_id)
    desk = store.desk_by_slug(db, session["genre"])
    prior = store.drafts_for_session(db, session_id, int(session["unit_index"]))
    avoid_seeds = {int(d["seed"]) for d in prior if d.get("seed") is not None}
    avoid_muts = {str(d["mutation"]) for d in prior if d.get("mutation")}
    champ_id = session.get("champion_id")
    champ = store.get_draft(db, int(champ_id)) if champ_id else None
    rolled_seed, rolled_temp, rolled_mut = roll_randomness(
        explore=champ is not None,
        avoid_seeds=avoid_seeds, avoid_mutations=avoid_muts)
    seed = rolled_seed if seed is None else int(seed)
    temperature = rolled_temp if temperature is None else float(temperature)
    mutation = rolled_mut if mutation is None else str(mutation)

    lessons = store.lessons_for_desk(db, int(desk["id"]))
    refs = store.refs_for_desk(db, int(desk["id"]))
    user_refs = [r for r in refs if r.get("kind") in ("user_excerpt", "public_domain")]
    style_block = memory.format_style_notes(user_refs)
    user = build_prompt(
        genre=session["genre"], tone=session["tone"],
        length=session["length"], summary=session["summary"],
        unit_kind=session["unit_kind"], unit_index=int(session["unit_index"]),
        mutation=mutation,
        lessons_block=memory.format_lessons(lessons),
        style_block=style_block,
        champion_text=champ["text"] if champ else None,
        prior_units=_prior_unit_texts(db, session),
    )
    writer = brain or _brain(session.get("provider"))
    text = complete_brain(writer, SYSTEM, user,
                          temperature=temperature, seed=seed)
    if not text:
        raise RuntimeError("writer produced an empty draft")
    private = store.user_ref_texts(db, int(desk["id"]))
    text = scrub_draft(text, private)

    against = champ["text"] if champ else None
    signals = measure(text, against)
    if user_refs:
        target = digest(user_refs[-1]["text"])
        signals["notes"] = list(signals.get("notes") or []) + contrast_to_digest(
            text, target)
    pending = session.get("challenger_id")
    if pending:
        store.set_draft_role(db, int(pending), "superseded")

    if champ is None:
        draft_id = store.add_draft(
            db, session_id=session_id, unit_index=int(session["unit_index"]),
            role="champion", text=text, seed=seed, temperature=temperature,
            mutation=mutation, parent_id=None, signals=signals)
        store.update_session(db, session_id, champion_id=draft_id,
                             challenger_id=None)
    else:
        draft_id = store.add_draft(
            db, session_id=session_id, unit_index=int(session["unit_index"]),
            role="challenger", text=text, seed=seed, temperature=temperature,
            mutation=mutation, parent_id=int(champ["id"]), signals=signals)
        store.update_session(db, session_id, challenger_id=draft_id)
    return session_view(db, session_id)


def judge(db: Database, session_id: int, result: str, *,
          note: str = "", brain: TextBrain | None = None) -> dict:
    """Human is the only judge. better → promote; worse → discard."""
    result = (result or "").strip().lower()
    if result not in ("better", "worse"):
        raise ValueError("result must be 'better' or 'worse'")
    session = _session_or_raise(db, session_id)
    champ = _draft_or_raise(db, session.get("champion_id"))
    chall = _draft_or_raise(db, session.get("challenger_id"))
    verdict_id = store.add_verdict(
        db, session_id=session_id, desk_id=int(session["desk_id"]),
        champion_id=int(champ["id"]), challenger_id=int(chall["id"]),
        result=result, note=note or "")
    if result == "better":
        store.set_draft_role(db, int(champ["id"]), "retired")
        store.set_draft_role(db, int(chall["id"]), "champion")
        db.update("write_drafts", int(chall["id"]),
                  signals=measure(chall["text"], None))
        store.update_session(db, session_id, champion_id=int(chall["id"]),
                             challenger_id=None)
    else:
        store.set_draft_role(db, int(chall["id"]), "discarded")
        store.update_session(db, session_id, challenger_id=None)
    writer = brain or _brain(session.get("provider"))
    memory.record_lesson(
        db, session=session, champ=champ, chall=chall,
        verdict_id=verdict_id, result=result, note=note, brain=writer)
    return session_view(db, session_id)


def advance(db: Database, session_id: int, **generate_kwargs: Any) -> dict:
    """Start the next chapter (medium/long). Short stories stay on unit 1."""
    session = _session_or_raise(db, session_id)
    if session["unit_kind"] == "story":
        raise ValueError("short sessions are a single piece; no next chapter")
    if not session.get("champion_id"):
        raise ValueError("need a champion before advancing")
    if session.get("challenger_id"):
        raise ValueError("judge the pending challenger before advancing")
    store.update_session(
        db, session_id, unit_index=int(session["unit_index"]) + 1,
        champion_id=None, challenger_id=None)
    return generate(db, session_id, **generate_kwargs)


def add_reference(db: Database, *, desk_slug: str, kind: str,
                  title: str, text: str) -> dict:
    """Store a user-provided chunk. Response never echoes the text."""
    kind = (kind or "").strip().lower()
    if kind not in ("user_excerpt", "public_domain"):
        raise ValueError("kind must be user_excerpt or public_domain")
    text = (text or "").strip()
    if not text:
        raise ValueError("reference text is empty")
    if len(text) > 40_000:
        text = text[:40_000]
    desk = store.desk_by_slug(db, normalize_genre(desk_slug))
    ref_id = store.add_ref(
        db, desk_id=int(desk["id"]), kind=kind,
        title=(title or kind).strip() or kind, text=text)
    pub = memory.public_refs([store.refs_for_desk(db, int(desk["id"]))[-1]])
    return {"id": ref_id, "desk": desk["slug"], "kind": kind,
            **(pub[0] if pub else {})}


def desk_view(db: Database, genre: str) -> dict:
    desk = store.desk_by_slug(db, normalize_genre(genre))
    desk_id = int(desk["id"])
    refs = memory.public_refs(store.refs_for_desk(db, desk_id))
    history = store.champions_for_desk(db, desk_id)
    for row in history:
        row.pop("text", None)
        sig = row.get("signals") or {}
        row["word_count"] = sig.get("word_count")
        row["signals"] = {"word_count": sig.get("word_count"),
                          "notes": sig.get("notes") or []}
    return {
        "desk": desk,
        "lessons": store.lessons_for_desk(db, desk_id),
        "history": history,
        "refs": refs,
    }


def session_view(db: Database, session_id: int) -> dict:
    session = _session_or_raise(db, session_id)
    desk = db.row("SELECT * FROM write_desks WHERE id = ?",
                  (int(session["desk_id"]),))
    champ = store.get_draft(db, int(session["champion_id"])) \
        if session.get("champion_id") else None
    chall = store.get_draft(db, int(session["challenger_id"])) \
        if session.get("challenger_id") else None
    history = [
        d for d in store.drafts_for_session(
            db, session_id, int(session["unit_index"]))
        if d.get("role") in ("champion", "retired")
    ]
    desk_id = int(session["desk_id"])
    return {
        "session": session,
        "desk": desk,
        "champion": champ,
        "challenger": chall,
        "history": history,
        "desk_history": store.champions_for_desk(db, desk_id),
        "lessons": store.lessons_for_desk(db, desk_id),
        "refs": memory.public_refs(store.refs_for_desk(db, desk_id)),
        "signals_are_advisory": True,
    }
