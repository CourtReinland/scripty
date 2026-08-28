"""Offline tests for the human-in-the-loop writer. No API keys, no corpus."""
from __future__ import annotations

from pathlib import Path

import pytest

from scripty.brain import MockBrain
from scripty.core.db import Database
from scripty.write import (
    add_reference, advance, generate, judge, list_desks, start_session,
)
from scripty.write.desks import DESKS, normalize_genre
from scripty.write.mock_prose import mock_fiction
from scripty.write.signals import lexical_novelty, measure


@pytest.fixture()
def db(tmp_path: Path):
    database = Database(tmp_path / "write.db")
    yield database
    database.close()


class RecordingBrain:
    name = "recording"

    def __init__(self, text: str = "A recording-brain paragraph about the stairs."):
        self.text = text
        self.calls: list[dict] = []

    def complete(self, system: str, user: str, *,
                 temperature: float | None = None,
                 seed: int | None = None,
                 max_tokens: int | None = None) -> str:
        self.calls.append({
            "system": system, "user": user, "temperature": temperature,
            "seed": seed, "max_tokens": max_tokens,
        })
        return f"{self.text} seed={seed} temp={temperature}"


def _start(db, **over):
    body = dict(genre="horror", tone="quiet dread", length="short",
                summary="A keeper finds smaller footprints on the lighthouse stairs.")
    body.update(over)
    return start_session(db, **body)


def test_desks_are_style_cards_not_a_corpus(db):
    desks = list_desks(db)
    slugs = {d["slug"] for d in desks}
    assert slugs == {d["slug"] for d in DESKS}
    blob = " ".join(d["style_card"] for d in desks)
    assert "Carrie" not in blob
    assert "The Shining" not in blob
    assert len(blob) < 4000


def test_first_draft_becomes_champion(db):
    view = _start(db, provider="mock")
    view = generate(db, int(view["session"]["id"]), seed=11, brain=MockBrain())
    assert view["champion"] is not None
    assert view["challenger"] is None
    assert view["champion"]["role"] == "champion"
    assert "horror" in view["champion"]["text"]
    assert view["champion"]["signals"]["word_count"] > 10


def test_better_promotes_challenger(db):
    sid = int(_start(db)["session"]["id"])
    generate(db, sid, seed=1, mutation="open on an object", brain=MockBrain())
    champ_id = generate(db, sid, seed=2, mutation="delay the reveal",
                        brain=MockBrain())["champion"]["id"]
    view = generate(db, sid, seed=3, mutation="raise the cost", brain=MockBrain())
    chall_id = view["challenger"]["id"]
    assert chall_id != champ_id
    judged = judge(db, sid, "better", note="colder")
    assert judged["champion"]["id"] == chall_id
    assert judged["challenger"] is None
    roles = {d["id"]: d["role"] for d in judged["history"]}
    assert roles[champ_id] == "retired"
    assert roles[chall_id] == "champion"
    notes = " ".join((judged["champion"].get("signals") or {}).get("notes") or [])
    assert "shorter" not in notes
    assert "first draft" in notes or notes == []


def test_worse_keeps_champion(db):
    sid = int(_start(db)["session"]["id"])
    generate(db, sid, seed=4, brain=MockBrain())
    champ_id = generate(db, sid, seed=5, brain=MockBrain())["champion"]["id"]
    generate(db, sid, seed=6, brain=MockBrain())
    judged = judge(db, sid, "worse")
    assert judged["champion"]["id"] == champ_id
    assert judged["challenger"] is None
    discarded = [d for d in judged["history"] if d["id"] == champ_id]
    assert discarded[0]["role"] == "champion"


def test_two_generates_are_not_identical(db):
    sid = int(_start(db)["session"]["id"])
    generate(db, sid, seed=20, brain=MockBrain())
    a = generate(db, sid, seed=21, brain=MockBrain())["challenger"]["text"]
    judge(db, sid, "worse")
    b = generate(db, sid, seed=22, brain=MockBrain())["challenger"]["text"]
    assert a != b
    assert lexical_novelty(a, b) > 0


def test_generate_injects_temperature_and_seed(db):
    brain = RecordingBrain()
    sid = int(_start(db)["session"]["id"])
    generate(db, sid, seed=99, temperature=0.91, mutation="cut a metaphor",
             brain=brain)
    assert brain.calls
    last = brain.calls[-1]
    assert last["seed"] == 99
    assert last["temperature"] == 0.91
    assert "cut a metaphor" in last["user"]
    assert "SCRIPTY_WRITER" in last["system"]


def test_lessons_persist_per_genre_desk(db):
    horror = int(_start(db, genre="horror")["session"]["id"])
    generate(db, horror, seed=1, brain=MockBrain())
    generate(db, horror, seed=2, brain=MockBrain())
    judge(db, horror, "better", note="keep it colder")
    horror_lessons = _start(db, genre="horror")  # same desk, new session
    assert generate(db, int(horror_lessons["session"]["id"]),
                    seed=3, brain=MockBrain())["lessons"]
    literary = int(_start(db, genre="literary")["session"]["id"])
    lit_view = generate(db, literary, seed=4, brain=MockBrain())
    assert lit_view["lessons"] == []
    assert lit_view["desk"]["slug"] == "literary"
    # horror desk still has its lesson
    from scripty.write.store import desk_by_slug, lessons_for_desk
    rows = lessons_for_desk(db, int(desk_by_slug(db, "horror")["id"]))
    assert rows and "horror" in rows[0]["rule"]


def test_next_generate_recalls_desk_lesson(db):
    brain = RecordingBrain()
    sid = int(_start(db)["session"]["id"])
    generate(db, sid, seed=1, brain=brain)
    generate(db, sid, seed=2, brain=brain)
    judge(db, sid, "worse", note="too tidy", brain=brain)
    generate(db, sid, seed=3, brain=brain)
    last_user = brain.calls[-1]["user"]
    assert "DESK LESSONS" in last_user


def test_judge_requires_challenger(db):
    sid = int(_start(db)["session"]["id"])
    generate(db, sid, seed=1, brain=MockBrain())
    with pytest.raises(ValueError, match="draft"):
        judge(db, sid, "better")
    with pytest.raises(ValueError, match="better"):
        generate(db, sid, seed=2, brain=MockBrain())
        judge(db, sid, "tie")


def test_short_session_has_no_next_chapter(db):
    sid = int(_start(db, length="short")["session"]["id"])
    generate(db, sid, seed=1, brain=MockBrain())
    with pytest.raises(ValueError, match="short"):
        advance(db, sid)


def test_medium_session_advances_chapter(db):
    sid = int(_start(db, length="medium")["session"]["id"])
    generate(db, sid, seed=1, brain=MockBrain())
    view = advance(db, sid, seed=8, brain=MockBrain())
    assert view["session"]["unit_index"] == 2
    assert view["session"]["unit_kind"] == "chapter"
    assert view["champion"] is not None


def test_user_excerpt_is_optional_and_recalled(db):
    add_reference(db, desk_slug="horror", kind="public_domain",
                  title="tiny PD note",
                  text="The wind walked on the roof all night.")
    brain = RecordingBrain()
    sid = int(_start(db)["session"]["id"])
    generate(db, sid, seed=1, brain=brain)
    assert "tiny PD note" in brain.calls[-1]["user"]
    assert "The wind walked" in brain.calls[-1]["user"]


def test_unknown_genre_rejected(db):
    with pytest.raises(ValueError, match="unknown genre"):
        normalize_genre("cyberpunk-western")
    with pytest.raises(ValueError):
        _start(db, genre="cyberpunk-western")


def test_signals_do_not_claim_a_winner():
    notes = measure("hello there friend", "hello there enemy")
    assert notes["word_count"] == 3
    assert notes["lexical_novelty"] is not None
    assert notes["notes"]


def test_cli_write_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from typer.testing import CliRunner
    from scripty.cli import app

    monkeypatch.setenv("SCRIPTY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SCRIPTY_PROVIDER", "mock")
    runner = CliRunner()
    start = runner.invoke(app, [
        "write", "start", "-g", "horror", "-t", "cold", "-l", "short",
        "-s", "Footprints on the lighthouse stairs.",
    ])
    assert start.exit_code == 0, start.output
    assert "CHAMPION" in start.output
    gen = runner.invoke(app, ["write", "generate", "1"])
    assert gen.exit_code == 0, gen.output
    assert "CHALLENGER" in gen.output
    better = runner.invoke(app, ["write", "judge", "1", "better", "-n", "colder"])
    assert better.exit_code == 0, better.output
    assert "verdict: better" in better.output
    show = runner.invoke(app, ["write", "show", "1"])
    assert show.exit_code == 0
    assert "DESK LESSONS" in show.output or "CHAMPION" in show.output


def test_mock_fiction_varies_by_seed():
    user = ("GENRE: horror\nTONE: cold\nLENGTH: short\nUNIT: story 1\n"
            "MUTATION: cut a metaphor\nSUMMARY:\nfootprints\nEND_SUMMARY")
    a = mock_fiction("SCRIPTY_WRITER", user, seed=1, temperature=0.8)
    b = mock_fiction("SCRIPTY_WRITER", user, seed=2, temperature=0.8)
    assert a != b
    assert "[horror/1]" in a
    assert "[horror/2]" in b
