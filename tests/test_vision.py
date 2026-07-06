"""Offline tests for scripty.vision (taxonomy + providers). No network, no API key."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scripty.core import config
from scripty.core.interfaces import VisionProvider
from scripty.core.models import (
    CameraAngle,
    CameraMove,
    Correction,
    IntExt,
    Lesson,
    RecallBundle,
    ShotAnalysis,
    ShotContext,
    ShotScale,
    TimeOfDay,
)
from scripty.vision import providers
from scripty.vision.taxonomy import (
    SHOT_SCALE_GUIDE,
    build_shot_prompt,
    parse_shot_json,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def make_ctx() -> ShotContext:
    return ShotContext(
        film_title="Night Coil", pass_number=2, shot_index=3, total_shots=12,
        start_s=10.0, end_s=13.5,
        prev_summaries=["WS FOREST", "MS RIVER BEND"],
        transcript_excerpt="I'm not going back there.",
    )


def make_recall() -> RecallBundle:
    lesson = Lesson(id=7, scope="project", project_id=1, field="location",
                    rule="When trees line both sides of a visible road, label "
                         "the location WOODED ROAD, not WOODS.")
    example = Correction(id=9, project_id=1, pass_id=1, entity_type="shot",
                         entity_id=3, field="subject", model_value="FIRE",
                         human_value="CAMP FIRE", note="it is a camp fire")
    return RecallBundle(lessons=[lesson], examples=[example])


def write_frames(tmp_path: Path, blobs: list[bytes]) -> list[Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, blob in enumerate(blobs):
        path = tmp_path / f"shot0000_f{i}.jpg"
        path.write_bytes(blob)
        paths.append(path)
    return paths


class FakeAPIStatusError(Exception):
    pass


class FakeAPIConnectionError(Exception):
    pass


def text_response(text: str, stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="thinking", thinking="hmm"),
                 SimpleNamespace(type="text", text=text)],
    )


@pytest.fixture
def fake_anthropic(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    client = MagicMock()
    module = SimpleNamespace(
        Anthropic=MagicMock(return_value=client),
        APIStatusError=FakeAPIStatusError,
        APIConnectionError=FakeAPIConnectionError,
    )
    module.client = client
    monkeypatch.setattr(providers, "anthropic", module)
    return module


VALID = {
    "scale": "CU", "subject": "CAMP FIRE", "angle": "LOW ANGLE",
    "move": "PUSH IN", "int_ext": "EXT.", "location": "WOODED ROAD",
    "time_of_day": "NIGHT", "action_text": "Flames snap at the dark.",
    "characters": [{"name": "MARA", "description": "gray coat"}],
    "mood": "ember glow", "confidence": 0.82,
}


# ---------------------------------------------------------------------------
# taxonomy: guide + prompt
# ---------------------------------------------------------------------------


def test_guide_covers_conventions() -> None:
    for token in ("ECU", "CU", "MCU", "MS", "WS", "EWS", "OTS", "POV",
                  "INSERT", "2-SHOT", "EST", "AERIAL"):
        assert token in SHOT_SCALE_GUIDE


def test_build_shot_prompt_includes_context_and_recall() -> None:
    prompt = build_shot_prompt(make_ctx(), make_recall())
    assert SHOT_SCALE_GUIDE.strip() in prompt
    assert "Night Coil" in prompt
    assert "Shot 4 of 12" in prompt
    assert "MS RIVER BEND" in prompt
    assert "I'm not going back there." in prompt
    assert "SUPERVISOR NOTES" in prompt
    assert "WOODED ROAD, not WOODS" in prompt
    assert "'CAMP FIRE'" in prompt
    assert "OUTRANK" in prompt
    assert "single JSON object" in prompt
    for key in ("scale", "subject", "angle", "move", "int_ext", "location",
                "time_of_day", "action_text", "characters", "mood",
                "confidence"):
        assert f'"{key}"' in prompt
    assert "2-SHOT" in prompt and "WORM'S EYE" in prompt


def test_build_shot_prompt_empty_recall_has_no_notes() -> None:
    prompt = build_shot_prompt(make_ctx(), RecallBundle())
    assert "SUPERVISOR NOTES" not in prompt


# ---------------------------------------------------------------------------
# taxonomy: defensive JSON parsing
# ---------------------------------------------------------------------------


def test_parse_shot_json_happy_with_surrounding_prose() -> None:
    text = f"Here is the log entry:\n{json.dumps(VALID)}\nDone."
    analysis = parse_shot_json(text)
    assert analysis.scale is ShotScale.CU
    assert analysis.subject == "CAMP FIRE"
    assert analysis.angle is CameraAngle.LOW
    assert analysis.move is CameraMove.PUSH_IN
    assert analysis.int_ext is IntExt.EXT
    assert analysis.location == "WOODED ROAD"
    assert analysis.time_of_day is TimeOfDay.NIGHT
    assert analysis.action_text == "Flames snap at the dark."
    assert [c.name for c in analysis.characters] == ["MARA"]
    assert analysis.characters[0].description == "gray coat"
    assert analysis.mood == "ember glow"
    assert analysis.confidence == pytest.approx(0.82)
    assert analysis.raw == VALID


def test_parse_shot_json_tolerates_enum_drift() -> None:
    text = json.dumps({"scale": "two shot", "angle": "worms eye",
                       "move": "push in", "int_ext": "int",
                       "time_of_day": "night"})
    analysis = parse_shot_json(text)
    assert analysis.scale is ShotScale.TWO_SHOT
    assert analysis.angle is CameraAngle.WORMS_EYE
    assert analysis.move is CameraMove.PUSH_IN
    assert analysis.int_ext is IntExt.INT
    assert analysis.time_of_day is TimeOfDay.NIGHT


def test_parse_shot_json_garbage_degrades() -> None:
    for text in ("no json at all", "", "{broken: json", "[1, 2, 3]"):
        analysis = parse_shot_json(text)
        assert isinstance(analysis, ShotAnalysis)
        assert analysis.confidence == pytest.approx(0.1)
        assert "error" in analysis.raw
        assert analysis.scale is ShotScale.MS  # defaults intact


def test_parse_shot_json_characters_variants() -> None:
    as_strings = parse_shot_json(json.dumps({"characters": ["MARA", "DEL"]}))
    assert [c.name for c in as_strings.characters] == ["MARA", "DEL"]
    as_csv = parse_shot_json(json.dumps({"characters": "MARA, DEL"}))
    assert [c.name for c in as_csv.characters] == ["MARA", "DEL"]
    as_junk = parse_shot_json(json.dumps({"characters": 42}))
    assert as_junk.characters == []


def test_parse_shot_json_confidence_clamped_and_defaulted() -> None:
    assert parse_shot_json(json.dumps({"confidence": 7})).confidence == 1.0
    assert parse_shot_json(json.dumps({"confidence": -3})).confidence == 0.0
    assert parse_shot_json(json.dumps({"confidence": "high"})).confidence == 0.5


# ---------------------------------------------------------------------------
# MockVision
# ---------------------------------------------------------------------------


def test_mock_is_a_vision_provider() -> None:
    provider = providers.MockVision()
    assert isinstance(provider, VisionProvider)
    assert provider.name == "mock"


def test_mock_deterministic(tmp_path: Path) -> None:
    frames = write_frames(tmp_path, [b"\xff\xd8fakejpegdata-a", b"blob-b"])
    provider = providers.MockVision()
    first = provider.analyze_shot(frames, make_ctx(), RecallBundle())
    second = provider.analyze_shot(frames, make_ctx(), RecallBundle())
    assert first == second
    assert first.subject and first.location and first.action_text
    assert 0.0 < first.confidence <= 1.0


def test_mock_brightness_sets_time_of_day(tmp_path: Path) -> None:
    provider = providers.MockVision()
    dark = provider.analyze_shot(write_frames(tmp_path, [b"\x08" * 400]),
                                 make_ctx(), RecallBundle())
    assert dark.time_of_day is TimeOfDay.NIGHT
    bright = provider.analyze_shot(write_frames(tmp_path, [b"\xee" * 400]),
                                   make_ctx(), RecallBundle())
    assert bright.time_of_day is TimeOfDay.DAY


def test_mock_applies_correction_examples(tmp_path: Path) -> None:
    frames = write_frames(tmp_path, [b"frame-bytes-1"])
    recall = RecallBundle(examples=[
        Correction(id=1, project_id=1, pass_id=1, entity_type="shot",
                   entity_id=1, field="location", model_value="WOODS",
                   human_value="WOODED ROAD"),
        Correction(id=2, project_id=1, pass_id=1, entity_type="shot",
                   entity_id=1, field="scale", model_value="MS",
                   human_value="CU"),
        Correction(id=3, project_id=1, pass_id=1, entity_type="shot",
                   entity_id=1, field="characters", model_value="",
                   human_value="MARA, DEL"),
    ])
    analysis = providers.MockVision().analyze_shot(frames, make_ctx(), recall)
    assert analysis.location == "WOODED ROAD"
    assert analysis.scale is ShotScale.CU
    assert [c.name for c in analysis.characters] == ["MARA", "DEL"]
    assert any(entry.startswith("correction:")
               for entry in analysis.raw.get("recall_applied", []))


def test_mock_applies_prefer_lesson(tmp_path: Path) -> None:
    frames = write_frames(tmp_path, [b"frame-bytes-lesson"])
    provider = providers.MockVision()
    base = provider.analyze_shot(frames, make_ctx(), RecallBundle())
    lesson = Lesson(id=1, scope="project", project_id=1, field="location",
                    rule=f"Prefer 'RIDGELINE HUT' over '{base.location}' "
                         "when similar context recurs.")
    learned = provider.analyze_shot(frames, make_ctx(),
                                    RecallBundle(lessons=[lesson]))
    assert learned.location == "RIDGELINE HUT"


def test_mock_example_outranks_lesson(tmp_path: Path) -> None:
    frames = write_frames(tmp_path, [b"frame-bytes-priority"])
    provider = providers.MockVision()
    base = provider.analyze_shot(frames, make_ctx(), RecallBundle())
    recall = RecallBundle(
        lessons=[Lesson(id=1, scope="project", project_id=1, field="location",
                        rule=f"Prefer 'A' over '{base.location}'.")],
        examples=[Correction(id=2, project_id=1, pass_id=1,
                             entity_type="shot", entity_id=1,
                             field="location", model_value="A",
                             human_value="B")],
    )
    assert provider.analyze_shot(frames, make_ctx(), recall).location == "B"


def test_mock_survives_missing_frames(tmp_path: Path) -> None:
    ghost = tmp_path / "nope" / "missing.jpg"
    analysis = providers.MockVision().analyze_shot([ghost], make_ctx(),
                                                   RecallBundle())
    assert isinstance(analysis, ShotAnalysis)
    assert analysis.subject


def test_mock_critique(tmp_path: Path) -> None:
    provider = providers.MockVision()
    original = write_frames(tmp_path / "o", [b"original-bytes"])
    generated = write_frames(tmp_path / "g", [b"generated-bytes"])
    note = provider.critique_frames(original, generated, "shot 1 MS CAMP FIRE")
    assert "PROMPT FIXES:" in note
    assert provider.critique_frames(original, original, "same") == ""


# ---------------------------------------------------------------------------
# AnthropicVision (SDK fully mocked)
# ---------------------------------------------------------------------------


def test_anthropic_analyze_happy(fake_anthropic: SimpleNamespace,
                                 tmp_path: Path) -> None:
    fake_anthropic.client.messages.create.return_value = \
        text_response(json.dumps(VALID))
    frames = write_frames(tmp_path, [b"jpeg-one", b"jpeg-two"])
    provider = providers.AnthropicVision()
    analysis = provider.analyze_shot(frames, make_ctx(), make_recall())

    fake_anthropic.Anthropic.assert_called_once_with()
    kwargs = fake_anthropic.client.messages.create.call_args.kwargs
    assert kwargs["model"] == config.VISION_MODEL
    assert kwargs["max_tokens"] == 2048
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["system"]
    for banned in ("temperature", "top_p", "top_k"):
        assert banned not in kwargs
    content = kwargs["messages"][0]["content"]
    images = [b for b in content if b.get("type") == "image"]
    assert len(images) == 2
    assert all(b["source"]["type"] == "base64" and
               b["source"]["media_type"] == "image/jpeg" for b in images)
    assert "SUPERVISOR NOTES" in content[-1]["text"]

    assert analysis.scale is ShotScale.CU
    assert analysis.location == "WOODED ROAD"
    assert analysis.confidence == pytest.approx(0.82)


def test_anthropic_custom_model(fake_anthropic: SimpleNamespace,
                                tmp_path: Path) -> None:
    fake_anthropic.client.messages.create.return_value = \
        text_response(json.dumps(VALID))
    provider = providers.AnthropicVision(model="claude-custom-1")
    provider.analyze_shot(write_frames(tmp_path, [b"x"]), make_ctx(),
                          RecallBundle())
    kwargs = fake_anthropic.client.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-custom-1"


def test_anthropic_api_errors_degrade(fake_anthropic: SimpleNamespace,
                                      tmp_path: Path) -> None:
    frames = write_frames(tmp_path, [b"x"])
    for exc in (FakeAPIStatusError("rate limited"),
                FakeAPIConnectionError("no route")):
        fake_anthropic.client.messages.create.side_effect = exc
        analysis = providers.AnthropicVision().analyze_shot(
            frames, make_ctx(), RecallBundle())
        assert analysis.confidence == pytest.approx(0.1)
        assert str(exc) in analysis.raw["error"]


def test_anthropic_refusal_degrades(fake_anthropic: SimpleNamespace,
                                    tmp_path: Path) -> None:
    fake_anthropic.client.messages.create.return_value = \
        text_response("nope", stop_reason="refusal")
    analysis = providers.AnthropicVision().analyze_shot(
        write_frames(tmp_path, [b"x"]), make_ctx(), RecallBundle())
    assert analysis.confidence == pytest.approx(0.1)
    assert "refused" in analysis.raw["error"]


def test_anthropic_garbled_reply_degrades(fake_anthropic: SimpleNamespace,
                                          tmp_path: Path) -> None:
    fake_anthropic.client.messages.create.return_value = \
        text_response("I looked at the frames but here is prose only.")
    analysis = providers.AnthropicVision().analyze_shot(
        write_frames(tmp_path, [b"x"]), make_ctx(), RecallBundle())
    assert analysis.confidence == pytest.approx(0.1)
    assert "error" in analysis.raw


def test_anthropic_critique(fake_anthropic: SimpleNamespace,
                            tmp_path: Path) -> None:
    fake_anthropic.client.messages.create.return_value = \
        text_response("Wider framing.\n\nPROMPT FIXES: pin the scale.")
    provider = providers.AnthropicVision()
    original = write_frames(tmp_path / "o", [b"a"])
    generated = write_frames(tmp_path / "g", [b"b"])
    note = provider.critique_frames(original, generated, "shot 1")
    assert "PROMPT FIXES" in note
    content = fake_anthropic.client.messages.create.call_args.kwargs[
        "messages"][0]["content"]
    assert sum(1 for b in content if b.get("type") == "image") == 2

    fake_anthropic.client.messages.create.side_effect = \
        FakeAPIConnectionError("down")
    assert provider.critique_frames(original, generated, "shot 1") == ""


def test_anthropic_requires_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(providers, "anthropic", None)
    with pytest.raises(RuntimeError):
        providers.AnthropicVision()


# ---------------------------------------------------------------------------
# get_provider
# ---------------------------------------------------------------------------


def test_get_provider_mock() -> None:
    assert isinstance(providers.get_provider("mock"), providers.MockVision)
    assert isinstance(providers.get_provider("MOCK"), providers.MockVision)


def test_get_provider_anthropic(fake_anthropic: SimpleNamespace) -> None:
    provider = providers.get_provider("anthropic")
    assert isinstance(provider, providers.AnthropicVision)


def test_get_provider_default_uses_config(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(providers.config, "default_provider", lambda: "mock")
    assert isinstance(providers.get_provider(None), providers.MockVision)
    assert isinstance(providers.get_provider(), providers.MockVision)


def test_get_provider_unknown_raises() -> None:
    with pytest.raises(ValueError):
        providers.get_provider("hal9000")
