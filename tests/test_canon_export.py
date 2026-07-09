"""export-canon pipe to director-bot."""
from __future__ import annotations

import json
from pathlib import Path

from scripty.canon_export import build_canon_export, write_canon_export
from scripty.core.db import Database


def _seed_mini(db: Database) -> tuple[int, int]:
    pid = db.create_project(
        name="Canon Export Film",
        slug="canon-export-film",
        video_path="/tmp/fake.mp4",
        duration=10.0, fps=24.0, width=1280, height=720,
    )
    pass_id = db.create_pass(project_id=pid, provider="mock")
    sid = db.add_shot(
        pass_id=pass_id, idx=0, start_s=0.0, end_s=2.0,
        scale="MS", subject="DOOR", angle="EYE LEVEL", move="STATIC",
        int_ext="INT.", location="HALL", time_of_day="NIGHT",
        action_text="A door opens.", characters=["ALI"], mood="tense",
        confidence=0.7, keyframes=[], raw={},
    )
    db.add_scene(
        pass_id=pass_id, idx=0, int_ext="INT.", location="HALL",
        time_of_day="NIGHT", shot_ids=[sid], synopsis="Door opens.",
    )
    db.add_dialogue(
        pass_id=pass_id, shot_id=sid, scene_id=None,
        character="ALI", text="Hello?", parenthetical="", start_s=0.5, end_s=1.0,
    )
    db.finish_pass(pass_id, "complete", {"shots": 1})
    return pid, pass_id


def test_build_canon_export(tmp_path: Path):
    db = Database(tmp_path / "s.db")
    pid, pass_id = _seed_mini(db)
    bundle = build_canon_export(
        db, pid, pass_id,
        tier="S",
        directors=["Ada"],
        genres=["thriller"],
        theme="thresholds",
    )
    assert bundle["work"]["tier"] == "S"
    assert bundle["work"]["directors"] == ["Ada"]
    assert len(bundle["scene_cards"]) == 1
    assert len(bundle["shot_moments"]) == 1
    assert bundle["shot_moments"][0]["dialogue"][0]["text"] == "Hello?"
    assert bundle["decision_digests"]
    out = write_canon_export(bundle, tmp_path / "out.json")
    loaded = json.loads(out.read_text())
    assert loaded["work"]["slug"] == "canon-export-film"


def test_export_latest_complete(tmp_path: Path):
    db = Database(tmp_path / "s.db")
    pid, _ = _seed_mini(db)
    bundle = build_canon_export(db, pid)  # no pass_id
    assert bundle["pass"]["status"] == "complete"
