"""Offline tests for scripty.learn — no LLM, no ffmpeg, no network."""
from __future__ import annotations

from pathlib import Path

import pytest

from scripty.core.db import Database
from scripty.learn import agreement_metrics, distill, recall, record_correction
from scripty.learn.distill import DISTILL_SYSTEM, _parse_rules
from scripty.learn.memory import _parse_slugline


# --------------------------------------------------------------------------- #
# Fixtures / seed helpers
# --------------------------------------------------------------------------- #

@pytest.fixture()
def db(tmp_path: Path):
    database = Database(tmp_path / "test.db")
    yield database
    database.close()


def seed_project(db: Database, name: str = "film") -> int:
    return db.create_project(name=name, slug=name, video_path=f"/media/{name}.mp4",
                             duration=10.0, fps=24.0, width=640, height=360)


def seed_shot(db: Database, pass_id: int, idx: int = 0, **over) -> int:
    cols = dict(pass_id=pass_id, idx=idx, start_s=idx * 2.0, end_s=idx * 2.0 + 2.0,
                scale="MS", subject="CAMP FIRE", angle="EYE LEVEL", move="STATIC",
                int_ext="EXT.", location="WOODS", time_of_day="NIGHT",
                action_text="Flames crackle.", characters=["MARA"], mood="warm",
                confidence=0.7, keyframes=[], raw={})
    cols.update(over)
    return db.add_shot(**cols)


class CannedBrain:
    name = "canned"

    def __init__(self, text: str):
        self.text = text
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.text


class ExplodingBrain:
    name = "exploding"

    def complete(self, system: str, user: str) -> str:
        raise RuntimeError("api down")


# --------------------------------------------------------------------------- #
# record_correction
# --------------------------------------------------------------------------- #

def test_record_rejects_unknown_field(db):
    pid = seed_project(db)
    with pytest.raises(ValueError):
        record_correction(db, project_id=pid, pass_id=1, entity_type="shot",
                          entity_id=1, field="haircut", model_value="a",
                          human_value="b")


def test_record_rejects_unknown_entity_type(db):
    pid = seed_project(db)
    with pytest.raises(ValueError):
        record_correction(db, project_id=pid, pass_id=1, entity_type="gaffer",
                          entity_id=1, field="subject", model_value="a",
                          human_value="b")


def test_record_rejects_bad_scope(db):
    pid = seed_project(db)
    with pytest.raises(ValueError):
        record_correction(db, project_id=pid, pass_id=1, entity_type="shot",
                          entity_id=1, field="subject", model_value="a",
                          human_value="b", scope="universe")


def test_correction_applies_to_shot_text_field(db):
    pid = seed_project(db)
    pass_id = db.create_pass(project_id=pid, provider="mock")
    sid = seed_shot(db, pass_id)
    cid = record_correction(db, project_id=pid, pass_id=pass_id,
                            entity_type="shot", entity_id=sid, field="location",
                            model_value="WOODS", human_value="WOODED ROAD")
    assert isinstance(cid, int)
    assert db.get_shot(sid)["location"] == "WOODED ROAD"
    rows = db.corrections(project_id=pid, field="location")
    assert rows and rows[0]["human_value"] == "WOODED ROAD"
    assert rows[0]["entity_type"] == "shot"


def test_correction_coerces_enum_and_keeps_current_on_garbage(db):
    pid = seed_project(db)
    pass_id = db.create_pass(project_id=pid, provider="mock")
    sid = seed_shot(db, pass_id)
    record_correction(db, project_id=pid, pass_id=pass_id, entity_type="shot",
                      entity_id=sid, field="scale", model_value="MS",
                      human_value="ecu")
    assert db.get_shot(sid)["scale"] == "ECU"
    # garbage enum value falls back to the row's current value, not a reset
    record_correction(db, project_id=pid, pass_id=pass_id, entity_type="shot",
                      entity_id=sid, field="scale", model_value="ECU",
                      human_value="banana")
    assert db.get_shot(sid)["scale"] == "ECU"


def test_correction_parses_characters_csv(db):
    pid = seed_project(db)
    pass_id = db.create_pass(project_id=pid, provider="mock")
    sid = seed_shot(db, pass_id)
    record_correction(db, project_id=pid, pass_id=pass_id, entity_type="shot",
                      entity_id=sid, field="characters", model_value="MARA",
                      human_value="MARA, JOE , MAN IN RED JACKET")
    assert db.get_shot(sid)["characters"] == ["MARA", "JOE", "MAN IN RED JACKET"]


def test_scene_slugline_correction_updates_heading_fields(db):
    pid = seed_project(db)
    pass_id = db.create_pass(project_id=pid, provider="mock")
    scene_id = db.add_scene(pass_id=pass_id, idx=0, int_ext="EXT.",
                            location="WOODS", time_of_day="DAY",
                            shot_ids=[1], synopsis="")
    record_correction(db, project_id=pid, pass_id=pass_id, entity_type="scene",
                      entity_id=scene_id, field="slugline",
                      model_value="EXT. WOODS - DAY",
                      human_value="INT. kitchen - NIGHT")
    row = db.row("SELECT * FROM scenes WHERE id = ?", (scene_id,), "scenes")
    assert row["int_ext"] == "INT."
    assert row["location"] == "KITCHEN"
    assert row["time_of_day"] == "NIGHT"


def test_slugline_parser_edge_cases():
    int_ext, loc, tod = _parse_slugline("I/E CAR - CONTINUOUS")
    assert int_ext.value == "INT./EXT." and loc == "CAR" and tod.value == "CONTINUOUS"
    int_ext, loc, tod = _parse_slugline("EXT. HOTEL - LOBBY - DAWN")
    assert loc == "HOTEL - LOBBY" and tod.value == "DAWN"


def test_dialogue_and_prompt_corrections_apply(db):
    pid = seed_project(db)
    pass_id = db.create_pass(project_id=pid, provider="mock")
    did = db.add_dialogue(pass_id=pass_id, character="SPEAKER 1", text="helo",
                          start_s=0.5, end_s=1.0)
    record_correction(db, project_id=pid, pass_id=pass_id, entity_type="dialogue",
                      entity_id=did, field="text", model_value="helo",
                      human_value="Hello there.")
    assert db.row("SELECT * FROM dialogue WHERE id = ?", (did,))["text"] == "Hello there."

    dp = db.add_describe_prompt(pass_id=pass_id, scene_id=None, shot_id=None,
                                revision=1, target="generic", prompt="old prompt",
                                continuity={})
    record_correction(db, project_id=pid, pass_id=pass_id,
                      entity_type="describe_prompt", entity_id=dp, field="prompt",
                      model_value="old prompt", human_value="new prompt")
    row = db.row("SELECT * FROM describe_prompts WHERE id = ?", (dp,),
                 "describe_prompts")
    assert row["prompt"] == "new prompt"


def test_correction_recorded_even_when_row_missing(db):
    pid = seed_project(db)
    cid = record_correction(db, project_id=pid, pass_id=1, entity_type="shot",
                            entity_id=9999, field="subject", model_value="a",
                            human_value="b")
    assert db.corrections(project_id=pid, field="subject")[0]["id"] == cid


# --------------------------------------------------------------------------- #
# recall
# --------------------------------------------------------------------------- #

def test_recall_merges_field_and_general_lessons_ranked_by_weight(db):
    pid = seed_project(db)
    low = db.add_lesson(scope="project", project_id=pid, field="location",
                        rule="low", source_ids=[], weight=1.0)
    high = db.add_lesson(scope="project", project_id=pid, field="location",
                         rule="high", source_ids=[], weight=3.0)
    star = db.add_lesson(scope="project", project_id=pid, field="*",
                         rule="general", source_ids=[], weight=2.0)
    bundle = recall(db, project_id=pid, field="location", k_lessons=2,
                    k_examples=5)
    assert [ls.id for ls in bundle.lessons] == [high, star]
    assert all(ls.active for ls in bundle.lessons)
    full = recall(db, project_id=pid, field="location", k_lessons=10,
                  k_examples=5)
    assert [ls.id for ls in full.lessons] == [high, star, low]


def test_recall_examples_recency_then_query_ranking(db):
    pid = seed_project(db)
    pass_id = db.create_pass(project_id=pid, provider="mock")
    s1 = seed_shot(db, pass_id, idx=0, location="WOODS")
    s2 = seed_shot(db, pass_id, idx=1, location="BEACH")
    record_correction(db, project_id=pid, pass_id=pass_id, entity_type="shot",
                      entity_id=s1, field="location", model_value="WOODS",
                      human_value="WOODED ROAD")
    record_correction(db, project_id=pid, pass_id=pass_id, entity_type="shot",
                      entity_id=s2, field="location", model_value="BEACH",
                      human_value="SHORELINE")
    # no query -> recency (latest first)
    bundle = recall(db, project_id=pid, field="location", k_examples=1)
    assert bundle.examples[0].human_value == "SHORELINE"
    # query -> fuzzy match wins over recency
    bundle = recall(db, project_id=pid, field="location", k_examples=1,
                    query_text="trees line a road through the woods")
    assert bundle.examples[0].human_value == "WOODED ROAD"
    assert "WOODED ROAD" in bundle.format_for_prompt()


def test_recall_empty_bundle(db):
    pid = seed_project(db)
    bundle = recall(db, project_id=pid, field="subject")
    assert bundle.is_empty()


# --------------------------------------------------------------------------- #
# agreement_metrics
# --------------------------------------------------------------------------- #

def test_agreement_no_prior_corrections(db):
    pid = seed_project(db)
    p1 = db.create_pass(project_id=pid, provider="mock")
    out = agreement_metrics(db, project_id=pid, pass_id=p1)
    assert out == {"corrections_checked": 0, "now_agreeing": 0,
                   "agreement_rate": None, "by_field": {}}


def test_agreement_shots_by_idx_and_by_field(db):
    pid = seed_project(db)
    p1 = db.create_pass(project_id=pid, provider="mock")
    s1 = seed_shot(db, p1, idx=0, location="WOODS", subject="CAMP FIRE")
    record_correction(db, project_id=pid, pass_id=p1, entity_type="shot",
                      entity_id=s1, field="location", model_value="WOODS",
                      human_value="WOODED ROAD")
    record_correction(db, project_id=pid, pass_id=p1, entity_type="shot",
                      entity_id=s1, field="subject", model_value="CAMP FIRE",
                      human_value="CAMP FIRE SITE")
    p2 = db.create_pass(project_id=pid, provider="mock")
    seed_shot(db, p2, idx=0, location="wooded road", subject="CAMP FIRE")
    out = agreement_metrics(db, project_id=pid, pass_id=p2)
    assert out["corrections_checked"] == 2
    assert out["now_agreeing"] == 1  # location learned (case-insensitive), subject not
    assert out["agreement_rate"] == pytest.approx(0.5)
    assert out["by_field"]["location"]["rate"] == pytest.approx(1.0)
    assert out["by_field"]["subject"]["rate"] == pytest.approx(0.0)
    # corrections made ON the current pass are not counted against it
    record_correction(db, project_id=pid, pass_id=p2, entity_type="shot",
                      entity_id=s1, field="mood", model_value="warm",
                      human_value="eerie")
    out2 = agreement_metrics(db, project_id=pid, pass_id=p2)
    assert out2["corrections_checked"] == 2


def test_agreement_dialogue_by_order_and_characters_as_set(db):
    pid = seed_project(db)
    p1 = db.create_pass(project_id=pid, provider="mock")
    db.add_dialogue(pass_id=p1, character="SPEAKER 1", text="hello",
                    start_s=0.5, end_s=1.0)
    d2 = db.add_dialogue(pass_id=p1, character="SPEAKER 2", text="goodby",
                         start_s=2.0, end_s=3.0)
    record_correction(db, project_id=pid, pass_id=p1, entity_type="dialogue",
                      entity_id=d2, field="text", model_value="goodby",
                      human_value="Goodbye.")
    s1 = seed_shot(db, p1, idx=0, characters=["MARA"])
    record_correction(db, project_id=pid, pass_id=p1, entity_type="shot",
                      entity_id=s1, field="characters", model_value="MARA",
                      human_value="JOE, MARA")
    p2 = db.create_pass(project_id=pid, provider="mock")
    db.add_dialogue(pass_id=p2, character="SPEAKER 1", text="hello",
                    start_s=0.5, end_s=1.0)
    db.add_dialogue(pass_id=p2, character="SPEAKER 2", text="goodbye.",
                    start_s=2.0, end_s=3.0)
    seed_shot(db, p2, idx=0, characters=["MARA", "JOE"])  # order differs; set match
    out = agreement_metrics(db, project_id=pid, pass_id=p2)
    assert out["corrections_checked"] == 2
    assert out["now_agreeing"] == 2
    assert out["agreement_rate"] == pytest.approx(1.0)


def test_agreement_scene_slugline(db):
    pid = seed_project(db)
    p1 = db.create_pass(project_id=pid, provider="mock")
    sc1 = db.add_scene(pass_id=p1, idx=0, int_ext="EXT.", location="WOODS",
                       time_of_day="DAY", shot_ids=[], synopsis="")
    record_correction(db, project_id=pid, pass_id=p1, entity_type="scene",
                      entity_id=sc1, field="slugline",
                      model_value="EXT. WOODS - DAY",
                      human_value="INT. KITCHEN - NIGHT")
    p2 = db.create_pass(project_id=pid, provider="mock")
    db.add_scene(pass_id=p2, idx=0, int_ext="INT.", location="Kitchen",
                 time_of_day="NIGHT", shot_ids=[], synopsis="")
    out = agreement_metrics(db, project_id=pid, pass_id=p2)
    assert out["corrections_checked"] == 1 and out["now_agreeing"] == 1


# --------------------------------------------------------------------------- #
# distill
# --------------------------------------------------------------------------- #

def _one_correction(db, pid, pass_id, sid, model="WOODS", human="WOODED ROAD",
                    field="location", **kw):
    return record_correction(db, project_id=pid, pass_id=pass_id,
                             entity_type="shot", entity_id=sid, field=field,
                             model_value=model, human_value=human, **kw)


def test_distill_fallback_rule_and_skip_already_cited(db):
    pid = seed_project(db)
    p1 = db.create_pass(project_id=pid, provider="mock")
    sid = seed_shot(db, p1)
    cid = _one_correction(db, pid, p1, sid)
    new_ids = distill(db, None, project_id=pid)
    assert len(new_ids) == 1
    lesson = db.row("SELECT * FROM lessons WHERE id = ?", (new_ids[0],), "lessons")
    assert lesson["rule"] == "Prefer 'WOODED ROAD' over 'WOODS' when similar context recurs."
    assert lesson["field"] == "location"
    assert lesson["scope"] == "project"
    assert lesson["project_id"] == pid
    assert lesson["source_ids"] == [cid]
    # nothing fresh left -> second run is a no-op
    assert distill(db, None, project_id=pid) == []
    # a new correction distills alone, without re-citing the old one
    cid2 = _one_correction(db, pid, p1, sid, model="MS", human="CU", field="scale")
    ids2 = distill(db, None, project_id=pid)
    assert len(ids2) == 1
    lesson2 = db.row("SELECT * FROM lessons WHERE id = ?", (ids2[0],), "lessons")
    assert lesson2["source_ids"] == [cid2]


def test_distill_with_brain_parses_rules(db):
    pid = seed_project(db)
    p1 = db.create_pass(project_id=pid, provider="mock")
    sid = seed_shot(db, p1)
    c1 = _one_correction(db, pid, p1, sid)
    c2 = _one_correction(db, pid, p1, sid, model="FOREST", human="WOODED ROAD")
    brain = CannedBrain(
        "Rules:\n"
        "- When trees line both sides of a visible road, label the location "
        "WOODED ROAD, not WOODS.\n"
        "2) Never label a location FOREST when a road is visible.\n")
    new_ids = distill(db, brain, project_id=pid)
    assert len(new_ids) == 2
    rules = [db.row("SELECT * FROM lessons WHERE id = ?", (i,), "lessons")
             for i in new_ids]
    assert rules[0]["rule"].startswith("When trees line both sides")
    assert rules[1]["rule"].startswith("Never label a location FOREST")
    assert all(r["source_ids"] == [c1, c2] for r in rules)
    system, user = brain.calls[0]
    assert system == DISTILL_SYSTEM
    assert "'WOODED ROAD'" in user and "location" in user


def test_distill_brain_error_falls_back(db):
    pid = seed_project(db)
    p1 = db.create_pass(project_id=pid, provider="mock")
    sid = seed_shot(db, p1)
    _one_correction(db, pid, p1, sid)
    new_ids = distill(db, ExplodingBrain(), project_id=pid)
    assert len(new_ids) == 1
    lesson = db.row("SELECT * FROM lessons WHERE id = ?", (new_ids[0],), "lessons")
    assert lesson["rule"].startswith("Prefer 'WOODED ROAD'")


def test_distill_min_corrections_gate(db):
    pid = seed_project(db)
    p1 = db.create_pass(project_id=pid, provider="mock")
    sid = seed_shot(db, p1)
    _one_correction(db, pid, p1, sid)
    assert distill(db, None, project_id=pid, min_corrections=2) == []
    _one_correction(db, pid, p1, sid, model="TREES", human="WOODED ROAD")
    assert len(distill(db, None, project_id=pid, min_corrections=2)) == 2


def test_distill_global_scope_correction_makes_global_lesson(db):
    pid = seed_project(db)
    p1 = db.create_pass(project_id=pid, provider="mock")
    sid = seed_shot(db, p1)
    _one_correction(db, pid, p1, sid, scope="global")
    new_ids = distill(db, None, project_id=pid)
    lesson = db.row("SELECT * FROM lessons WHERE id = ?", (new_ids[0],), "lessons")
    assert lesson["scope"] == "global"
    assert lesson["project_id"] is None


def test_parse_rules_caps_at_three_and_strips_markers():
    text = '1. "First rule."\n- Second rule\n* Third rule\n- Fourth rule\n\nHeader:\n'
    rules = _parse_rules(text)
    assert rules == ["First rule.", "Second rule", "Third rule"]


def test_distilled_lesson_flows_back_through_recall(db):
    pid = seed_project(db)
    p1 = db.create_pass(project_id=pid, provider="mock")
    sid = seed_shot(db, p1)
    _one_correction(db, pid, p1, sid)
    distill(db, None, project_id=pid)
    bundle = recall(db, project_id=pid, field="location")
    assert any("WOODED ROAD" in ls.rule for ls in bundle.lessons)
    assert any(ex.human_value == "WOODED ROAD" for ex in bundle.examples)
