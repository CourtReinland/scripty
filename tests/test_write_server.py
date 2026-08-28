"""HTTP tests for the writer dashboard and /api/write."""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from scripty.server.app import create_app


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SCRIPTY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SCRIPTY_PROVIDER", "mock")
    app = create_app(db_path=tmp_path / "write.db")
    with TestClient(app) as test_client:
        yield test_client


def test_writer_shell_is_home(client: TestClient) -> None:
    html = client.get("/").text
    assert "fiction trainer" in html
    assert "Better — promote challenger" in html
    assert client.get("/static/write.js").status_code == 200
    assert client.get("/static/write.css").status_code == 200


def test_http_loop_first_draft_better_worse_and_desk_lessons(
        client: TestClient) -> None:
    desks = client.get("/api/write/desks").json()
    slugs = {d["slug"] for d in desks}
    assert {"horror", "literary", "romance", "thriller",
            "slice_of_life", "science_fiction", "custom"} <= slugs
    peek = client.get("/api/write/desks/thriller").json()
    assert peek["desk"]["slug"] == "thriller"

    first = client.post("/api/write/sessions", json={
        "genre": "horror",
        "tone": "cold",
        "length": "short",
        "summary": "Footprints on the lighthouse stairs, one size smaller.",
        "draft": True,
    }).json()
    sid = first["session"]["id"]
    champ1 = first["champion"]["id"]
    assert first["challenger"] is None
    assert first["champion"]["text"]

    chall = client.post(f"/api/write/sessions/{sid}/generate").json()
    assert chall["challenger"]["id"] != champ1
    assert chall["challenger"]["text"] != chall["champion"]["text"]
    assert chall["challenger"]["seed"] != chall["champion"]["seed"]

    better = client.post(f"/api/write/sessions/{sid}/judge",
                         json={"result": "better", "note": "colder"}).json()
    assert better["champion"]["id"] == chall["challenger"]["id"]
    assert better["challenger"] is None
    assert better["lessons"]

    again = client.post(f"/api/write/sessions/{sid}/generate").json()
    worse = client.post(f"/api/write/sessions/{sid}/judge",
                        json={"result": "worse"}).json()
    assert worse["champion"]["id"] == better["champion"]["id"]
    assert worse["challenger"] is None

    other = client.post("/api/write/sessions", json={
        "genre": "literary",
        "tone": "spare",
        "length": "short",
        "summary": "Two siblings divide a house after a funeral.",
        "draft": True,
    }).json()
    assert other["lessons"] == []
    assert other["desk"]["slug"] == "literary"

    listed = client.get("/api/write/sessions").json()
    assert {row["id"] for row in listed} >= {sid, other["session"]["id"]}


def test_http_rejects_bad_genre_and_missing_session(client: TestClient) -> None:
    assert client.post("/api/write/sessions", json={
        "genre": "not-a-desk", "summary": "x",
    }).status_code == 400
    assert client.get("/api/write/sessions/999").status_code == 404
    assert client.post("/api/write/sessions/999/generate").status_code == 404


def test_http_stores_user_reference_without_echoing_text(client: TestClient) -> None:
    phrase = "The violet kettle whistled twice at dawn while the porch cats argued."
    res = client.post("/api/write/refs", json={
        "genre": "horror",
        "kind": "user_excerpt",
        "title": "my paragraph",
        "text": phrase,
    })
    assert res.status_code == 200
    body = res.json()
    assert body["kind"] == "user_excerpt"
    assert "text" not in body
    assert phrase not in str(body)
    started = client.post("/api/write/sessions", json={
        "genre": "horror",
        "tone": "cold",
        "length": "short",
        "summary": "A keeper waits on the stairs.",
        "draft": True,
        "reference_text": phrase,
        "reference_title": "start chunk",
    }).json()
    dumped = str(started)
    assert phrase not in dumped
    assert "violet kettle" not in dumped
    assert started["refs"]
    assert "text" not in started["refs"][0]
    html = client.get("/").text
    assert "Optional reference" in html
    assert "never shown again" in html
    assert client.post("/api/write/refs", json={
        "genre": "horror", "kind": "stolen_novel",
        "title": "no", "text": "no",
    }).status_code == 400
