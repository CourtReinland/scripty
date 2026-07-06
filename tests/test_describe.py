"""Offline tests for scripty.describe (no LLM, no network, no ffmpeg)."""
from __future__ import annotations

from unittest.mock import Mock

import pytest

from scripty.describe import build_continuity, export_text, generate_prompts
from scripty.describe.promptgen import POLISH_SYSTEM


# --------------------------------------------------------------------------- #
# Fixtures: db-shaped dicts (as returned by Database.shots_for_pass etc.)
# --------------------------------------------------------------------------- #

@pytest.fixture()
def shots() -> list[dict]:
    return [
        {
            "id": 11, "pass_id": 1, "idx": 0, "start_s": 0.0, "end_s": 2.0,
            "scale": "WS", "subject": "CAMP FIRE", "angle": "HIGH ANGLE",
            "move": "STATIC", "int_ext": "EXT.", "location": "WOODED ROAD",
            "time_of_day": "NIGHT",
            "action_text": "A fire crackles beside the empty road.",
            "characters": ["MARA"], "mood": "ominous", "confidence": 0.8,
            "keyframes": [],
            "raw": {"characters": [
                {"name": "MARA", "description": "woman in a red parka"}]},
        },
        {
            "id": 12, "pass_id": 1, "idx": 1, "start_s": 2.0, "end_s": 5.5,
            "scale": "CU", "subject": "MARA", "angle": "EYE LEVEL",
            "move": "PUSH IN", "int_ext": "EXT.", "location": "wooded road",
            "time_of_day": "NIGHT", "action_text": "Mara stares into the flames.",
            "characters": ["MARA"], "mood": "ominous", "confidence": 0.7,
            "keyframes": [],
            "raw": {"characters": [{
                "name": "MARA",
                "description": "woman in a red parka with mud-streaked boots",
            }]},
        },
        {
            "id": 13, "pass_id": 1, "idx": 2, "start_s": 5.5, "end_s": 8.0,
            "scale": "MS", "subject": "JON", "angle": "LOW ANGLE",
            "move": "HANDHELD", "int_ext": "INT.", "location": "TENT",
            "time_of_day": "NIGHT", "action_text": "Jon zips the tent shut.",
            "characters": ["JON"], "mood": "warm", "confidence": 0.6,
            "keyframes": [],
            "raw": {"characters": [
                {"name": "JON", "description": "bearded man in a wool cap"}]},
        },
    ]


@pytest.fixture()
def scenes() -> list[dict]:
    return [
        {"id": 21, "pass_id": 1, "idx": 0, "int_ext": "EXT.",
         "location": "WOODED ROAD", "time_of_day": "NIGHT",
         "shot_ids": [11, 12],
         "synopsis": "A fire crackles beside the empty road."},
        {"id": 22, "pass_id": 1, "idx": 1, "int_ext": "INT.",
         "location": "TENT", "time_of_day": "NIGHT", "shot_ids": [13],
         "synopsis": "Jon zips the tent shut."},
    ]


@pytest.fixture()
def dialogue() -> list[dict]:
    return [
        {"id": 31, "pass_id": 1, "shot_id": 12, "scene_id": None,
         "character": "MARA", "text": "We shouldn't be here.",
         "parenthetical": "", "start_s": 3.0, "end_s": 4.0},
    ]


# --------------------------------------------------------------------------- #
# build_continuity
# --------------------------------------------------------------------------- #

def test_continuity_picks_best_character_description(shots, scenes):
    cont = build_continuity(shots, scenes)
    # longest description seen wins
    assert cont["characters"]["MARA"] == (
        "woman in a red parka with mud-streaked boots")
    assert cont["characters"]["JON"] == "bearded man in a wool cap"


def test_continuity_locations_dedupe_case_insensitively(shots, scenes):
    cont = build_continuity(shots, scenes)
    locs = cont["locations"]
    # "WOODED ROAD" and "wooded road" collapse to one entry
    assert len([k for k in locs if k.upper() == "WOODED ROAD"]) == 1
    road = locs["WOODED ROAD"]
    assert road["int_ext"] == "EXT."
    assert road["time_of_day"] == "NIGHT"
    assert road["mood"] == "ominous"
    assert locs["TENT"]["int_ext"] == "INT."


def test_continuity_style_reflects_dominant_lighting_and_mood(shots, scenes):
    cont = build_continuity(shots, scenes)
    style = cont["style"].lower()
    assert "night" in style
    assert "ominous" in style


def test_continuity_empty_inputs():
    cont = build_continuity([], [])
    assert cont["characters"] == {}
    assert cont["locations"] == {}
    assert isinstance(cont["style"], str) and cont["style"]


def test_continuity_scene_only_location():
    scenes = [{"id": 1, "idx": 0, "int_ext": "EXT.", "location": "BEACH",
               "time_of_day": "DAY", "shot_ids": [], "synopsis": ""}]
    cont = build_continuity([], scenes)
    assert cont["locations"]["BEACH"]["time_of_day"] == "DAY"


# --------------------------------------------------------------------------- #
# generate_prompts — deterministic template path
# --------------------------------------------------------------------------- #

def test_one_prompt_per_shot_plus_one_per_scene(shots, scenes, dialogue):
    prompts = generate_prompts(scenes, shots, dialogue)
    assert len(prompts) == len(shots) + len(scenes)
    shot_ids = sorted(p["shot_id"] for p in prompts if p["shot_id"] is not None)
    assert shot_ids == [11, 12, 13]
    scene_umbrellas = [p for p in prompts if p["shot_id"] is None]
    assert sorted(p["scene_id"] for p in scene_umbrellas) == [21, 22]


def test_prompt_dicts_have_exact_db_keys(shots, scenes, dialogue):
    for p in generate_prompts(scenes, shots, dialogue, target="sora"):
        assert set(p) == {"scene_id", "shot_id", "revision", "target",
                          "prompt", "continuity"}
        assert p["revision"] == 1
        assert p["target"] == "sora"
        assert isinstance(p["prompt"], str) and p["prompt"]
        assert isinstance(p["continuity"], dict)
        assert "pass_id" not in p


def test_shot_prompt_composition(shots, scenes, dialogue):
    prompts = generate_prompts(scenes, shots, dialogue)
    by_shot = {p["shot_id"]: p for p in prompts if p["shot_id"] is not None}

    p11 = by_shot[11]["prompt"]
    assert "Wide shot" in p11                       # scale -> framing words
    assert "from a high angle" in p11               # angle
    assert "locked-off static camera" in p11        # move
    assert "exterior" in p11.lower()                # int_ext
    assert "WOODED ROAD" in p11                     # location
    assert "night" in p11.lower()                   # time_of_day -> lighting
    assert "ominous" in p11                         # mood
    assert "approximately 2.0s" in p11              # duration hint
    assert "A fire crackles beside the empty road." in p11   # action
    assert "woman in a red parka with mud-streaked boots" in p11  # continuity

    p12 = by_shot[12]["prompt"]
    assert "approximately 3.5s" in p12
    assert 'MARA: "We shouldn\'t be here."' in p12  # dialogue mapped to shot
    assert "We shouldn't be here." not in p11       # not leaked to other shots


def test_shot_prompt_scene_id_backfilled(shots, scenes, dialogue):
    prompts = generate_prompts(scenes, shots, dialogue)
    by_shot = {p["shot_id"]: p for p in prompts if p["shot_id"] is not None}
    assert by_shot[11]["scene_id"] == 21
    assert by_shot[12]["scene_id"] == 21
    assert by_shot[13]["scene_id"] == 22


def test_scene_umbrella_content_and_order(shots, scenes, dialogue):
    prompts = generate_prompts(scenes, shots, dialogue)
    umbrella = next(p for p in prompts if p["scene_id"] == 21
                    and p["shot_id"] is None)
    text = umbrella["prompt"]
    assert "WOODED ROAD" in text
    assert "Covers 2 shot(s)" in text
    assert "approximately 5.5s" in text             # 2.0 + 3.5
    assert "A fire crackles beside the empty road." in text  # synopsis
    assert "MARA" in text
    # umbrella precedes its member shot prompts
    idx_umbrella = prompts.index(umbrella)
    idx_shot = next(i for i, p in enumerate(prompts) if p["shot_id"] == 11)
    assert idx_umbrella < idx_shot


def test_shot_outside_any_scene_still_gets_prompt(shots, dialogue):
    prompts = generate_prompts([], shots, dialogue)
    assert len(prompts) == 3
    assert all(p["shot_id"] is not None for p in prompts)
    assert all(p["scene_id"] is None for p in prompts)


def test_empty_everything():
    assert generate_prompts([], [], []) == []


def test_lesson_rules_only_prompt_field_and_active(shots, scenes, dialogue):
    lessons = [
        {"id": 1, "field": "prompt", "rule": "Never mention camera brands.",
         "active": 1, "weight": 1.0},
        {"id": 2, "field": "location", "rule": "LOCATION-ONLY RULE",
         "active": 1, "weight": 1.0},
        {"id": 3, "field": "prompt", "rule": "INACTIVE RULE",
         "active": 0, "weight": 1.0},
    ]
    prompts = generate_prompts(scenes, shots, dialogue, lessons=lessons)
    for p in prompts:
        assert "Never mention camera brands." in p["prompt"]
        assert "LOCATION-ONLY RULE" not in p["prompt"]
        assert "INACTIVE RULE" not in p["prompt"]


def test_degraded_shot_fields_fall_back_to_defaults():
    shot = {"id": 1, "idx": 0, "start_s": 0.0, "end_s": 1.0,
            "scale": "GARBAGE", "subject": "", "angle": None, "move": "??",
            "int_ext": "nope", "location": "", "time_of_day": "whenever",
            "action_text": "", "characters": [], "mood": "", "raw": {}}
    [p] = generate_prompts([], [shot], [])
    text = p["prompt"]
    assert "Medium shot" in text                    # ShotScale default MS
    assert "at eye level" in text                   # CameraAngle default
    assert "the established location" in text
    assert "approximately 1.0s" in text


# --------------------------------------------------------------------------- #
# generate_prompts — brain polish path (mocked TextBrain boundary)
# --------------------------------------------------------------------------- #

def test_brain_polishes_every_template(shots, scenes, dialogue):
    brain = Mock()
    brain.name = "mockbrain"
    brain.complete = Mock(return_value="  POLISHED PROMPT  ")
    prompts = generate_prompts(scenes, shots, dialogue, brain=brain)
    assert all(p["prompt"] == "POLISHED PROMPT" for p in prompts)
    assert brain.complete.call_count == len(prompts)
    system_arg, user_arg = brain.complete.call_args[0]
    assert system_arg == POLISH_SYSTEM
    assert "approximately" in user_arg              # template passed as user


def test_brain_error_never_fails_the_pass(shots, scenes, dialogue):
    brain = Mock()
    brain.name = "broken"
    brain.complete = Mock(side_effect=RuntimeError("api down"))
    with_brain = generate_prompts(scenes, shots, dialogue, brain=brain)
    without = generate_prompts(scenes, shots, dialogue)
    assert [p["prompt"] for p in with_brain] == [p["prompt"] for p in without]


def test_brain_empty_answer_falls_back_to_template(shots, scenes, dialogue):
    brain = Mock()
    brain.name = "empty"
    brain.complete = Mock(return_value="   ")
    prompts = generate_prompts(scenes, shots, dialogue, brain=brain)
    assert all("approximately" in p["prompt"] for p in prompts)


# --------------------------------------------------------------------------- #
# export_text
# --------------------------------------------------------------------------- #

def test_export_text_numbered_sheet(shots, scenes, dialogue):
    prompts = generate_prompts(scenes, shots, dialogue, target="veo")
    sheet = export_text(prompts)
    assert "[001]" in sheet and f"[{len(prompts):03d}]" in sheet
    assert "target: veo" in sheet
    assert "SCENE PROMPT (scene id 21)" in sheet
    assert "SHOT PROMPT (shot id 13)" in sheet
    for p in prompts:
        assert p["prompt"] in sheet


def test_export_text_empty():
    sheet = export_text([])
    assert "no prompts" in sheet


# --------------------------------------------------------------------------- #
# round-trip: dicts are directly insertable via db.add_describe_prompt
# --------------------------------------------------------------------------- #

def test_prompts_insert_into_database(tmp_path, shots, scenes, dialogue):
    from scripty.core.db import Database

    db = Database(tmp_path / "t.db")
    try:
        pid = db.create_project(name="Test Film", slug="test-film",
                                video_path="/tmp/x.mp4", duration=8.0,
                                fps=24.0, width=640, height=360)
        pass_id = db.create_pass(project_id=pid, provider="mock")
        prompts = generate_prompts(scenes, shots, dialogue)
        ids = [db.add_describe_prompt(pass_id=pass_id, **p) for p in prompts]
        assert len(ids) == len(prompts)
        rows = db.prompts_for_pass(pass_id)
        assert len(rows) == len(prompts)
        assert all(r["revision"] == 1 for r in rows)
        assert all(isinstance(r["continuity"], dict) for r in rows)
        assert any(r["continuity"].get("characters", {}).get("MARA")
                   for r in rows)
    finally:
        db.close()
