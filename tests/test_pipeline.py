"""Offline tests for scripty.pipeline, scripty.cli, and scripty.brain.

Every LLM / ffmpeg / cross-module boundary is mocked (sys.modules injection),
so these tests pass standalone with no API key, no network, and even while
sibling modules are still being built.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import mock

import pytest
from typer.testing import CliRunner

from scripty import cli, pipeline
from scripty.brain import AnthropicBrain, MockBrain, XaiBrain, get_brain
from scripty.core import config
from scripty.core.db import Database
from scripty.core.models import (
    MediaInfo,
    RecallBundle,
    ShotAnalysis,
    TranscriptSegment,
)

runner = CliRunner()


# --------------------------------------------------------------------------- #
# helpers & fixtures
# --------------------------------------------------------------------------- #

def _module(name: str, **attrs: object) -> ModuleType:
    mod = ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


class FakeProvider:
    name = "mock"

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def analyze_shot(self, frames, ctx, recall):
        self.calls.append((frames, ctx, recall))
        return ShotAnalysis(subject=f"THING {ctx.shot_index}", location="CAMP",
                            action_text=f"Action {ctx.shot_index}.", confidence=0.8)

    def critique_frames(self, original, generated, context):
        return ""


class FakeTranscriber:
    name = "none"

    def __init__(self, segments=None) -> None:
        self._segments = segments or []

    def transcribe(self, wav):
        return list(self._segments)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    root = tmp_path / "scripty-home"
    monkeypatch.setenv("SCRIPTY_HOME", str(root))
    return root


@pytest.fixture()
def db(home):
    database = Database(config.db_path())
    yield database
    database.close()


@pytest.fixture()
def boundary(monkeypatch, home):
    """Replace every cross-module boundary with deterministic fakes."""
    provider = FakeProvider()
    transcriber = FakeTranscriber(
        [TranscriptSegment(0.2, 1.0, "Hello there.", "SPEAKER 1")])

    def fake_make_test_film(out, shots=5, shot_seconds=3.0):
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"demo-film")
        return path

    def fake_dialogue_from_segments(segments, shots):
        if not shots or not segments:
            return []
        return [{"shot_id": shots[0]["id"], "character": "SPEAKER 1",
                 "text": "Hello there.", "parenthetical": "",
                 "start_s": 0.2, "end_s": 1.0}]

    def fake_group_scenes(shots):
        return [{"idx": 0, "int_ext": "EXT.", "location": "CAMP",
                 "time_of_day": "DAY", "shot_ids": [s["id"] for s in shots],
                 "synopsis": "Action 0."}]

    def fake_generate_prompts(scenes, shots, dialogue, brain=None,
                              lessons=None, target="generic"):
        out = [{"scene_id": s["id"], "shot_id": None, "revision": 1,
                "target": target, "prompt": "scene prompt", "continuity": {}}
               for s in scenes]
        out += [{"scene_id": None, "shot_id": s["id"], "revision": 1,
                 "target": target, "prompt": f"shot {s['idx']}", "continuity": {}}
                for s in shots]
        return out

    ingest_mod = _module(
        "scripty.ingest",
        probe=mock.Mock(return_value=MediaInfo(path="v", duration=4.0, fps=24.0,
                                               width=640, height=360,
                                               has_audio=True)),
        detect_cuts=mock.Mock(return_value=[2.0]),
        cuts_to_shots=mock.Mock(return_value=[(0.0, 2.0), (2.0, 4.0)]),
        extract_keyframes=mock.Mock(return_value=[[Path("shot0_f1.jpg")],
                                                  [Path("shot1_f1.jpg")]]),
        extract_audio=mock.Mock(return_value=Path("fake.wav")),
        make_test_film=fake_make_test_film,
    )
    testfilm_mod = _module("scripty.ingest.testfilm",
                           make_test_film=fake_make_test_film)
    vision_mod = _module("scripty.vision",
                         get_provider=mock.Mock(return_value=provider))
    audio_mod = _module(
        "scripty.audio",
        get_transcriber=mock.Mock(return_value=transcriber),
        assign_speakers=mock.Mock(side_effect=lambda segs: segs),
        dialogue_from_segments=mock.Mock(side_effect=fake_dialogue_from_segments),
    )
    assemble_mod = _module(
        "scripty.script.assemble",
        group_scenes=mock.Mock(side_effect=fake_group_scenes),
        cut_log=mock.Mock(return_value="0001  00:00:00:00 - 00:00:02:00  MS THING 0"),
    )
    fountain_mod = _module(
        "scripty.script.fountain",
        to_fountain=mock.Mock(return_value="Title: TEST FILM\n\nEXT. CAMP - DAY\n"),
        parse_fountain=mock.Mock(), ParsedScript=object, ParsedScene=object,
    )
    render_mod = _module("scripty.script.render",
                         render_html=mock.Mock(return_value="<div>script</div>"),
                         render_text=mock.Mock(return_value="SCRIPT"))
    script_pkg = _module("scripty.script", assemble=assemble_mod,
                         fountain=fountain_mod, render=render_mod)
    describe_mod = _module(
        "scripty.describe",
        generate_prompts=mock.Mock(side_effect=fake_generate_prompts),
        export_text=mock.Mock(return_value="PROMPT SHEET"),
        build_continuity=mock.Mock(return_value={}),
    )
    memory_mod = _module(
        "scripty.learn.memory",
        recall=mock.Mock(return_value=RecallBundle()),
        agreement_metrics=mock.Mock(return_value={
            "corrections_checked": 0, "now_agreeing": 0,
            "agreement_rate": None, "by_field": {}}),
        record_correction=mock.Mock(return_value=1),
    )
    learn_pkg = _module("scripty.learn", memory=memory_mod)

    mods = {m.__name__: m for m in (
        ingest_mod, testfilm_mod, vision_mod, audio_mod, script_pkg,
        assemble_mod, fountain_mod, render_mod, describe_mod, learn_pkg,
        memory_mod)}
    for name, mod in mods.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return SimpleNamespace(provider=provider, transcriber=transcriber, mods=mods)


def _seed_project(db: Database, tmp_path: Path, name: str = "Test Film") -> int:
    video = tmp_path / "film.mp4"
    video.write_bytes(b"\x00\x00")
    return pipeline.create_project(db, video, name=name)


# --------------------------------------------------------------------------- #
# brain
# --------------------------------------------------------------------------- #

def test_mock_brain_is_deterministic():
    brain = MockBrain()
    text = "First sentence here. Second sentence too. Third never appears."
    assert brain.complete("sys", text) == brain.complete("sys", text)
    out = brain.complete("sys", text)
    assert "First sentence here." in out
    assert "Third" not in out
    assert brain.complete("sys", "   ") == ""


def test_get_brain_resolution(monkeypatch):
    assert get_brain("mock").name == "mock"
    assert get_brain("anthropic").name == "anthropic"
    assert get_brain("xai").name == "xai"
    monkeypatch.setenv("SCRIPTY_PROVIDER", "mock")
    assert get_brain().name == "mock"
    with pytest.raises(ValueError):
        get_brain("nonsense")


def test_anthropic_brain_calls_api_with_pinned_params():
    response = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text="polished text")])
    client = mock.Mock()
    client.messages.create.return_value = response
    brain = AnthropicBrain()
    brain._client = client
    assert brain.complete("system prompt", "user text") == "polished text"
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["model"] == config.TEXT_MODEL
    assert kwargs["max_tokens"] == 2000
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["system"] == "system prompt"
    for banned in ("temperature", "top_p", "top_k"):
        assert banned not in kwargs


def test_anthropic_brain_writer_path_passes_temperature():
    response = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text="a draft")])
    client = mock.Mock()
    client.messages.create.return_value = response
    brain = AnthropicBrain()
    brain._client = client
    assert brain.complete("sys", "user", temperature=0.88, seed=7,
                          max_tokens=4000) == "a draft"
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["temperature"] == 0.88
    assert kwargs["max_tokens"] == 4000
    assert "thinking" not in kwargs
    assert "seed 7" in kwargs["messages"][0]["content"]


def test_xai_brain_pins_grok_46_and_xhigh(monkeypatch):
    captured: dict = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "choices": [{"message": {"content": "a grok draft"}}],
            }).encode()

    def fake_urlopen(request, timeout=3600):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode())
        captured["auth"] = request.get_header("Authorization")
        return FakeResp()

    monkeypatch.setenv("XAI_API_KEY", "test-key")
    monkeypatch.setattr("scripty.brain.urllib.request.urlopen", fake_urlopen)
    brain = XaiBrain()
    assert brain.model == "grok-4.6"
    assert brain.reasoning_effort == "xhigh"
    out = brain.complete("sys", "user", temperature=0.9, seed=3, max_tokens=111)
    assert out == "a grok draft"
    assert captured["url"].endswith("/chat/completions")
    assert captured["url"].startswith("https://api.x.ai/v1")
    assert captured["body"]["model"] == "grok-4.6"
    assert captured["body"]["reasoning_effort"] == "xhigh"
    assert captured["body"]["temperature"] == 0.9
    assert "seed 3" in captured["body"]["messages"][1]["content"]
    assert captured["auth"] == "Bearer test-key"


def test_writer_defaults_never_silently_use_high_or_old_grok(monkeypatch):
    monkeypatch.delenv("SCRIPTY_WRITER_MODEL", raising=False)
    monkeypatch.delenv("SCRIPTY_WRITER_REASONING_EFFORT", raising=False)
    assert config.writer_model() == "grok-4.6"
    assert config.writer_reasoning_effort() == "xhigh"
    monkeypatch.setenv("SCRIPTY_WRITER_REASONING_EFFORT", "")
    assert config.writer_reasoning_effort() == "xhigh"
    monkeypatch.setenv("SCRIPTY_WRITER_REASONING_EFFORT", "banana")
    assert config.writer_reasoning_effort() == "xhigh"
    for old in ("grok-2", "grok-3", "grok-4", "grok-4.5", "claude-opus-4-8"):
        monkeypatch.setenv("SCRIPTY_WRITER_MODEL", old)
        assert config.writer_model() == "grok-4.6"


def test_default_writer_provider_is_xai_or_mock_never_anthropic(monkeypatch):
    monkeypatch.delenv("SCRIPTY_PROVIDER", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-for-prose")
    assert config.default_writer_provider() == "mock"
    monkeypatch.setenv("XAI_API_KEY", "xai-key")
    assert config.default_writer_provider() == "xai"
    monkeypatch.setenv("SCRIPTY_PROVIDER", "anthropic")
    assert config.default_writer_provider() == "xai"
    monkeypatch.setenv("SCRIPTY_PROVIDER", "mock")
    assert config.default_writer_provider() == "mock"


def test_generate_uses_xai_even_if_session_says_anthropic(tmp_path, monkeypatch):
    from scripty.core.db import Database
    from scripty.write import generate, start_session

    captured: dict = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "choices": [{"message": {"content": "Grok wrote this draft."}}],
            }).encode()

    def fake_urlopen(request, timeout=3600):
        captured["body"] = json.loads(request.data.decode())
        return FakeResp()

    monkeypatch.setenv("XAI_API_KEY", "test-key")
    monkeypatch.setenv("SCRIPTY_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("SCRIPTY_PROVIDER", raising=False)
    monkeypatch.setattr("scripty.brain.urllib.request.urlopen", fake_urlopen)
    db = Database(tmp_path / "w.db")
    view = start_session(db, genre="thriller", tone="tight", length="short",
                         summary="A key is already lost.", provider="anthropic")
    out = generate(db, int(view["session"]["id"]))
    db.close()
    assert captured["body"]["model"] == "grok-4.6"
    assert captured["body"]["reasoning_effort"] == "xhigh"
    assert "Grok wrote this draft." in out["champion"]["text"]


def test_anthropic_brain_wraps_typed_errors():
    import anthropic
    import httpx

    client = mock.Mock()
    client.messages.create.side_effect = anthropic.APIConnectionError(
        request=httpx.Request("POST", "https://api.invalid/v1/messages"))
    brain = AnthropicBrain()
    brain._client = client
    with pytest.raises(RuntimeError) as excinfo:
        brain.complete("s", "u")
    assert isinstance(excinfo.value.__cause__, anthropic.APIConnectionError)


def test_anthropic_brain_raises_on_refusal():
    client = mock.Mock()
    client.messages.create.return_value = SimpleNamespace(
        stop_reason="refusal", content=[])
    brain = AnthropicBrain()
    brain._client = client
    with pytest.raises(RuntimeError, match="refused"):
        brain.complete("s", "u")


# --------------------------------------------------------------------------- #
# pipeline.create_project
# --------------------------------------------------------------------------- #

def test_create_project_registers_project_and_artifact(db, boundary, tmp_path):
    project_id = _seed_project(db, tmp_path)
    project = db.get_project(project_id)
    assert project is not None
    assert project["name"] == "Test Film"
    assert project["slug"] == "test-film"
    assert project["duration"] == pytest.approx(4.0)
    kinds = [a["kind"] for a in db.artifacts_for_project(project_id)]
    assert kinds == ["source_video"]


def test_create_project_uniquifies_slug(db, boundary, tmp_path):
    first = _seed_project(db, tmp_path)
    second = _seed_project(db, tmp_path)
    assert db.get_project(first)["slug"] == "test-film"
    assert db.get_project(second)["slug"] == "test-film-2"


def test_create_project_rejects_missing_path(db, boundary, tmp_path):
    with pytest.raises(FileNotFoundError):
        pipeline.create_project(db, tmp_path / "nope.mp4")


# --------------------------------------------------------------------------- #
# pipeline.run_pass
# --------------------------------------------------------------------------- #

def test_run_pass_end_to_end(db, boundary, home, tmp_path):
    project_id = _seed_project(db, tmp_path)
    lines: list[str] = []
    pass_id = pipeline.run_pass(db, project_id, provider_name="mock",
                                transcriber_name="none", progress=lines.append)

    pass_row = db.get_pass(pass_id)
    assert pass_row["status"] == "complete"
    assert pass_row["provider"] == "mock"

    shots = db.shots_for_pass(pass_id)
    assert [s["subject"] for s in shots] == ["THING 0", "THING 1"]
    assert shots[0]["start_s"] == 0.0 and shots[1]["end_s"] == 4.0

    scenes = db.scenes_for_pass(pass_id)
    assert len(scenes) == 1
    assert scenes[0]["shot_ids"] == [s["id"] for s in shots]

    dialogue = db.dialogue_for_pass(pass_id)
    assert len(dialogue) == 1
    assert dialogue[0]["scene_id"] == scenes[0]["id"]  # backfilled

    prompts = db.prompts_for_pass(pass_id)
    assert len(prompts) == 3  # 1 scene + 2 shots

    kinds = {a["kind"] for a in db.artifacts_for_project(project_id)}
    assert {"source_video", "generated_script", "describe_set"} <= kinds

    art_dir = home / "projects" / "test-film" / "artifacts"
    assert (art_dir / "pass1.fountain").read_text(encoding="utf-8").startswith("Title:")
    assert (art_dir / "pass1.html").is_file()
    assert (art_dir / "pass1_prompts.txt").read_text(encoding="utf-8") == "PROMPT SHEET"

    metrics = pass_row["metrics"]
    assert metrics["n_shots"] == 2
    assert metrics["n_scenes"] == 1
    assert metrics["n_dialogue"] == 1
    assert metrics["mean_confidence"] == pytest.approx(0.8)
    assert "agreement" in metrics
    assert lines, "progress callable was never invoked"


def test_run_pass_recall_covers_hot_fields(db, boundary, tmp_path):
    project_id = _seed_project(db, tmp_path)
    pipeline.run_pass(db, project_id)
    recall = boundary.mods["scripty.learn.memory"].recall
    fields = {c.kwargs["field"] for c in recall.call_args_list}
    assert fields == set(pipeline.RECALL_FIELDS)
    # every shot analysis received a RecallBundle
    assert boundary.provider.calls
    for _, _, bundle in boundary.provider.calls:
        assert isinstance(bundle, RecallBundle)


def test_run_pass_marks_failed_and_reraises(db, boundary, tmp_path):
    project_id = _seed_project(db, tmp_path)
    boundary.mods["scripty.ingest"].detect_cuts.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"):
        pipeline.run_pass(db, project_id)
    passes = db.passes_for_project(project_id)
    assert passes[-1]["status"] == "failed"
    assert "boom" in passes[-1]["metrics"]["error"]


def test_run_pass_unknown_project(db, boundary):
    with pytest.raises(ValueError):
        pipeline.run_pass(db, 999)


def test_run_pass_folds_truth_alignment(db, boundary, tmp_path, monkeypatch):
    project_id = _seed_project(db, tmp_path)
    db.add_truth(project_id=project_id, script_path="/known.fountain",
                 parsed={"scenes": []})
    align_mod = _module(
        "scripty.truth.align",
        align_pass=mock.Mock(return_value={
            "scene_pairs": [], "slugline_accuracy": 1.0,
            "dialogue": {"truth_lines": 2, "matched": 1, "avg_score": 80.0,
                         "character_accuracy": 0.5}}),
        link_script=mock.Mock(), emit_auto_corrections=mock.Mock(return_value=0),
    )
    monkeypatch.setitem(sys.modules, "scripty.truth",
                        _module("scripty.truth", align=align_mod))
    monkeypatch.setitem(sys.modules, "scripty.truth.align", align_mod)
    pass_id = pipeline.run_pass(db, project_id)
    metrics = db.get_pass(pass_id)["metrics"]
    assert metrics["slugline_accuracy"] == 1.0
    assert metrics["dialogue_match"]["matched"] == 1
    align_mod.align_pass.assert_called_once_with(db, pass_id=pass_id)


# --------------------------------------------------------------------------- #
# pipeline.demo
# --------------------------------------------------------------------------- #

def test_demo_builds_film_and_runs_offline_pass(db, boundary, home):
    project_id, pass_id = pipeline.demo(db)
    assert (home / "demo.mp4").is_file()
    assert db.get_pass(pass_id)["status"] == "complete"
    assert db.get_project(project_id)["name"] == "Scripty Demo"
    # demo pins the offline stack
    kwargs = boundary.mods["scripty.vision"].get_provider.call_args.args
    assert kwargs == ("mock",)


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #

def test_cli_create(db, boundary, tmp_path, monkeypatch):
    video = tmp_path / "film.mp4"
    video.write_bytes(b"\x00")
    monkeypatch.setattr(cli, "_db", lambda: db)
    created = mock.Mock(return_value=42)
    monkeypatch.setattr(cli.pipeline, "create_project", created)
    result = runner.invoke(cli.app, ["create", str(video), "--name", "Film"])
    assert result.exit_code == 0
    assert "42" in result.output
    assert created.call_args.kwargs["name"] == "Film"


def test_cli_pass_forwards_options(db, monkeypatch):
    monkeypatch.setattr(cli, "_db", lambda: db)
    ran = mock.Mock(return_value=9)
    monkeypatch.setattr(cli.pipeline, "run_pass", ran)
    result = runner.invoke(cli.app, ["pass", "3", "--provider", "mock",
                                     "--transcriber", "none", "--brain", "mock"])
    assert result.exit_code == 0
    assert "pass 9 complete" in result.output
    kwargs = ran.call_args.kwargs
    assert kwargs["provider_name"] == "mock"
    assert kwargs["transcriber_name"] == "none"
    assert kwargs["brain_name"] == "mock"


def test_cli_log_prints_cut_log(db, boundary, monkeypatch):
    monkeypatch.setattr(cli, "_db", lambda: db)
    project_id = db.create_project(name="F", slug="f", video_path="/x.mp4",
                                   duration=4.0, fps=24.0, width=10, height=10)
    pass_id = db.create_pass(project_id=project_id, provider="mock")
    db.add_shot(pass_id=pass_id, idx=0, start_s=0.0, end_s=2.0, subject="FIRE")
    result = runner.invoke(cli.app, ["log", str(pass_id)])
    assert result.exit_code == 0
    assert "0001" in result.output


def test_cli_log_missing_pass_fails(db, boundary, monkeypatch):
    monkeypatch.setattr(cli, "_db", lambda: db)
    result = runner.invoke(cli.app, ["log", "777"])
    assert result.exit_code == 1


def test_cli_lessons_list(db, monkeypatch):
    monkeypatch.setattr(cli, "_db", lambda: db)
    db.add_lesson(scope="project", project_id=1, field="location",
                  rule="Prefer WOODED ROAD over WOODS.", source_ids=[1])
    result = runner.invoke(cli.app, ["lessons", "list"])
    assert result.exit_code == 0
    assert "WOODED ROAD" in result.output


def test_cli_demo(db, boundary, monkeypatch):
    monkeypatch.setattr(cli, "_db", lambda: db)
    result = runner.invoke(cli.app, ["demo"])
    assert result.exit_code == 0
    assert "demo ready" in result.output
    assert "serve" in result.output
