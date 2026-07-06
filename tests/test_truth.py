"""Offline tests for scripty.truth (ground-truth linking, alignment, auto-corrections).

The `scripty.script.fountain` and `scripty.learn.memory` modules are built in
parallel, so they are injected into sys.modules as mocks; truth imports them
lazily inside its functions.
"""
from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from unittest import mock

import pytest

from scripty.core.db import Database
from scripty.truth import align_pass, emit_auto_corrections, link_script


# --------------------------------------------------------------------------- #
# Fakes / injection helpers
# --------------------------------------------------------------------------- #

@dataclass
class FakeParsedScene:
    idx: int
    int_ext: str
    location: str
    time_of_day: str
    action: list = field(default_factory=list)
    dialogue: list = field(default_factory=list)


@dataclass
class FakeParsedScript:
    title: str
    scenes: list = field(default_factory=list)


def inject_fountain(parse_fountain):
    """Context manager placing a fake scripty.script.fountain in sys.modules."""
    pkg = types.ModuleType("scripty.script")
    mod = types.ModuleType("scripty.script.fountain")
    mod.parse_fountain = parse_fountain
    pkg.fountain = mod
    return mock.patch.dict(sys.modules, {
        "scripty.script": pkg,
        "scripty.script.fountain": mod,
    })


def inject_memory(record_correction):
    """Context manager placing a fake scripty.learn.memory in sys.modules."""
    pkg = types.ModuleType("scripty.learn")
    mod = types.ModuleType("scripty.learn.memory")
    mod.record_correction = record_correction
    pkg.memory = mod
    return mock.patch.dict(sys.modules, {
        "scripty.learn": pkg,
        "scripty.learn.memory": mod,
    })


# --------------------------------------------------------------------------- #
# Fixtures / seed helpers
# --------------------------------------------------------------------------- #

@pytest.fixture()
def db(tmp_path: Path):
    database = Database(tmp_path / "test.db")
    yield database
    database.close()


@pytest.fixture()
def project_id(db: Database, tmp_path: Path) -> int:
    return db.create_project(name="Test Film", slug="test-film",
                             video_path=str(tmp_path / "film.mp4"),
                             duration=30.0, fps=24.0, width=640, height=360)


@pytest.fixture()
def pass_id(db: Database, project_id: int) -> int:
    return db.create_pass(project_id=project_id, provider="mock")


def add_scene(db: Database, pass_id: int, idx: int, int_ext: str,
              location: str, time_of_day: str) -> int:
    return db.add_scene(pass_id=pass_id, idx=idx, int_ext=int_ext,
                        location=location, time_of_day=time_of_day,
                        shot_ids=[], synopsis="")


def truth_scene(idx: int, int_ext: str, location: str, time_of_day: str,
                dialogue: list | None = None) -> dict:
    return {"idx": idx, "int_ext": int_ext, "location": location,
            "time_of_day": time_of_day, "action": [],
            "dialogue": dialogue or []}


def link_truth(db: Database, project_id: int, scenes: list[dict]) -> int:
    return db.add_truth(project_id=project_id, script_path="/tmp/known.fountain",
                        parsed={"title": "TEST", "scenes": scenes})


# --------------------------------------------------------------------------- #
# link_script
# --------------------------------------------------------------------------- #

class TestLinkScript:
    def test_stores_parsed_truth_and_artifact(self, db, project_id, tmp_path):
        script = tmp_path / "known.fountain"
        script.write_text("INT. CABIN - NIGHT\n\nA fire crackles.\n",
                          encoding="utf-8")
        parsed = FakeParsedScript(title="KNOWN", scenes=[
            FakeParsedScene(idx=0, int_ext="INT.", location="CABIN",
                            time_of_day="NIGHT", action=["A fire crackles."],
                            dialogue=[("MARA", "Hello.")]),
        ])
        parse_fountain = mock.Mock(return_value=parsed)
        with inject_fountain(parse_fountain):
            truth_id = link_script(db, project_id=project_id, script_path=script)

        parse_fountain.assert_called_once_with(script.read_text(encoding="utf-8"))
        row = db.truth_for_project(project_id)
        assert row is not None and row["id"] == truth_id
        assert row["script_path"] == str(script.resolve())
        assert row["parsed"]["title"] == "KNOWN"
        assert row["parsed"]["scenes"][0]["location"] == "CABIN"
        kinds = [a["kind"] for a in db.artifacts_for_project(project_id)]
        assert "known_script" in kinds
        art = [a for a in db.artifacts_for_project(project_id)
               if a["kind"] == "known_script"][0]
        assert art["ref"] == str(script.resolve())

    def test_accepts_dict_from_parser(self, db, project_id, tmp_path):
        script = tmp_path / "plain.txt"
        script.write_text("EXT. WOODS - DAY", encoding="utf-8")
        parsed = {"title": "T", "scenes": [truth_scene(0, "EXT.", "WOODS", "DAY")]}
        with inject_fountain(mock.Mock(return_value=parsed)):
            link_script(db, project_id=project_id, script_path=script)
        assert db.truth_for_project(project_id)["parsed"] == parsed

    def test_missing_file_raises(self, db, project_id, tmp_path):
        with inject_fountain(mock.Mock()):
            with pytest.raises(FileNotFoundError):
                link_script(db, project_id=project_id,
                            script_path=tmp_path / "nope.fountain")

    def test_directory_raises(self, db, project_id, tmp_path):
        with inject_fountain(mock.Mock()):
            with pytest.raises(ValueError):
                link_script(db, project_id=project_id, script_path=tmp_path)

    def test_unknown_project_raises(self, db, tmp_path):
        script = tmp_path / "s.txt"
        script.write_text("x", encoding="utf-8")
        with inject_fountain(mock.Mock()):
            with pytest.raises(ValueError):
                link_script(db, project_id=999, script_path=script)


# --------------------------------------------------------------------------- #
# align_pass
# --------------------------------------------------------------------------- #

class TestAlignPass:
    def test_perfect_match(self, db, project_id, pass_id):
        add_scene(db, pass_id, 0, "EXT.", "WOODS", "DAY")
        add_scene(db, pass_id, 1, "INT.", "CABIN", "NIGHT")
        link_truth(db, project_id, [
            truth_scene(0, "EXT.", "WOODS", "DAY"),
            truth_scene(1, "INT.", "CABIN", "NIGHT"),
        ])
        report = align_pass(db, pass_id=pass_id)
        assert len(report["scene_pairs"]) == 2
        assert report["unmatched_model"] == []
        assert report["unmatched_truth"] == []
        assert report["slugline_accuracy"] == 1.0
        for pair in report["scene_pairs"]:
            assert pair["diffs"] == {}
            assert pair["score"] == pytest.approx(1.0)

    def test_diffs_reported_per_field(self, db, project_id, pass_id):
        add_scene(db, pass_id, 0, "EXT.", "WOODS", "DAY")
        add_scene(db, pass_id, 1, "EXT.", "CABIN", "DAY")
        link_truth(db, project_id, [
            truth_scene(0, "EXT.", "WOODED ROAD", "DAY"),
            truth_scene(1, "INT.", "CABIN", "NIGHT"),
        ])
        report = align_pass(db, pass_id=pass_id)
        assert len(report["scene_pairs"]) == 2
        first, second = report["scene_pairs"]
        assert first["diffs"] == {
            "location": {"model": "WOODS", "truth": "WOODED ROAD"}}
        assert set(second["diffs"]) == {"int_ext", "time_of_day"}
        assert second["diffs"]["int_ext"] == {"model": "EXT.", "truth": "INT."}
        assert report["slugline_accuracy"] == 0.0

    def test_int_ext_compare_is_lenient(self, db, project_id, pass_id):
        add_scene(db, pass_id, 0, "INT.", "CABIN", "NIGHT")
        link_truth(db, project_id, [truth_scene(0, "int", "cabin", "Night")])
        report = align_pass(db, pass_id=pass_id)
        assert report["scene_pairs"][0]["diffs"] == {}
        assert report["slugline_accuracy"] == 1.0

    def test_unmatched_truth_scene_gapped(self, db, project_id, pass_id):
        add_scene(db, pass_id, 0, "EXT.", "GARDEN", "DAY")
        add_scene(db, pass_id, 1, "INT.", "KITCHEN", "NIGHT")
        link_truth(db, project_id, [
            truth_scene(0, "INT.", "SPACESHIP", "NIGHT"),
            truth_scene(1, "EXT.", "GARDEN", "DAY"),
            truth_scene(2, "INT.", "KITCHEN", "NIGHT"),
        ])
        report = align_pass(db, pass_id=pass_id)
        assert report["unmatched_truth"] == [0]
        assert report["unmatched_model"] == []
        assert [(p["model_idx"], p["truth_idx"])
                for p in report["scene_pairs"]] == [(0, 1), (1, 2)]
        assert report["slugline_accuracy"] == 1.0

    def test_unmatched_model_scene_gapped(self, db, project_id, pass_id):
        add_scene(db, pass_id, 0, "EXT.", "GARDEN", "DAY")
        add_scene(db, pass_id, 1, "INT.", "SUBMARINE", "NIGHT")
        add_scene(db, pass_id, 2, "INT.", "KITCHEN", "NIGHT")
        link_truth(db, project_id, [
            truth_scene(0, "EXT.", "GARDEN", "DAY"),
            truth_scene(1, "INT.", "KITCHEN", "NIGHT"),
        ])
        report = align_pass(db, pass_id=pass_id)
        assert report["unmatched_model"] == [1]
        assert report["unmatched_truth"] == []

    def test_no_truth_raises(self, db, pass_id):
        with pytest.raises(ValueError):
            align_pass(db, pass_id=pass_id)

    def test_unknown_pass_raises(self, db):
        with pytest.raises(ValueError):
            align_pass(db, pass_id=12345)

    def test_report_is_stored(self, db, project_id, pass_id):
        add_scene(db, pass_id, 0, "EXT.", "WOODS", "DAY")
        link_truth(db, project_id, [truth_scene(0, "EXT.", "WOODS", "DAY")])
        report = align_pass(db, pass_id=pass_id)
        stored = db.alignment_for_pass(pass_id)
        assert stored is not None
        assert stored["report"] == report

    def test_empty_pass_all_truth_unmatched(self, db, project_id, pass_id):
        link_truth(db, project_id, [truth_scene(0, "EXT.", "WOODS", "DAY")])
        report = align_pass(db, pass_id=pass_id)
        assert report["scene_pairs"] == []
        assert report["unmatched_truth"] == [0]
        assert report["slugline_accuracy"] == 0.0


class TestDialogueAlignment:
    def test_matched_lines_and_character_accuracy(self, db, project_id, pass_id):
        add_scene(db, pass_id, 0, "EXT.", "WOODS", "DAY")
        link_truth(db, project_id, [
            truth_scene(0, "EXT.", "WOODS", "DAY", dialogue=[
                ["MARA", "We shouldn't be out here this late."],
                ["JON", "You brought the map, right?"],
            ]),
        ])
        db.add_dialogue(pass_id=pass_id, shot_id=None, scene_id=None,
                        character="MARA",
                        text="We shouldn't be out here this late",
                        parenthetical="", start_s=1.0, end_s=3.0)
        db.add_dialogue(pass_id=pass_id, shot_id=None, scene_id=None,
                        character="VOICE (O.S.)",
                        text="The quick brown fox jumps over the lazy dog",
                        parenthetical="", start_s=4.0, end_s=6.0)
        report = align_pass(db, pass_id=pass_id)
        dia = report["dialogue"]
        assert dia["truth_lines"] == 2
        assert dia["matched"] == 1
        assert 0.0 < dia["avg_score"] <= 100.0
        assert dia["character_accuracy"] == 1.0

    def test_character_extension_stripped(self, db, project_id, pass_id):
        add_scene(db, pass_id, 0, "INT.", "CABIN", "NIGHT")
        link_truth(db, project_id, [
            truth_scene(0, "INT.", "CABIN", "NIGHT", dialogue=[
                ["VOICE", "Meet me at the old bridge at midnight."],
            ]),
        ])
        db.add_dialogue(pass_id=pass_id, shot_id=None, scene_id=None,
                        character="VOICE (O.S.)",
                        text="Meet me at the old bridge at midnight",
                        parenthetical="", start_s=0.5, end_s=2.0)
        dia = align_pass(db, pass_id=pass_id)["dialogue"]
        assert dia["matched"] == 1
        assert dia["character_accuracy"] == 1.0

    def test_wrong_speaker_counts_against_accuracy(self, db, project_id, pass_id):
        add_scene(db, pass_id, 0, "INT.", "CABIN", "NIGHT")
        link_truth(db, project_id, [
            truth_scene(0, "INT.", "CABIN", "NIGHT", dialogue=[
                ["JON", "Meet me at the old bridge at midnight."],
            ]),
        ])
        db.add_dialogue(pass_id=pass_id, shot_id=None, scene_id=None,
                        character="MARA",
                        text="Meet me at the old bridge at midnight",
                        parenthetical="", start_s=0.5, end_s=2.0)
        dia = align_pass(db, pass_id=pass_id)["dialogue"]
        assert dia["matched"] == 1
        assert dia["character_accuracy"] == 0.0

    def test_no_truth_dialogue(self, db, project_id, pass_id):
        add_scene(db, pass_id, 0, "INT.", "CABIN", "NIGHT")
        link_truth(db, project_id, [truth_scene(0, "INT.", "CABIN", "NIGHT")])
        dia = align_pass(db, pass_id=pass_id)["dialogue"]
        assert dia == {"truth_lines": 0, "matched": 0, "avg_score": 0.0,
                       "character_accuracy": None}


# --------------------------------------------------------------------------- #
# emit_auto_corrections
# --------------------------------------------------------------------------- #

class TestEmitAutoCorrections:
    def _seed(self, db, project_id, pass_id):
        s0 = add_scene(db, pass_id, 0, "EXT.", "WOODS", "DAY")
        s1 = add_scene(db, pass_id, 1, "EXT.", "CABIN", "DAY")
        link_truth(db, project_id, [
            truth_scene(0, "EXT.", "WOODED ROAD", "DAY"),
            truth_scene(1, "INT.", "CABIN", "NIGHT"),
        ])
        return s0, s1

    def test_single_field_diff_emits_specific_field(self, db, project_id, pass_id):
        s0, s1 = self._seed(db, project_id, pass_id)
        report = align_pass(db, pass_id=pass_id)
        record = mock.Mock(side_effect=range(1, 100))
        with inject_memory(record):
            count = emit_auto_corrections(db, pass_id=pass_id, report=report)

        assert count == 2
        assert record.call_count == 2
        by_entity = {c.kwargs["entity_id"]: c.kwargs
                     for c in record.call_args_list}
        loc = by_entity[s0]
        assert loc["field"] == "location"
        assert loc["model_value"] == "WOODS"
        assert loc["human_value"] == "WOODED ROAD"
        assert loc["entity_type"] == "scene"
        assert loc["source"] == "ground_truth"
        assert loc["project_id"] == project_id
        assert loc["pass_id"] == pass_id
        # first positional arg is the Database
        assert all(c.args[0] is db for c in record.call_args_list)

    def test_multi_field_diff_emits_slugline(self, db, project_id, pass_id):
        s0, s1 = self._seed(db, project_id, pass_id)
        report = align_pass(db, pass_id=pass_id)
        record = mock.Mock(return_value=1)
        with inject_memory(record):
            emit_auto_corrections(db, pass_id=pass_id, report=report)
        slug = [c.kwargs for c in record.call_args_list
                if c.kwargs["entity_id"] == s1][0]
        assert slug["field"] == "slugline"
        assert slug["model_value"] == "EXT. CABIN - DAY"
        assert slug["human_value"] == "INT. CABIN - NIGHT"
        assert slug["source"] == "ground_truth"

    def test_clean_report_emits_nothing(self, db, project_id, pass_id):
        add_scene(db, pass_id, 0, "EXT.", "WOODS", "DAY")
        link_truth(db, project_id, [truth_scene(0, "EXT.", "WOODS", "DAY")])
        report = align_pass(db, pass_id=pass_id)
        record = mock.Mock()
        with inject_memory(record):
            assert emit_auto_corrections(db, pass_id=pass_id, report=report) == 0
        record.assert_not_called()

    def test_unknown_pass_raises(self, db):
        with inject_memory(mock.Mock()):
            with pytest.raises(ValueError):
                emit_auto_corrections(db, pass_id=777, report={"scene_pairs": []})
