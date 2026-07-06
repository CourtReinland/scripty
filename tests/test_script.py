"""Offline tests for scripty.script (assemble / fountain / render).

This module is pure — no LLM, ffmpeg, or network boundary exists here,
so nothing needs mocking and everything runs offline.
"""
from __future__ import annotations

from scripty.script import (cut_log, group_scenes, parse_fountain,
                            render_html, render_text, to_fountain)
from scripty.script.fountain import ParsedScene, ParsedScript, classify_lines


def make_shot(shot_id: int, idx: int, *, location: str = "WOODED ROAD",
              int_ext: str = "EXT.", time_of_day: str = "NIGHT",
              scale: str = "MS", subject: str = "CAMP FIRE",
              action_text: str = "", start_s: float = 0.0,
              end_s: float = 3.5) -> dict:
    return {"id": shot_id, "pass_id": 1, "idx": idx, "start_s": start_s,
            "end_s": end_s, "scale": scale, "subject": subject,
            "angle": "EYE LEVEL", "move": "STATIC", "int_ext": int_ext,
            "location": location, "time_of_day": time_of_day,
            "action_text": action_text, "characters": [], "mood": "",
            "confidence": 0.8, "keyframes": [], "raw": {}}


def make_dialogue(shot_id: int, *, character: str = "MARA",
                  text: str = "We should not be out here.",
                  parenthetical: str = "", start_s: float = 0.0) -> dict:
    return {"shot_id": shot_id, "character": character, "text": text,
            "parenthetical": parenthetical, "start_s": start_s,
            "end_s": start_s + 1.0}


# --------------------------------------------------------------------------- #
# assemble.group_scenes
# --------------------------------------------------------------------------- #

def test_group_scenes_splits_on_heading_change() -> None:
    shots = [
        make_shot(11, 0, location="WOODS", action_text="Trees sway."),
        make_shot(12, 1, location="WOODS"),
        make_shot(13, 2, location="CABIN", int_ext="INT.", time_of_day="DAY",
                  action_text="A kettle whistles."),
    ]
    scenes = group_scenes(shots)
    assert len(scenes) == 2
    assert scenes[0]["idx"] == 0 and scenes[1]["idx"] == 1
    assert scenes[0]["shot_ids"] == [11, 12]
    assert scenes[1]["shot_ids"] == [13]
    assert scenes[0]["location"] == "WOODS"
    assert scenes[1] == {"idx": 1, "int_ext": "INT.", "location": "CABIN",
                         "time_of_day": "DAY", "shot_ids": [13],
                         "synopsis": "A kettle whistles."}


def test_group_scenes_location_case_insensitive() -> None:
    shots = [make_shot(1, 0, location="Woods"),
             make_shot(2, 1, location="WOODS")]
    assert len(group_scenes(shots)) == 1


def test_group_scenes_empty_location_inherits() -> None:
    shots = [make_shot(1, 0, location="WOODS"),
             make_shot(2, 1, location=""),
             make_shot(3, 2, location="WOODS")]
    scenes = group_scenes(shots)
    assert len(scenes) == 1
    assert scenes[0]["shot_ids"] == [1, 2, 3]


def test_group_scenes_leading_empty_location_adopts_first_located() -> None:
    shots = [make_shot(1, 0, location=""),
             make_shot(2, 1, location="CABIN", int_ext="INT.")]
    scenes = group_scenes(shots)
    assert len(scenes) == 1
    assert scenes[0]["location"] == "CABIN"
    assert scenes[0]["int_ext"] == "INT."
    assert scenes[0]["shot_ids"] == [1, 2]


def test_group_scenes_synopsis_first_nonempty_truncated() -> None:
    long_action = "x" * 500
    shots = [make_shot(1, 0, action_text=""),
             make_shot(2, 1, action_text=long_action),
             make_shot(3, 2, action_text="later text")]
    scenes = group_scenes(shots)
    assert scenes[0]["synopsis"] == "x" * 200


def test_group_scenes_empty_input() -> None:
    assert group_scenes([]) == []


# --------------------------------------------------------------------------- #
# assemble.cut_log
# --------------------------------------------------------------------------- #

def test_cut_log_line_format() -> None:
    shots = [make_shot(1, 0, start_s=0.0, end_s=3.5)]
    assert cut_log(shots) == "0001  00:00:00:00 - 00:00:03:12  MS CAMP FIRE"


def test_cut_log_multiple_lines_and_unknown_subject() -> None:
    shots = [make_shot(1, 0, end_s=2.0),
             make_shot(2, 1, subject="", scale="WS", start_s=2.0, end_s=4.0)]
    lines = cut_log(shots).split("\n")
    assert len(lines) == 2
    assert lines[1] == "0002  00:00:02:00 - 00:00:04:00  WS UNKNOWN"


# --------------------------------------------------------------------------- #
# fountain.to_fountain
# --------------------------------------------------------------------------- #

def _sample_pass() -> tuple[list[dict], list[dict], list[dict]]:
    shots = [
        make_shot(11, 0, action_text="A camp fire crackles.", end_s=3.0),
        make_shot(12, 1, action_text="MARA leans into the light.",
                  start_s=3.0, end_s=6.0),
        make_shot(13, 2, location="CABIN", int_ext="INT.", time_of_day="DAY",
                  action_text="Morning. A kettle whistles.",
                  start_s=6.0, end_s=9.0),
    ]
    scenes = group_scenes(shots)
    dialogue = [make_dialogue(12, parenthetical="whispering", start_s=3.5)]
    return scenes, shots, dialogue


def test_to_fountain_title_page() -> None:
    scenes, shots, dialogue = _sample_pass()
    text = to_fountain(scenes, shots, dialogue, title="NIGHT WOODS",
                       author="Scripty")
    lines = text.splitlines()
    assert lines[0] == "Title: NIGHT WOODS"
    assert lines[1] == "Author: Scripty"
    assert lines[2] == "Draft date:"
    assert lines[3] == ""


def test_to_fountain_sluglines_and_action() -> None:
    scenes, shots, dialogue = _sample_pass()
    text = to_fountain(scenes, shots, dialogue)
    assert "EXT. WOODED ROAD - NIGHT" in text
    assert "INT. CABIN - DAY" in text
    assert "A camp fire crackles." in text
    assert text.index("EXT. WOODED ROAD - NIGHT") < text.index("INT. CABIN - DAY")


def test_to_fountain_dialogue_placed_after_its_shot() -> None:
    scenes, shots, dialogue = _sample_pass()
    text = to_fountain(scenes, shots, dialogue)
    i_shot2_action = text.index("MARA leans into the light.")
    i_char = text.index("\nMARA\n")
    i_shot3_action = text.index("Morning. A kettle whistles.")
    assert i_shot2_action < i_char < i_shot3_action
    assert "(whispering)" in text
    assert text.index("(whispering)") > i_char


def test_to_fountain_skips_unmatched_and_empty_dialogue() -> None:
    scenes, shots, _ = _sample_pass()
    dialogue = [make_dialogue(999), make_dialogue(12, text="   ")]
    text = to_fountain(scenes, shots, dialogue)
    assert "\nMARA\n" not in text


# --------------------------------------------------------------------------- #
# fountain.parse_fountain
# --------------------------------------------------------------------------- #

def test_parse_fountain_roundtrip() -> None:
    scenes, shots, dialogue = _sample_pass()
    parsed = parse_fountain(to_fountain(scenes, shots, dialogue,
                                        title="Night Woods"))
    assert isinstance(parsed, ParsedScript)
    assert parsed.title == "Night Woods"
    assert len(parsed.scenes) == 2
    first, second = parsed.scenes
    assert isinstance(first, ParsedScene)
    assert (first.int_ext, first.location, first.time_of_day) == \
        ("EXT.", "WOODED ROAD", "NIGHT")
    assert (second.int_ext, second.location, second.time_of_day) == \
        ("INT.", "CABIN", "DAY")
    assert "A camp fire crackles." in first.action
    assert first.dialogue == [("MARA",
                               "(whispering) We should not be out here.")]
    assert second.dialogue == []


def test_parse_fountain_ie_and_lowercase_sluglines() -> None:
    parsed = parse_fountain(
        "I/E. CAR - CONTINUOUS\n\nHands on the wheel.\n\n"
        "int. kitchen - dawn\n\nToast burns.\n")
    assert len(parsed.scenes) == 2
    assert parsed.scenes[0].int_ext == "INT./EXT."
    assert parsed.scenes[0].location == "CAR"
    assert parsed.scenes[0].time_of_day == "CONTINUOUS"
    assert parsed.scenes[1].int_ext == "INT."
    assert parsed.scenes[1].location == "KITCHEN"
    assert parsed.scenes[1].time_of_day == "DAWN"


def test_parse_fountain_plain_text_without_sluglines() -> None:
    parsed = parse_fountain("Just some prose.\nAcross two lines.\n")
    assert parsed.title == "UNTITLED"
    assert len(parsed.scenes) == 1
    assert parsed.scenes[0].location == "UNKNOWN"
    assert parsed.scenes[0].action == ["Just some prose. Across two lines."]


def test_parse_fountain_uppercase_action_not_character() -> None:
    # Uppercase line followed by a blank line must stay action.
    parsed = parse_fountain("EXT. YARD - DAY\n\nMS CAMP FIRE\n\nA dog barks.\n")
    assert parsed.scenes[0].dialogue == []
    assert "MS CAMP FIRE" in parsed.scenes[0].action


def test_parse_fountain_character_extension_and_multiline_speech() -> None:
    parsed = parse_fountain(
        "EXT. YARD - DAY\n\nVOICE (O.S.)\nFirst part.\nSecond part.\n")
    assert parsed.scenes[0].dialogue == [("VOICE (O.S.)",
                                          "First part. Second part.")]


def test_parse_fountain_time_defaults_and_odd_values_kept() -> None:
    parsed = parse_fountain("EXT. RIDGE\n\nWind.\n\nEXT. RIDGE - SUNSET\n\nSun.\n")
    assert parsed.scenes[0].time_of_day == "DAY"
    assert parsed.scenes[1].time_of_day == "SUNSET"


# --------------------------------------------------------------------------- #
# render.render_text
# --------------------------------------------------------------------------- #

def test_render_text_indents() -> None:
    scenes, shots, dialogue = _sample_pass()
    rendered = render_text(to_fountain(scenes, shots, dialogue))
    lines = rendered.splitlines()
    slug = next(l for l in lines if l.startswith("EXT. WOODED ROAD"))
    assert not slug.startswith(" ")
    char = next(l for l in lines if l.strip() == "MARA")
    assert char.startswith(" " * 22) and not char.startswith(" " * 23)
    paren = next(l for l in lines if l.strip() == "(whispering)")
    assert paren.startswith(" " * 16) and not paren.startswith(" " * 17)
    dlg = next(l for l in lines if "We should not" in l)
    assert dlg.startswith(" " * 10) and not dlg.startswith(" " * 11)
    action = next(l for l in lines if "camp fire crackles" in l)
    assert not action.startswith(" ")


def test_render_text_dialogue_wrapped_at_35() -> None:
    shots = [make_shot(1, 0, action_text="Fire.")]
    scenes = group_scenes(shots)
    dialogue = [make_dialogue(1, text="word " * 20)]
    rendered = render_text(to_fountain(scenes, shots, dialogue))
    dlg_lines = [l for l in rendered.splitlines()
                 if l.startswith(" " * 10) and l.strip().startswith("word")]
    assert len(dlg_lines) > 1
    assert all(len(l) <= 10 + 35 for l in dlg_lines)


def test_render_text_action_wrapped_at_60() -> None:
    shots = [make_shot(1, 0, action_text="ash " * 40)]
    rendered = render_text(to_fountain(group_scenes(shots), shots, []))
    action_lines = [l for l in rendered.splitlines() if l.startswith("ash")]
    assert len(action_lines) > 1
    assert all(len(l) <= 60 for l in action_lines)


def test_render_text_page_markers_every_55_lines() -> None:
    shots = [make_shot(i + 1, i, action_text=f"Beat {i + 1}.",
                       start_s=float(i), end_s=float(i + 1))
             for i in range(80)]
    rendered = render_text(to_fountain(group_scenes(shots), shots, []))
    lines = rendered.splitlines()
    assert lines[55] == "--- p.2 ---"
    assert lines[111] == "--- p.3 ---"
    assert "--- p.2 ---" not in lines[:55]


# --------------------------------------------------------------------------- #
# render.render_html
# --------------------------------------------------------------------------- #

def test_render_html_structure_classes_and_escaping() -> None:
    shots = [make_shot(1, 0, action_text="Fish & chips <hot>.")]
    scenes = group_scenes(shots)
    dialogue = [make_dialogue(1, parenthetical="dry")]
    html = render_html(to_fountain(scenes, shots, dialogue,
                                   title="Night <Woods>"))
    assert html.startswith("<!DOCTYPE html>")
    assert "<title>Night &lt;Woods&gt;</title>" in html
    assert '<p class="slug">EXT. WOODED ROAD - NIGHT</p>' in html
    assert '<p class="action">Fish &amp; chips &lt;hot&gt;.</p>' in html
    assert '<p class="character">MARA</p>' in html
    assert '<p class="paren">(dry)</p>' in html
    assert '<p class="dialogue">We should not be out here.</p>' in html
    assert "Courier" in html


def test_render_html_title_arg_overrides() -> None:
    scenes, shots, dialogue = _sample_pass()
    html = render_html(to_fountain(scenes, shots, dialogue, title="A"),
                       title="Override")
    assert "<title>Override</title>" in html


def test_render_html_no_external_requests() -> None:
    scenes, shots, dialogue = _sample_pass()
    html = render_html(to_fountain(scenes, shots, dialogue))
    assert "http://" not in html and "https://" not in html


# --------------------------------------------------------------------------- #
# classify_lines (shared classifier)
# --------------------------------------------------------------------------- #

def test_classify_lines_title_page_and_kinds() -> None:
    scenes, shots, dialogue = _sample_pass()
    title_page, items = classify_lines(
        to_fountain(scenes, shots, dialogue, title="X", author="Y"))
    assert title_page["title"] == "X"
    assert title_page["author"] == "Y"
    assert title_page["draft date"] == ""
    kinds = {kind for kind, _ in items}
    assert {"slug", "action", "character", "paren", "dialogue"} <= kinds
    assert ("slug", "EXT. WOODED ROAD - NIGHT") in items
