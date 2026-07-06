"""Offline tests for the phase-2 ingest + background-pass endpoints.

No network, no LLM: SCRIPTY_PROVIDER is pinned to 'mock'. ffmpeg IS available
locally, so the happy paths chew on a real 2x1s synthetic film (built once per
session); the concurrency/progress tests monkeypatch pipeline.run_pass.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from scripty.server.app import create_app


@pytest.fixture(scope="session")
def film(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from scripty.ingest.testfilm import make_test_film

    out = tmp_path_factory.mktemp("film") / "two-shot.mp4"
    return make_test_film(out, shots=2, shot_seconds=1.0)


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "scripty-home"
    monkeypatch.setenv("SCRIPTY_HOME", str(root))
    monkeypatch.setenv("SCRIPTY_PROVIDER", "mock")
    monkeypatch.setenv("SCRIPTY_TRANSCRIBER", "none")
    return root


@pytest.fixture()
def client(home: Path, tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(db_path=tmp_path / "ingest-test.db")
    with TestClient(app) as test_client:
        yield test_client


def _make_project(client: TestClient, film: Path) -> int:
    res = client.post("/api/projects/from-path",
                      json={"path": str(film), "name": "Ingest Film"})
    assert res.status_code == 200, res.text
    return int(res.json()["project_id"])


# ---- POST /api/projects/from-path -------------------------------------------


def test_from_path_creates_project(client: TestClient, film: Path,
                                   home: Path) -> None:
    # NOTE: the film lives OUTSIDE SCRIPTY_HOME and must still be accepted:
    # Scripty is a local single-user app that ingests the user's own files,
    # so from-path only checks existence + extension (by design, per spec).
    assert not str(film).startswith(str(home))
    res = client.post("/api/projects/from-path",
                      json={"path": str(film), "name": "Two Shot"})
    assert res.status_code == 200
    data = res.json()
    pid = data["project_id"]
    assert data["project"]["id"] == pid
    assert data["project"]["name"] == "Two Shot"
    assert data["project"]["video_path"] == str(film)
    assert data["project"]["duration"] > 0

    detail = client.get(f"/api/projects/{pid}").json()
    assert {a["kind"] for a in detail["artifacts"]} == {"source_video"}
    assert detail["truth"] is False


def test_from_path_rejections(client: TestClient, tmp_path: Path) -> None:
    missing = tmp_path / "nope.mp4"
    res = client.post("/api/projects/from-path", json={"path": str(missing)})
    assert res.status_code == 400

    bad_ext = tmp_path / "clip.gif"
    bad_ext.write_bytes(b"GIF89a")
    res = client.post("/api/projects/from-path", json={"path": str(bad_ext)})
    assert res.status_code == 400

    directory = tmp_path / "dir.mp4"
    directory.mkdir()
    res = client.post("/api/projects/from-path", json={"path": str(directory)})
    assert res.status_code == 400

    junk = tmp_path / "junk.mp4"  # right extension, not actually a video
    junk.write_bytes(b"this is not an mp4 file")
    res = client.post("/api/projects/from-path", json={"path": str(junk)})
    assert res.status_code == 400


# ---- POST /api/projects/upload ------------------------------------------------


def test_upload_sanitizes_and_dedupes(client: TestClient, film: Path,
                                      home: Path) -> None:
    payload = film.read_bytes()
    uploads = home / "uploads"

    res = client.post("/api/projects/upload",
                      files={"file": ("../../evil.mp4", payload, "video/mp4")})
    assert res.status_code == 200, res.text
    first = res.json()["project"]
    saved = Path(first["video_path"])
    assert saved == uploads / "evil.mp4"  # traversal basename-ed away
    assert saved.is_file()

    res = client.post("/api/projects/upload",
                      files={"file": ("evil.mp4", payload, "video/mp4")})
    assert res.status_code == 200
    second = res.json()["project"]
    assert Path(second["video_path"]) == uploads / "evil-2.mp4"
    assert (uploads / "evil-2.mp4").is_file()
    assert second["id"] != first["id"]


def test_upload_rejects_bad_names_and_exts(client: TestClient,
                                           home: Path) -> None:
    for name in ("..", ".", "..."):
        res = client.post("/api/projects/upload",
                          files={"file": (name, b"x", "video/mp4")})
        assert res.status_code == 400, name
    res = client.post("/api/projects/upload",
                      files={"file": ("evil.gif", b"x", "image/gif")})
    assert res.status_code == 400
    uploads = home / "uploads"
    assert not uploads.exists() or list(uploads.iterdir()) == []


def test_save_upload_rejects_control_char_names(home: Path) -> None:
    # a raw NUL survives os.path.basename and the extension checks but would
    # explode in dest.open() as an unhandled 500 — must be a clean 400.
    # (httpx percent-encodes filenames, so exercise the helper directly the
    # way starlette's own multipart parser would hand it over.)
    import io

    from fastapi import UploadFile
    from starlette.exceptions import HTTPException as StarletteHTTPException

    from scripty.server.ingest_api import VIDEO_EXTS, _save_upload

    upload = UploadFile(io.BytesIO(b"x"), filename="foo\x00bar.mp4")
    with pytest.raises(StarletteHTTPException) as excinfo:
        _save_upload(upload, VIDEO_EXTS, "video")
    assert excinfo.value.status_code == 400
    uploads = home / "uploads"
    assert not uploads.exists() or list(uploads.iterdir()) == []


def test_upload_size_capped(client: TestClient, home: Path,
                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCRIPTY_MAX_UPLOAD_MB", "1")
    big = b"\x00" * (1024 * 1024 + 1)
    res = client.post("/api/projects/upload",
                      files={"file": ("big.mp4", big, "video/mp4")})
    assert res.status_code == 413
    uploads = home / "uploads"  # partial file must not be left behind
    assert not uploads.exists() or list(uploads.iterdir()) == []


def test_cross_origin_upload_and_pass_rejected(client: TestClient,
                                               film: Path) -> None:
    # multipart POST is a preflight-less "simple" request — must be blocked
    res = client.post(
        "/api/projects/upload",
        files={"file": ("clip.mp4", film.read_bytes(), "video/mp4")},
        headers={"Origin": "https://evil.example"})
    assert res.status_code == 403
    # no-body POST (also preflight-less) to the pass runner — blocked too
    res = client.post("/api/projects/1/passes",
                      headers={"Origin": "https://evil.example"})
    assert res.status_code == 403
    # DNS-rebinding style Host header is rejected outright
    res = client.post("/api/projects/from-path", json={"path": str(film)},
                      headers={"Host": "rebind.evil.example"})
    assert res.status_code == 400
    assert res.json()["detail"] == "invalid host header"


# ---- truth linking --------------------------------------------------------------


def test_truth_from_path_and_get(client: TestClient, film: Path,
                                 tmp_path: Path) -> None:
    pid = _make_project(client, film)
    assert client.get(f"/api/projects/{pid}/truth").status_code == 404

    script = tmp_path / "known.fountain"
    script.write_text("INT. CABIN - NIGHT\n\nA fire crackles.\n\n"
                      "MARA\nStay close.\n", encoding="utf-8")
    res = client.post(f"/api/projects/{pid}/truth/from-path",
                      json={"path": str(script)})
    assert res.status_code == 200
    assert res.json()["truth_id"] >= 1

    got = client.get(f"/api/projects/{pid}/truth").json()
    assert got["script_path"] == str(script.resolve())
    assert got["parsed"]["scenes"], "parsed scenes should be non-empty"

    bad_ext = tmp_path / "known.pdf"
    bad_ext.write_bytes(b"%PDF")
    assert client.post(f"/api/projects/{pid}/truth/from-path",
                       json={"path": str(bad_ext)}).status_code == 400
    assert client.post(f"/api/projects/{pid}/truth/from-path",
                       json={"path": str(tmp_path / "gone.txt")}
                       ).status_code == 400
    assert client.post("/api/projects/9999/truth/from-path",
                       json={"path": str(script)}).status_code == 404
    assert client.get("/api/projects/9999/truth").status_code == 404


def test_truth_upload(client: TestClient, film: Path, home: Path) -> None:
    pid = _make_project(client, film)
    body = b"EXT. WOODS - NIGHT\n\nWind in the pines.\n"
    res = client.post(f"/api/projects/{pid}/truth/upload",
                      files={"file": ("../real.txt", body, "text/plain")})
    assert res.status_code == 200, res.text
    got = client.get(f"/api/projects/{pid}/truth").json()
    assert got["script_path"] == str(home / "uploads" / "real.txt")
    assert (home / "uploads" / "real.txt").read_bytes() == body


# ---- background pass runs --------------------------------------------------------


def test_pass_run_to_completion(client: TestClient, film: Path) -> None:
    pid = _make_project(client, film)
    res = client.post(f"/api/projects/{pid}/passes",
                      json={"provider": "mock", "transcriber": "none"})
    assert res.status_code == 202
    assert res.json() == {"started": True}

    deadline = time.time() + 90.0
    status = ""
    while time.time() < deadline:
        passes = client.get(f"/api/projects/{pid}").json()["passes"]
        status = passes[-1]["status"] if passes else ""
        if status in ("complete", "failed"):
            break
        time.sleep(0.25)
    assert status == "complete"

    progress = client.get(f"/api/projects/{pid}/progress").json()
    assert progress["running"] is False
    assert 0 < len(progress["lines"]) <= 30
    assert any("complete" in line for line in progress["lines"])

    passes = client.get(f"/api/projects/{pid}").json()["passes"]
    assert passes[-1]["metrics"]["n_shots"] >= 1

    assert client.post("/api/projects/9999/passes").status_code == 404


def test_pass_conflict_409_while_running(client: TestClient, film: Path,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    import scripty.pipeline as pipeline_mod

    pid = _make_project(client, film)
    started, release = threading.Event(), threading.Event()

    def slow_run_pass(db: object, project_id: int, *, provider_name=None,
                      transcriber_name=None, brain_name=None,
                      progress=None) -> int:
        if progress is not None:
            progress("stub pass running")
        started.set()
        release.wait(timeout=30.0)
        return 0

    monkeypatch.setattr(pipeline_mod, "run_pass", slow_run_pass)

    assert client.post(f"/api/projects/{pid}/passes").status_code == 202
    assert started.wait(timeout=10.0)
    res = client.post(f"/api/projects/{pid}/passes")
    assert res.status_code == 409

    release.set()
    client.app.state.pass_threads[pid].join(timeout=10.0)
    # once the run finishes, a new pass may start again
    started.clear()
    assert client.post(f"/api/projects/{pid}/passes").status_code == 202
    assert started.wait(timeout=10.0)
    client.app.state.pass_threads[pid].join(timeout=10.0)


def test_progress_capped_and_tailed(client: TestClient, film: Path,
                                    monkeypatch: pytest.MonkeyPatch) -> None:
    import scripty.pipeline as pipeline_mod

    pid = _make_project(client, film)

    def chatty_run_pass(db: object, project_id: int, *, provider_name=None,
                        transcriber_name=None, brain_name=None,
                        progress=None) -> int:
        for i in range(250):
            progress(f"line {i}")
        return 0

    monkeypatch.setattr(pipeline_mod, "run_pass", chatty_run_pass)
    assert client.post(f"/api/projects/{pid}/passes").status_code == 202
    client.app.state.pass_threads[pid].join(timeout=10.0)

    assert len(client.app.state.progress[pid]) == 200  # cap
    got = client.get(f"/api/projects/{pid}/progress").json()
    assert got["running"] is False
    assert got["lines"] == [f"line {i}" for i in range(220, 250)]  # last 30


def test_progress_empty_and_unknown(client: TestClient, film: Path) -> None:
    pid = _make_project(client, film)
    got = client.get(f"/api/projects/{pid}/progress").json()
    assert got == {"running": False, "lines": []}
    assert client.get("/api/projects/9999/progress").status_code == 404


def test_stale_running_pass_swept_at_startup(home: Path, tmp_path: Path,
                                             film: Path) -> None:
    from scripty.core.db import Database

    db_path = tmp_path / "stale.db"
    db = Database(db_path)
    pid = db.create_project(name="Stale", slug="stale", video_path=str(film),
                            duration=2.0, fps=24.0, width=320, height=180)
    db.create_pass(project_id=pid, provider="mock")  # left status='running'
    db.close()

    # relaunch: the stale row must be failed, not brick RUN PASS forever
    with TestClient(create_app(db_path=db_path)) as client:
        passes = client.get(f"/api/projects/{pid}").json()["passes"]
        assert passes[-1]["status"] == "failed"
        assert "interrupted" in passes[-1]["metrics"]["error"]
        got = client.get(f"/api/projects/{pid}/progress").json()
        assert got["running"] is False


# ---- truth linking triggers alignment ---------------------------------------------


def test_truth_link_aligns_completed_passes(client: TestClient, film: Path,
                                            tmp_path: Path) -> None:
    from scripty.core.db import Database

    pid = _make_project(client, film)
    side = Database(tmp_path / "ingest-test.db")
    pass_id = side.create_pass(project_id=pid, provider="mock")
    side.add_scene(pass_id=pass_id, idx=0, int_ext="INT.", location="CABIN",
                   time_of_day="NIGHT", shot_ids=[], synopsis="")
    side.finish_pass(pass_id, "complete", {})
    side.close()
    assert client.get(f"/api/passes/{pass_id}/alignment").status_code == 404

    # drop-film -> run-pass -> drop-script must yield verdicts immediately
    script = tmp_path / "late-truth.fountain"
    script.write_text("INT. CABIN - NIGHT\n\nA fire crackles.\n",
                      encoding="utf-8")
    res = client.post(f"/api/projects/{pid}/truth/from-path",
                      json={"path": str(script)})
    assert res.status_code == 200
    report = client.get(f"/api/passes/{pass_id}/alignment").json()
    assert report["scene_pairs"], "linking truth should have aligned the pass"
    assert report["scene_pairs"][0]["diffs"] == {}

    # re-linking a DIFFERENT truth refreshes the alignment (no stale pairing)
    script2 = tmp_path / "late-truth-2.fountain"
    script2.write_text("EXT. RIDGE - DAY\n\nWind.\n", encoding="utf-8")
    assert client.post(f"/api/projects/{pid}/truth/from-path",
                       json={"path": str(script2)}).status_code == 200
    report2 = client.get(f"/api/passes/{pass_id}/alignment").json()
    assert report2 != report
