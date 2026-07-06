"""Offline tests for the Scripty dashboard server (no network, no ffmpeg).

Seeds a tmp SQLite database with one project/pass/shots and exercises every
endpoint through fastapi.testclient. LLM/brain boundaries never fire:
SCRIPTY_PROVIDER is pinned to 'mock' so distill uses a mock/deterministic path.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from scripty.core.db import Database
from scripty.server.app import create_app

FAKE_JPEG = b"\xff\xd8\xff\xdb" + b"scripty-fake-jpeg" * 4
FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 128


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "scripty-home"
    monkeypatch.setenv("SCRIPTY_HOME", str(root))
    monkeypatch.setenv("SCRIPTY_PROVIDER", "mock")
    return root


@pytest.fixture()
def seed(home: Path, tmp_path: Path) -> SimpleNamespace:
    db_path = tmp_path / "scripty-test.db"
    video = tmp_path / "night-road.mp4"
    video.write_bytes(FAKE_MP4)

    keyframe_dir = home / "projects" / "night-road" / "keyframes" / "pass1"
    keyframe_dir.mkdir(parents=True)
    keyframe = keyframe_dir / "shot0000_f1.jpg"
    keyframe.write_bytes(FAKE_JPEG)

    db = Database(db_path)
    pid = db.create_project(name="Night Road", slug="night-road",
                            video_path=str(video), duration=6.0, fps=24.0,
                            width=640, height=360)
    pass_id = db.create_pass(project_id=pid, provider="mock",
                             transcriber="none")
    shot1 = db.add_shot(pass_id=pass_id, idx=0, start_s=0.0, end_s=3.0,
                        scale="MS", subject="CAMP FIRE", angle="EYE LEVEL",
                        move="STATIC", int_ext="EXT.", location="WOODS",
                        time_of_day="NIGHT", action_text="Flames snap.",
                        characters=["MARA"], mood="wary", confidence=0.8,
                        keyframes=[str(keyframe)], raw={})
    shot2 = db.add_shot(pass_id=pass_id, idx=1, start_s=3.0, end_s=6.0,
                        scale="WS", subject="TREE LINE", angle="LOW ANGLE",
                        move="PAN", int_ext="EXT.", location="WOODS",
                        time_of_day="NIGHT", action_text="Wind in the pines.",
                        characters=[], mood="ominous", confidence=0.3,
                        keyframes=[], raw={})
    scene = db.add_scene(pass_id=pass_id, idx=0, int_ext="EXT.",
                         location="WOODS", time_of_day="NIGHT",
                         shot_ids=[shot1, shot2], synopsis="Flames snap.")
    db.add_dialogue(pass_id=pass_id, shot_id=shot1, scene_id=scene,
                    character="MARA", text="Stay close.", parenthetical="",
                    start_s=1.0, end_s=2.0)
    prompt_id = db.add_describe_prompt(pass_id=pass_id, scene_id=scene,
                                       shot_id=shot1, revision=1,
                                       target="generic",
                                       prompt="Medium shot of a camp fire.",
                                       continuity={})
    db.add_artifact(project_id=pid, kind="source_video", ref=str(video))
    db.add_artifact(project_id=pid, kind="generated_script",
                    ref=str(tmp_path / "pass1.fountain"), pass_id=pass_id)
    db.finish_pass(pass_id, "complete", {
        "n_shots": 2, "n_scenes": 1, "n_dialogue": 1, "mean_confidence": 0.55,
        "agreement": {"corrections_checked": 0, "now_agreeing": 0,
                      "agreement_rate": None, "by_field": {}},
    })
    db.close()
    return SimpleNamespace(db_path=db_path, pid=pid, pass_id=pass_id,
                           shot1=shot1, shot2=shot2, scene=scene,
                           prompt_id=prompt_id, video=video,
                           keyframe=keyframe)


@pytest.fixture()
def client(seed: SimpleNamespace) -> Iterator[TestClient]:
    app = create_app(db_path=seed.db_path)
    with TestClient(app) as test_client:
        yield test_client


# ---- static shell ----------------------------------------------------------


def test_index_and_static_served(client: TestClient) -> None:
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "SCRIPTY" in res.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


# ---- projects / passes ------------------------------------------------------


def test_projects_list_and_detail(client: TestClient, seed: SimpleNamespace) -> None:
    listing = client.get("/api/projects").json()
    assert [p["id"] for p in listing] == [seed.pid]

    detail = client.get(f"/api/projects/{seed.pid}").json()
    assert detail["project"]["name"] == "Night Road"
    assert len(detail["passes"]) == 1
    assert detail["passes"][0]["metrics"]["n_shots"] == 2
    assert {a["kind"] for a in detail["artifacts"]} == {
        "source_video", "generated_script"}
    assert detail["truth"] is False

    assert client.get("/api/projects/9999").status_code == 404


def test_pass_detail(client: TestClient, seed: SimpleNamespace) -> None:
    data = client.get(f"/api/passes/{seed.pass_id}").json()
    assert data["pass"]["status"] == "complete"
    assert [s["idx"] for s in data["shots"]] == [0, 1]
    assert data["shots"][0]["characters"] == ["MARA"]
    assert len(data["scenes"]) == 1
    assert data["dialogue"][0]["text"] == "Stay close."
    assert data["metrics"]["mean_confidence"] == 0.55

    assert client.get("/api/passes/9999").status_code == 404


def test_script_rebuilt_live(client: TestClient, seed: SimpleNamespace) -> None:
    data = client.get(f"/api/passes/{seed.pass_id}/script").json()
    assert "WOODS" in data["fountain"]
    assert "MARA" in data["fountain"]
    assert "Stay close." in data["fountain"]
    assert "WOODS" in data["html"]


def test_cutlog_plaintext(client: TestClient, seed: SimpleNamespace) -> None:
    res = client.get(f"/api/passes/{seed.pass_id}/cutlog")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/plain")
    assert "MS CAMP FIRE" in res.text
    assert "WS TREE LINE" in res.text


def test_prompts_with_revision_filter(client: TestClient,
                                      seed: SimpleNamespace) -> None:
    all_prompts = client.get(f"/api/passes/{seed.pass_id}/prompts").json()
    assert [p["id"] for p in all_prompts] == [seed.prompt_id]
    rev1 = client.get(
        f"/api/passes/{seed.pass_id}/prompts", params={"revision": 1}).json()
    assert len(rev1) == 1
    rev2 = client.get(
        f"/api/passes/{seed.pass_id}/prompts", params={"revision": 2}).json()
    assert rev2 == []


def test_alignment_404_then_report(client: TestClient,
                                   seed: SimpleNamespace) -> None:
    assert client.get(f"/api/passes/{seed.pass_id}/alignment").status_code == 404
    side = Database(seed.db_path)
    truth_id = side.add_truth(project_id=seed.pid, script_path="/tmp/x.fountain",
                              parsed={"title": "NIGHT ROAD", "scenes": []})
    side.add_alignment(pass_id=seed.pass_id, truth_id=truth_id,
                       report={"slugline_accuracy": 1.0, "scene_pairs": []})
    side.close()
    report = client.get(f"/api/passes/{seed.pass_id}/alignment").json()
    assert report["slugline_accuracy"] == 1.0


# ---- corrections -------------------------------------------------------------


def _correction_body(seed: SimpleNamespace, **overrides: object) -> dict:
    body = {
        "project_id": seed.pid, "pass_id": seed.pass_id,
        "entity_type": "shot", "entity_id": seed.shot1,
        "field": "location", "model_value": "WOODS",
        "human_value": "WOODED ROAD", "note": "", "scope": "project",
    }
    body.update(overrides)
    return body


def test_correction_applies_to_live_shot_row(client: TestClient,
                                             seed: SimpleNamespace) -> None:
    res = client.post("/api/corrections", json=_correction_body(seed))
    assert res.status_code == 200
    correction_id = res.json()["id"]
    assert correction_id >= 1

    shots = client.get(f"/api/passes/{seed.pass_id}").json()["shots"]
    assert shots[0]["location"] == "WOODED ROAD"

    side = Database(seed.db_path)
    stored = side.corrections(project_id=seed.pid, field="location")
    side.close()
    assert stored and stored[0]["human_value"] == "WOODED ROAD"
    assert stored[0]["source"] == "human"


def test_correction_invalid_field_rejected(client: TestClient,
                                           seed: SimpleNamespace) -> None:
    res = client.post("/api/corrections",
                      json=_correction_body(seed, field="banana"))
    assert res.status_code == 400
    res = client.post("/api/corrections",
                      json=_correction_body(seed, entity_type="spaceship"))
    assert res.status_code == 400


def test_correction_unknown_project_or_pass_404(client: TestClient,
                                                seed: SimpleNamespace) -> None:
    assert client.post("/api/corrections",
                       json=_correction_body(seed, project_id=9999)
                       ).status_code == 404
    assert client.post("/api/corrections",
                       json=_correction_body(seed, pass_id=9999)
                       ).status_code == 404


# ---- lessons ------------------------------------------------------------------


def test_lessons_distill_list_deactivate(client: TestClient,
                                         seed: SimpleNamespace) -> None:
    client.post("/api/corrections", json=_correction_body(seed))
    out = client.post("/api/lessons/distill",
                      json={"project_id": seed.pid}).json()
    assert out["count"] >= 1

    lessons = client.get("/api/lessons",
                         params={"project_id": seed.pid}).json()
    assert lessons and any(l["field"] == "location" for l in lessons)

    lesson_id = lessons[0]["id"]
    assert client.post(f"/api/lessons/{lesson_id}/deactivate"
                       ).json()["ok"] is True
    remaining = client.get("/api/lessons",
                           params={"project_id": seed.pid}).json()
    assert lesson_id not in [l["id"] for l in remaining]

    assert client.post("/api/lessons/9999/deactivate").status_code == 404


# ---- metrics -------------------------------------------------------------------


def test_metrics_learning_curve(client: TestClient,
                                seed: SimpleNamespace) -> None:
    series = client.get(f"/api/metrics/{seed.pid}").json()
    assert len(series) == 1
    entry = series[0]
    assert entry["pass_id"] == seed.pass_id
    assert entry["number"] == 1
    assert entry["metrics"]["n_shots"] == 2
    assert entry["metrics"]["agreement"]["agreement_rate"] is None

    assert client.get("/api/metrics/9999").status_code == 404


# ---- media ----------------------------------------------------------------------


def test_video_served_from_db_path(client: TestClient,
                                   seed: SimpleNamespace) -> None:
    res = client.get(f"/media/video/{seed.pid}")
    assert res.status_code == 200
    assert res.content == FAKE_MP4
    assert res.headers["content-type"].startswith("video/")
    assert client.get("/media/video/9999").status_code == 404

    ranged = client.get(f"/media/video/{seed.pid}",
                        headers={"Range": "bytes=0-3"})
    assert ranged.status_code in (200, 206)


def test_video_missing_file_404(client: TestClient,
                                seed: SimpleNamespace) -> None:
    seed.video.unlink()
    assert client.get(f"/media/video/{seed.pid}").status_code == 404


def test_keyframe_served_inside_home(client: TestClient,
                                     seed: SimpleNamespace) -> None:
    res = client.get("/media/keyframe", params={"path": str(seed.keyframe)})
    assert res.status_code == 200
    assert res.content == FAKE_JPEG
    assert res.headers["content-type"] == "image/jpeg"


def test_keyframe_path_traversal_rejected(client: TestClient,
                                          home: Path) -> None:
    for evil in ("../../etc/passwd", "/etc/passwd",
                 str(home / "projects" / ".." / ".." / "secret.txt"),
                 "~/secret.txt"):
        res = client.get("/media/keyframe", params={"path": evil})
        assert res.status_code == 403, evil
    missing = home / "projects" / "night-road" / "nope.jpg"
    assert client.get("/media/keyframe",
                      params={"path": str(missing)}).status_code == 404


# ---- compare bay (quad view) assets ------------------------------------------


def test_quad_static_assets_served(client: TestClient) -> None:
    js = client.get("/static/quad.js")
    assert js.status_code == 200
    assert "COMPARE BAY" in js.text
    css = client.get("/static/quad.css")
    assert css.status_code == 200
    assert "quadLayout" in css.text


def test_index_references_quad_assets(client: TestClient) -> None:
    html = client.get("/").text
    assert "/static/quad.js" in html
    assert "/static/quad.css" in html
    assert 'id="quadLayout"' in html
    assert 'id="viewToggle"' in html


def test_fountain_classifier_asset_served(client: TestClient) -> None:
    res = client.get("/static/fountain.js")
    assert res.status_code == 200
    assert "scriptyFountain" in res.text
    assert "/static/fountain.js" in client.get("/").text


# ---- local-only guards (Host / Origin) ----------------------------------------


def test_nonlocal_host_header_rejected(client: TestClient) -> None:
    # DNS-rebinding style request: attacker hostname now resolving to 127.0.0.1
    res = client.get("/api/projects", headers={"Host": "evil.example"})
    assert res.status_code == 400
    for ok_host in ("127.0.0.1:8787", "localhost", "[::1]:9000"):
        assert client.get("/api/projects",
                          headers={"Host": ok_host}).status_code == 200


def test_cross_origin_state_change_rejected(client: TestClient,
                                            seed: SimpleNamespace) -> None:
    body = _correction_body(seed)
    res = client.post("/api/corrections", json=body,
                      headers={"Origin": "https://evil.example"})
    assert res.status_code == 403
    res = client.post("/api/corrections", json=body,
                      headers={"Origin": "null"})
    assert res.status_code == 403
    # same-origin browser POSTs (Electron shell / loopback tab) still work
    res = client.post("/api/corrections", json=body,
                      headers={"Origin": "http://127.0.0.1:8787"})
    assert res.status_code == 200
    # cross-origin GETs are read-only and remain allowed
    assert client.get("/api/projects",
                      headers={"Origin": "https://evil.example"}
                      ).status_code == 200
