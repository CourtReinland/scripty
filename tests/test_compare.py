"""Offline tests for scripty.compare — every ffmpeg/LLM boundary mocked."""
from __future__ import annotations

import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

import scripty.compare.video as video_mod
from scripty.compare import (
    compare_videos,
    critique_shots,
    revise_prompts,
    run_comparison,
)
from scripty.core.db import Database
from scripty.core.models import ArtifactKind, CritiqueNote, MediaInfo

SSIM_LINE = ("[Parsed_ssim_3 @ 0x0] SSIM Y:0.592724 (3.901110) U:0.495864 "
             "(2.974520) V:0.445557 (2.561430) All:0.552053 (3.487730)")
PSNR_LINE = ("[Parsed_psnr_4 @ 0x0] PSNR y:9.261976 u:9.527835 v:9.098948 "
             "average:9.277296 min:9.252163 max:9.299180")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

class FakeProvider:
    """Deterministic VisionProvider stand-in for critique tests."""

    name = "fake"

    def __init__(self, critiques: list[str]):
        self.critiques = critiques
        self.calls: list[tuple[list[Path], list[Path], str]] = []

    def analyze_shot(self, frames, ctx, recall):  # pragma: no cover - unused
        raise NotImplementedError

    def critique_frames(self, original, generated, context):
        self.calls.append((original, generated, context))
        result = self.critiques[len(self.calls) - 1]
        if isinstance(result, Exception):
            raise result
        return result


class FakeBrain:
    name = "fake"

    def __init__(self, reply: str = "", error: bool = False):
        self.reply = reply
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if self.error:
            raise RuntimeError("brain down")
        return self.reply


def fake_media(path, width=640, height=360):
    return MediaInfo(path=str(path), duration=3.0, fps=24.0,
                     width=width, height=height, vcodec="h264")


def touch_video(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(b"\x00\x00\x00\x18ftypmp42 not a real video")
    return p


def seed_db(tmp_path: Path, video: Path):
    db = Database(tmp_path / "scripty.db")
    pid = db.create_project(name="Demo Film", slug="demo-film",
                            video_path=str(video), duration=6.0, fps=24.0,
                            width=640, height=360)
    pass_id = db.create_pass(project_id=pid, provider="mock")
    s0 = db.add_shot(pass_id=pass_id, idx=0, start_s=0.0, end_s=3.0,
                     scale="MS", subject="CAMP FIRE", location="FOREST",
                     action_text="Flames crackle.")
    s1 = db.add_shot(pass_id=pass_id, idx=1, start_s=3.0, end_s=6.0,
                     scale="WS", subject="RIDGE", location="RIDGE")
    return db, pid, pass_id, (s0, s1)


# --------------------------------------------------------------------------- #
# compare_videos
# --------------------------------------------------------------------------- #

class TestCompareVideos:
    def test_parses_all_and_average_from_stderr(self, tmp_path, monkeypatch):
        orig = touch_video(tmp_path, "orig.mp4")
        gen = touch_video(tmp_path, "gen.mp4")
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(
                cmd, 0, stdout="", stderr=f"{PSNR_LINE}\n{SSIM_LINE}\n")

        monkeypatch.setattr(video_mod, "_run", fake_run)
        monkeypatch.setattr(video_mod, "probe", fake_media)

        metrics = compare_videos(orig, gen, tmp_path / "work")

        assert metrics.ssim == pytest.approx(0.552053)
        assert metrics.psnr == pytest.approx(9.277296)
        assert metrics.detail["scaled_to"] == [640, 360]
        assert (tmp_path / "work").is_dir()
        # command shape: generated first, scaled to original's size, shortest
        cmd = calls[0]
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "scale=640:360" in graph
        assert "ssim=" in graph and "psnr=" in graph
        assert "shortest=1" in graph
        assert cmd[cmd.index("-i") + 1] == str(gen.resolve())

    def test_inf_psnr_capped_for_json(self, tmp_path, monkeypatch):
        orig = touch_video(tmp_path, "orig.mp4")
        gen = touch_video(tmp_path, "gen.mp4")
        stderr = (SSIM_LINE.replace("0.552053", "1.000000") + "\n" +
                  "[Parsed_psnr_4 @ 0x0] PSNR y:inf u:inf v:inf average:inf "
                  "min:inf max:inf\n")
        monkeypatch.setattr(video_mod, "_run", lambda cmd, **k:
                            subprocess.CompletedProcess(cmd, 0, "", stderr))
        monkeypatch.setattr(video_mod, "probe", fake_media)

        metrics = compare_videos(orig, gen, tmp_path / "work")

        assert metrics.psnr == video_mod.PSNR_CAP
        assert metrics.detail["psnr_inf"] is True
        json.dumps(metrics.detail)  # report must stay strict-JSON safe

    def test_falls_back_to_stats_files(self, tmp_path, monkeypatch):
        orig = touch_video(tmp_path, "orig.mp4")
        gen = touch_video(tmp_path, "gen.mp4")
        work = tmp_path / "work"

        def fake_run(cmd, **kwargs):
            work.joinpath("ssim_stats.log").write_text(
                "n:1 Y:0.9 U:0.9 V:0.9 All:0.80 (10.0)\n"
                "n:2 Y:0.9 U:0.9 V:0.9 All:0.60 (10.0)\n")
            work.joinpath("psnr_stats.log").write_text(
                "n:1 mse_avg:10.0 psnr_avg:30.0 psnr_y:30.0\n"
                "n:2 mse_avg:10.0 psnr_avg:20.0 psnr_y:20.0\n")
            return subprocess.CompletedProcess(cmd, 0, "", "no summary here")

        monkeypatch.setattr(video_mod, "_run", fake_run)
        monkeypatch.setattr(video_mod, "probe", fake_media)

        metrics = compare_videos(orig, gen, work)

        assert metrics.ssim == pytest.approx(0.70)
        assert metrics.psnr == pytest.approx(25.0)

    def test_missing_generated_file_rejected(self, tmp_path, monkeypatch):
        orig = touch_video(tmp_path, "orig.mp4")
        monkeypatch.setattr(video_mod, "_run", lambda *a, **k:
                            pytest.fail("must not shell out"))
        with pytest.raises(FileNotFoundError):
            compare_videos(orig, tmp_path / "missing.mp4", tmp_path / "w")

    def test_unparseable_output_raises(self, tmp_path, monkeypatch):
        orig = touch_video(tmp_path, "orig.mp4")
        gen = touch_video(tmp_path, "gen.mp4")
        monkeypatch.setattr(video_mod, "_run", lambda cmd, **k:
                            subprocess.CompletedProcess(cmd, 0, "", "junk"))
        monkeypatch.setattr(video_mod, "probe", fake_media)
        with pytest.raises(RuntimeError):
            compare_videos(orig, gen, tmp_path / "work")


# --------------------------------------------------------------------------- #
# critique_shots
# --------------------------------------------------------------------------- #

class TestCritiqueShots:
    def frames(self, tmp_path, n):
        out = []
        for i in range(n):
            f = tmp_path / f"f{i}.jpg"
            f.write_bytes(b"jpeg")
            out.append([f])
        return out

    def test_zips_and_splits_fix_marker(self, tmp_path):
        provider = FakeProvider([
            "Original is warmer.\nPROMPT FIXES: add golden-hour light.",
            "Generated ridge is barren.",
        ])
        notes = critique_shots(provider, self.frames(tmp_path, 2),
                               self.frames(tmp_path, 2),
                               ["shot one ctx", "shot two ctx"])

        assert [n.shot_idx for n in notes] == [0, 1]
        assert notes[0].differences == "Original is warmer."
        assert notes[0].prompt_fixes == "add golden-hour light."
        # no marker: full text lands in both fields
        assert notes[1].differences == notes[1].prompt_fixes == \
            "Generated ridge is barren."
        assert provider.calls[0][2] == "shot one ctx"

    def test_skips_empty_and_failing_critiques(self, tmp_path):
        provider = FakeProvider(["   ", RuntimeError("api down"), "real note"])
        notes = critique_shots(provider, self.frames(tmp_path, 3),
                               self.frames(tmp_path, 3), ["a", "b", "c"])
        assert [n.shot_idx for n in notes] == [2]

    def test_truncates_to_shortest_list(self, tmp_path):
        provider = FakeProvider(["note"])
        notes = critique_shots(provider, self.frames(tmp_path, 1),
                               self.frames(tmp_path, 3), ["a", "b", "c"])
        assert len(notes) == 1


# --------------------------------------------------------------------------- #
# revise_prompts
# --------------------------------------------------------------------------- #

class TestRevisePrompts:
    def seed_prompts(self, db, pass_id, shot_ids):
        p0 = db.add_describe_prompt(pass_id=pass_id, scene_id=None,
                                    shot_id=shot_ids[0], revision=1,
                                    target="generic",
                                    prompt="A campfire in a dark forest.",
                                    continuity={"style": "35mm"})
        p1 = db.add_describe_prompt(pass_id=pass_id, scene_id=None,
                                    shot_id=shot_ids[1], revision=1,
                                    target="generic",
                                    prompt="A wide ridge at dawn.",
                                    continuity={})
        scene_p = db.add_describe_prompt(pass_id=pass_id, scene_id=77,
                                         shot_id=None, revision=1,
                                         target="generic",
                                         prompt="Scene umbrella prompt.",
                                         continuity={})
        return p0, p1, scene_p

    def test_creates_revision_2_for_critiqued_shots_only(self, tmp_path):
        video = touch_video(tmp_path, "v.mp4")
        db, pid, pass_id, shot_ids = seed_db(tmp_path, video)
        self.seed_prompts(db, pass_id, shot_ids)

        critiques = [CritiqueNote(shot_idx=0, differences="colder",
                                  prompt_fixes="Add warm flicker.")]
        new_ids = revise_prompts(db, pass_id=pass_id, critiques=critiques)

        assert len(new_ids) == 1
        rev2 = db.prompts_for_pass(pass_id, revision=2)
        assert len(rev2) == 1
        row = rev2[0]
        assert row["id"] == new_ids[0]
        assert row["shot_id"] == shot_ids[0]
        assert row["prompt"] == ("A campfire in a dark forest."
                                 + video_mod.REVISION_MARKER
                                 + "Add warm flicker.")
        assert row["target"] == "generic"
        assert row["continuity"] == {"style": "35mm"}
        # revision-1 prompts untouched
        assert len(db.prompts_for_pass(pass_id, revision=1)) == 3

    def test_no_critiques_no_new_prompts(self, tmp_path):
        video = touch_video(tmp_path, "v.mp4")
        db, pid, pass_id, shot_ids = seed_db(tmp_path, video)
        self.seed_prompts(db, pass_id, shot_ids)
        assert revise_prompts(db, pass_id=pass_id, critiques=[]) == []
        assert db.prompts_for_pass(pass_id, revision=2) == []

    def test_brain_polishes_when_available(self, tmp_path):
        video = touch_video(tmp_path, "v.mp4")
        db, pid, pass_id, shot_ids = seed_db(tmp_path, video)
        self.seed_prompts(db, pass_id, shot_ids)
        brain = FakeBrain(reply="  Polished campfire prompt.  ")

        critiques = [CritiqueNote(shot_idx=0, differences="d",
                                  prompt_fixes="warmer light")]
        new_ids = revise_prompts(db, pass_id=pass_id, critiques=critiques,
                                 brain=brain)

        row = db.prompts_for_pass(pass_id, revision=2)[0]
        assert row["prompt"] == "Polished campfire prompt."
        assert "warmer light" in brain.calls[0][1]
        assert len(new_ids) == 1

    def test_brain_failure_falls_back_to_template(self, tmp_path):
        video = touch_video(tmp_path, "v.mp4")
        db, pid, pass_id, shot_ids = seed_db(tmp_path, video)
        self.seed_prompts(db, pass_id, shot_ids)

        critiques = [CritiqueNote(shot_idx=1, differences="d",
                                  prompt_fixes="More haze.")]
        revise_prompts(db, pass_id=pass_id, critiques=critiques,
                       brain=FakeBrain(error=True))

        row = db.prompts_for_pass(pass_id, revision=2)[0]
        assert row["prompt"].startswith("A wide ridge at dawn.")
        assert row["prompt"].endswith("More haze.")

    def test_duplicate_critiques_for_one_shot_merge(self, tmp_path):
        video = touch_video(tmp_path, "v.mp4")
        db, pid, pass_id, shot_ids = seed_db(tmp_path, video)
        self.seed_prompts(db, pass_id, shot_ids)

        critiques = [
            CritiqueNote(shot_idx=0, differences="a", prompt_fixes="Fix one."),
            CritiqueNote(shot_idx=0, differences="b", prompt_fixes="Fix two."),
        ]
        revise_prompts(db, pass_id=pass_id, critiques=critiques)

        row = db.prompts_for_pass(pass_id, revision=2)[0]
        assert "Fix one." in row["prompt"] and "Fix two." in row["prompt"]


# --------------------------------------------------------------------------- #
# run_comparison
# --------------------------------------------------------------------------- #

class TestRunComparison:
    def setup_run(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SCRIPTY_HOME", str(tmp_path / "home"))
        orig = touch_video(tmp_path, "orig.mp4")
        gen = touch_video(tmp_path, "gen.mp4")
        db, pid, pass_id, shot_ids = seed_db(tmp_path, orig)
        db.add_describe_prompt(pass_id=pass_id, scene_id=None,
                               shot_id=shot_ids[0], revision=1,
                               target="generic", prompt="Campfire prompt.",
                               continuity={})
        db.add_describe_prompt(pass_id=pass_id, scene_id=None,
                               shot_id=shot_ids[1], revision=1,
                               target="generic", prompt="Ridge prompt.",
                               continuity={})

        from scripty.core.models import VideoMetrics
        fake_metrics = VideoMetrics(ssim=0.91, psnr=31.5, detail={"k": "v"})
        cv_calls: list[tuple] = []

        def fake_compare(original, generated, workdir):
            cv_calls.append((Path(original), Path(generated), Path(workdir)))
            return fake_metrics

        kf_calls: list[tuple] = []

        def fake_keyframes(video, spans, out_dir, per_shot=3):
            kf_calls.append((Path(video), list(spans), Path(out_dir), per_shot))
            frames = []
            for i in range(len(spans)):
                f = Path(out_dir) / f"shot{i:04d}_f0.jpg"
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(b"jpeg")
                frames.append([f])
            return frames

        monkeypatch.setattr(video_mod, "compare_videos", fake_compare)
        monkeypatch.setattr(video_mod, "extract_keyframes", fake_keyframes)
        return db, pid, pass_id, orig, gen, cv_calls, kf_calls

    def test_orchestrates_full_flow(self, tmp_path, monkeypatch):
        db, pid, pass_id, orig, gen, cv_calls, kf_calls = \
            self.setup_run(tmp_path, monkeypatch)
        provider = FakeProvider([
            "diff one\nPROMPT FIXES: warmer fire.",
            "",  # second shot: empty critique -> skipped
        ])

        result = run_comparison(db, project_id=pid, pass_id=pass_id,
                                generated=gen, provider=provider)

        assert result == {"metrics": {"ssim": 0.91, "psnr": 31.5,
                                      "detail": {"k": "v"}},
                          "critiques": 1, "revised_prompts": 1}
        # compare_videos got original + generated + project compare dir
        assert cv_calls[0][0] == orig.resolve()
        assert cv_calls[0][1] == gen.resolve()
        assert cv_calls[0][2].name == "compare"
        # keyframes: both videos, same spans, one frame per shot
        assert len(kf_calls) == 2
        assert kf_calls[0][1] == [(0.0, 3.0), (3.0, 6.0)]
        assert kf_calls[0][3] == 1 and kf_calls[1][3] == 1
        assert kf_calls[1][0] == gen.resolve()
        # critique got a shot-context string
        assert "CAMP FIRE" in provider.calls[0][2]
        # artifacts registered
        kinds = [a["kind"] for a in db.artifacts_for_project(pid)]
        assert ArtifactKind.GENERATED_VIDEO.value in kinds
        assert ArtifactKind.COMPARISON_REPORT.value in kinds
        # report json exists, is valid, and carries the critique
        report_ref = next(a["ref"] for a in db.artifacts_for_project(pid)
                          if a["kind"] == ArtifactKind.COMPARISON_REPORT.value)
        report = json.loads(Path(report_ref).read_text())
        assert report["metrics"]["ssim"] == 0.91
        assert report["critiques"][0]["prompt_fixes"] == "warmer fire."
        assert len(report["revised_prompt_ids"]) == 1
        # revision-2 prompt exists for the critiqued shot
        assert len(db.prompts_for_pass(pass_id, revision=2)) == 1

    def test_provider_none_uses_vision_get_provider(self, tmp_path, monkeypatch):
        db, pid, pass_id, orig, gen, _, _ = self.setup_run(tmp_path, monkeypatch)
        provider = FakeProvider(["note", "note"])

        fake_providers = types.ModuleType("scripty.vision.providers")
        fake_providers.get_provider = lambda name=None: provider  # type: ignore[attr-defined]
        fake_vision = types.ModuleType("scripty.vision")
        fake_vision.providers = fake_providers  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "scripty.vision", fake_vision)
        monkeypatch.setitem(sys.modules, "scripty.vision.providers",
                            fake_providers)

        result = run_comparison(db, project_id=pid, pass_id=pass_id,
                                generated=gen)
        assert result["critiques"] == 2
        assert len(provider.calls) == 2

    def test_unknown_project_or_mismatched_pass_rejected(self, tmp_path,
                                                         monkeypatch):
        db, pid, pass_id, orig, gen, _, _ = self.setup_run(tmp_path, monkeypatch)
        provider = FakeProvider([])
        with pytest.raises(ValueError):
            run_comparison(db, project_id=999, pass_id=pass_id,
                           generated=gen, provider=provider)
        other_pid = db.create_project(name="Other", slug="other",
                                      video_path=str(orig), duration=1.0,
                                      fps=24.0, width=64, height=36)
        with pytest.raises(ValueError):
            run_comparison(db, project_id=other_pid, pass_id=pass_id,
                           generated=gen, provider=provider)

    def test_missing_generated_video_rejected(self, tmp_path, monkeypatch):
        db, pid, pass_id, orig, gen, _, _ = self.setup_run(tmp_path, monkeypatch)
        with pytest.raises(FileNotFoundError):
            run_comparison(db, project_id=pid, pass_id=pass_id,
                           generated=tmp_path / "nope.mp4",
                           provider=FakeProvider([]))
        # nothing registered for a rejected input
        kinds = [a["kind"] for a in db.artifacts_for_project(pid)]
        assert ArtifactKind.GENERATED_VIDEO.value not in kinds

    def test_keyframe_failure_still_returns_metrics(self, tmp_path,
                                                    monkeypatch):
        db, pid, pass_id, orig, gen, _, _ = self.setup_run(tmp_path, monkeypatch)

        def boom(video, spans, out_dir, per_shot=3):
            raise RuntimeError("ffmpeg exploded")

        monkeypatch.setattr(video_mod, "extract_keyframes", boom)
        result = run_comparison(db, project_id=pid, pass_id=pass_id,
                                generated=gen, provider=FakeProvider([]))
        assert result["metrics"]["ssim"] == 0.91
        assert result["critiques"] == 0
        assert result["revised_prompts"] == 0
        report_ref = next(a["ref"] for a in db.artifacts_for_project(pid)
                          if a["kind"] == ArtifactKind.COMPARISON_REPORT.value)
        report = json.loads(Path(report_ref).read_text())
        assert report["errors"] and "keyframe" in report["errors"][0]
