"""Pipeline orchestration: create projects, run supervision passes, demo mode.

Cross-module imports happen inside functions so this module imports cleanly
(and unit-tests can inject mocks via ``sys.modules``) even while sibling
modules are still being built in parallel.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from scripty.core import config
from scripty.core.db import Database
from scripty.core.models import (
    ArtifactKind,
    RecallBundle,
    ShotContext,
    TranscriptSegment,
)

Progress = Optional[Callable[[str], None]]

#: shot fields whose prior corrections/lessons are recalled into every prompt
RECALL_FIELDS: tuple[str, ...] = (
    "scale", "subject", "location", "time_of_day", "int_ext", "action_text",
)


def _say(progress: Progress, message: str) -> None:
    if progress is not None:
        progress(message)


def _unique_slug(db: Database, base: str) -> str:
    slug, n = base, 2
    while db.project_by_slug(slug) is not None:
        slug = f"{base}-{n}"
        n += 1
    return slug


def create_project(db: Database, video: Path, name: str | None = None) -> int:
    """Probe a video, register it as a project, and log the source artifact."""
    from scripty.ingest import probe

    video = Path(video).expanduser().resolve()
    if not video.is_file():
        raise FileNotFoundError(f"video not found: {video}")
    info = probe(video)
    title = (name or video.stem).strip() or video.stem
    slug = _unique_slug(db, config.slugify(title))
    project_id = db.create_project(
        name=title, slug=slug, video_path=str(video), duration=info.duration,
        fps=info.fps, width=info.width, height=info.height,
    )
    db.add_artifact(project_id=project_id, kind=ArtifactKind.SOURCE_VIDEO.value,
                    ref=str(video))
    return project_id


def _merged_recall(db: Database, project_id: int) -> RecallBundle:
    """One RecallBundle covering the hot shot fields, k divided per field."""
    from scripty.learn.memory import recall

    k_lessons = max(1, config.RECALL_LESSONS // len(RECALL_FIELDS))
    k_examples = max(1, config.RECALL_EXAMPLES // len(RECALL_FIELDS))
    bundle = RecallBundle()
    seen_lessons: set[int] = set()
    seen_examples: set[int] = set()
    for field in RECALL_FIELDS:
        part = recall(db, project_id=project_id, field=field,
                      k_lessons=k_lessons, k_examples=k_examples)
        for lesson in part.lessons:
            if lesson.id not in seen_lessons:
                seen_lessons.add(lesson.id)
                bundle.lessons.append(lesson)
        for example in part.examples:
            if example.id not in seen_examples:
                seen_examples.add(example.id)
                bundle.examples.append(example)
    return bundle


def _excerpt(segments: list[TranscriptSegment], start_s: float, end_s: float) -> str:
    """Transcript text whose segment midpoints fall inside [start_s, end_s)."""
    parts = [seg.text for seg in segments
             if start_s <= (seg.start_s + seg.end_s) / 2.0 < end_s]
    return " ".join(parts)[:500]


def run_pass(db: Database, project_id: int, *, provider_name: str | None = None,
             transcriber_name: str | None = None, brain_name: str | None = None,
             progress: Progress = None) -> int:
    """Run one full supervision pass over a project's video.

    Detect cuts, label every shot (recall-aware), transcribe & attribute
    dialogue, group scenes, emit fountain/HTML/describe artifacts, and
    record learning-curve metrics. Failures mark the pass 'failed' and
    re-raise.
    """
    from scripty.audio import get_transcriber
    from scripty.vision import get_provider

    project = db.get_project(project_id)
    if project is None:
        raise ValueError(f"no such project: {project_id}")
    video = Path(str(project["video_path"])).expanduser().resolve()
    if not video.is_file():
        raise FileNotFoundError(f"project video missing: {video}")

    provider = get_provider(provider_name)
    transcriber = get_transcriber(transcriber_name)
    pass_id = db.create_pass(
        project_id=project_id, provider=provider.name, transcriber=transcriber.name,
        params={"provider": provider.name, "transcriber": transcriber.name,
                "brain": brain_name or ""},
    )
    pass_row = db.get_pass(pass_id) or {}
    number = int(pass_row.get("number", 1))
    _say(progress, f"pass {number} started (provider={provider.name})")
    try:
        metrics = _execute_pass(db, project=project, pass_id=pass_id, number=number,
                                video=video, provider=provider, transcriber=transcriber,
                                brain_name=brain_name, progress=progress)
    except Exception as exc:
        db.finish_pass(pass_id, "failed", {"error": str(exc)})
        raise
    db.finish_pass(pass_id, "complete", metrics)
    _say(progress, f"pass {number} complete")
    return pass_id


def _execute_pass(db: Database, *, project: dict, pass_id: int, number: int,
                  video: Path, provider: object, transcriber: object,
                  brain_name: str | None, progress: Progress) -> dict:
    """The body of run_pass; any exception marks the pass failed upstream."""
    from scripty.audio import assign_speakers, dialogue_from_segments
    from scripty.describe import export_text, generate_prompts
    from scripty.ingest import (
        cuts_to_shots, detect_cuts, extract_audio, extract_keyframes,
    )
    from scripty.learn.memory import agreement_metrics
    from scripty.script.assemble import group_scenes
    from scripty.script.fountain import to_fountain
    from scripty.script.render import render_html

    project_id = int(project["id"])
    pdir = config.project_dir(str(project["slug"]))

    _say(progress, "detecting cuts")
    cuts = detect_cuts(video)
    spans = cuts_to_shots(cuts, float(project["duration"]))
    _say(progress, f"{len(spans)} shot(s) found")

    kf_dir = pdir / "keyframes" / f"pass{number}"
    kf_dir.mkdir(parents=True, exist_ok=True)
    frames = extract_keyframes(video, spans, kf_dir)

    _say(progress, "extracting audio / transcript")
    wav = extract_audio(video, pdir / "audio" / f"pass{number}.wav")
    segments = transcriber.transcribe(wav) if wav is not None else []
    segments = assign_speakers(segments)

    bundle = _merged_recall(db, project_id)
    prev_summaries: list[str] = []
    for idx, (start_s, end_s) in enumerate(spans):
        _say(progress, f"analyzing shot {idx + 1}/{len(spans)}")
        shot_frames = list(frames[idx]) if idx < len(frames) else []
        ctx = ShotContext(
            film_title=str(project["name"]), pass_number=number, shot_index=idx,
            total_shots=len(spans), start_s=start_s, end_s=end_s,
            prev_summaries=prev_summaries[-3:],
            transcript_excerpt=_excerpt(segments, start_s, end_s),
        )
        analysis = provider.analyze_shot(shot_frames, ctx, bundle)
        db.add_shot(
            pass_id=pass_id, idx=idx, start_s=start_s, end_s=end_s,
            scale=analysis.scale, subject=analysis.subject, angle=analysis.angle,
            move=analysis.move, int_ext=analysis.int_ext,
            location=analysis.location, time_of_day=analysis.time_of_day,
            action_text=analysis.action_text,
            characters=[c.name for c in analysis.characters], mood=analysis.mood,
            confidence=analysis.confidence,
            keyframes=[str(p) for p in shot_frames], raw=analysis.raw,
        )
        prev_summaries.append(
            f"{analysis.scale.value} {(analysis.subject or 'UNKNOWN').upper()}")

    shot_rows = db.shots_for_pass(pass_id)

    for line in dialogue_from_segments(segments, shot_rows):
        db.add_dialogue(pass_id=pass_id, **line)

    _say(progress, "assembling scenes")
    for scene in group_scenes(shot_rows):
        db.add_scene(pass_id=pass_id, **scene)
    scene_rows = db.scenes_for_pass(pass_id)

    shot_to_scene: dict[int, int] = {}
    for scene in scene_rows:
        for sid in scene.get("shot_ids") or []:
            shot_to_scene[int(sid)] = int(scene["id"])
    for row in db.dialogue_for_pass(pass_id):
        scene_id = shot_to_scene.get(row.get("shot_id"))
        if scene_id is not None and row.get("scene_id") is None:
            db.update("dialogue", int(row["id"]), scene_id=scene_id)
    dialogue_rows = db.dialogue_for_pass(pass_id)

    _say(progress, "writing screenplay artifacts")
    art_dir = pdir / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)
    fountain_text = to_fountain(scene_rows, shot_rows, dialogue_rows,
                                title=str(project["name"]).upper())
    fountain_path = art_dir / f"pass{number}.fountain"
    fountain_path.write_text(fountain_text, encoding="utf-8")
    html_path = art_dir / f"pass{number}.html"
    html_path.write_text(render_html(fountain_text, title=str(project["name"])),
                         encoding="utf-8")
    db.add_artifact(project_id=project_id, kind=ArtifactKind.GENERATED_SCRIPT.value,
                    ref=str(fountain_path), pass_id=pass_id,
                    meta={"html": str(html_path)})

    _say(progress, "generating describe prompts")
    brain = None
    if provider.name == "anthropic":
        from scripty.brain import get_brain
        brain = get_brain(brain_name)
    prompts = generate_prompts(scene_rows, shot_rows, dialogue_rows, brain=brain,
                               lessons=db.lessons(project_id=project_id))
    for prompt in prompts:
        db.add_describe_prompt(pass_id=pass_id, **prompt)
    prompts_path = art_dir / f"pass{number}_prompts.txt"
    prompts_path.write_text(export_text(prompts), encoding="utf-8")
    db.add_artifact(project_id=project_id, kind=ArtifactKind.DESCRIBE_SET.value,
                    ref=str(prompts_path), pass_id=pass_id,
                    meta={"count": len(prompts)})

    _say(progress, "computing metrics")
    confidences = [float(s.get("confidence") or 0.0) for s in shot_rows]
    metrics: dict = {
        "n_shots": len(shot_rows),
        "n_scenes": len(scene_rows),
        "n_dialogue": len(dialogue_rows),
        "mean_confidence": (round(sum(confidences) / len(confidences), 4)
                            if confidences else 0.0),
    }
    agreement = agreement_metrics(db, project_id=project_id, pass_id=pass_id)
    metrics["agreement"] = agreement
    metrics["agreement_rate"] = agreement.get("agreement_rate")

    if db.truth_for_project(project_id) is not None:
        _say(progress, "aligning against ground truth")
        try:
            from scripty.truth.align import align_pass
            report = align_pass(db, pass_id=pass_id)
            metrics["slugline_accuracy"] = report.get("slugline_accuracy")
            metrics["dialogue_match"] = report.get("dialogue", {})
        except Exception as exc:  # alignment must never sink a finished pass
            metrics["alignment_error"] = str(exc)
    return metrics


def demo(db: Database) -> tuple[int, int]:
    """Build a tiny synthetic film and run a fully offline pass over it."""
    from scripty.ingest.testfilm import make_test_film

    out = config.home() / "demo.mp4"
    if not out.is_file():
        make_test_film(out, shots=4, shot_seconds=2.0)
    project_id = create_project(db, out, name="Scripty Demo")
    pass_id = run_pass(db, project_id, provider_name="mock",
                       transcriber_name="none", brain_name="mock")
    return project_id, pass_id
