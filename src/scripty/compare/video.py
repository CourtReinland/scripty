"""Compare an original film against its gen-AI regeneration.

Three layers: pixel metrics (ffmpeg SSIM/PSNR), semantic critique
(a VisionProvider looks at paired keyframes), and the recursive
re-prompt loop (revision-2 describe prompts folding critique notes in).
All subprocess traffic goes through the single seam ``_run`` so tests
can mock one function and stay fully offline.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from scripty.core import config
from scripty.core.db import Database
from scripty.core.interfaces import TextBrain, VisionProvider
from scripty.core.models import ArtifactKind, CritiqueNote, VideoMetrics
from scripty.ingest.ffmpeg import extract_keyframes, probe

_SSIM_ALL = re.compile(r"SSIM.*?All:\s*(inf|[0-9]*\.?[0-9]+)", re.IGNORECASE)
_PSNR_AVG = re.compile(r"PSNR.*?average:\s*(inf|[0-9]*\.?[0-9]+)", re.IGNORECASE)
_STATS_SSIM = re.compile(r"All:\s*(inf|[0-9]*\.?[0-9]+)", re.IGNORECASE)
_STATS_PSNR = re.compile(r"psnr_avg:\s*(inf|[0-9]*\.?[0-9]+)", re.IGNORECASE)
_FIX_MARKER = re.compile(
    r"(?im)^[\s#*\-]*(?:prompt[ _-]?fix(?:es)?|suggested fix(?:es)?|"
    r"revision notes?|fixes)\s*[:\-]\s*")

PSNR_CAP = 100.0  # identical frames report psnr=inf; keep JSON finite

REVISION_MARKER = "\n\nREVISION NOTES (from comparison with generated output): "

REVISION_SYSTEM = (
    "You revise prompts for generative video models. Given an original "
    "prompt and critique notes comparing the original film shot with the "
    "generated result, rewrite the prompt so the next generation fixes the "
    "noted divergences. Keep everything that already worked. Answer with "
    "the revised prompt text only."
)


# --------------------------------------------------------------------------- #
# internals
# --------------------------------------------------------------------------- #

def _run(cmd: list[str], *, timeout: float = 900.0,
         cwd: Path | str | None = None) -> subprocess.CompletedProcess[str]:
    """Run one ffmpeg command; raise RuntimeError on failure."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, check=False,
                              cwd=str(cwd) if cwd is not None else None)
    except FileNotFoundError as exc:
        raise RuntimeError(f"executable not found: {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{cmd[0]} timed out after {timeout}s") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-2000:]
        raise RuntimeError(f"{cmd[0]} failed (exit {proc.returncode}): {tail}")
    return proc


def _validate_file(path: Path, kind: str = "video") -> Path:
    """Resolve a user-supplied path and require it to be an existing file."""
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"{kind} file not found: {resolved}")
    return resolved


def _metric_value(text: str) -> Optional[float]:
    if text.lower() == "inf":
        return math.inf
    try:
        return float(text)
    except ValueError:
        return None


def _from_stats_file(path: Path, pattern: re.Pattern[str]) -> Optional[float]:
    """Mean of the per-frame metric values in an ffmpeg stats file."""
    if not path.is_file():
        return None
    values = [v for m in pattern.finditer(path.read_text(errors="replace"))
              if (v := _metric_value(m.group(1))) is not None]
    finite = [v for v in values if math.isfinite(v)]
    if finite:
        return sum(finite) / len(finite)
    return math.inf if values else None


def _split_critique(text: str) -> tuple[str, str]:
    """Split a critique into (differences, prompt_fixes) on a fixes marker."""
    match = _FIX_MARKER.search(text)
    if not match:
        clean = text.strip()
        return clean, clean
    differences = text[:match.start()].strip()
    fixes = text[match.end():].strip()
    return differences or fixes, fixes or differences


def _shot_context(shot: dict) -> str:
    """Human-readable one-shot summary handed to critique_frames."""
    label = f"{shot.get('scale', 'MS')} {(shot.get('subject') or 'UNKNOWN')}"
    slug = (f"{shot.get('int_ext', 'EXT.')} {shot.get('location') or 'UNKNOWN'}"
            f" - {shot.get('time_of_day', 'DAY')}")
    action = (shot.get("action_text") or "").strip()
    span = f"{shot.get('start_s', 0.0):.2f}-{shot.get('end_s', 0.0):.2f}s"
    parts = [f"Shot {int(shot.get('idx', 0)) + 1} ({span}): {label}", slug]
    if action:
        parts.append(action)
    return " | ".join(parts)


# --------------------------------------------------------------------------- #
# public API (pinned)
# --------------------------------------------------------------------------- #

def compare_videos(original: Path, generated: Path, workdir: Path) -> VideoMetrics:
    """Compute SSIM + PSNR between the original film and a generated video.

    The generated video is scaled to the original's dimensions and the
    comparison stops with the shorter stream. "All:"/"average:" summary
    values are parsed from ffmpeg stderr, falling back to the per-frame
    stats files written into ``workdir``.
    """
    orig = _validate_file(original, "original video")
    gen = _validate_file(generated, "generated video")
    dest = Path(workdir).expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)

    info = probe(orig)
    if info.width <= 0 or info.height <= 0:
        raise RuntimeError(f"could not determine dimensions of {orig}")

    ssim_log = dest / "ssim_stats.log"
    psnr_log = dest / "psnr_stats.log"
    # The stats files are referenced RELATIVE to a cwd of ``dest`` so no
    # user-controlled path ever needs escaping inside the filter graph
    # (ffmpeg filtergraph escaping rules are fragile: ':', ';', '[', ']'
    # and quotes in an absolute workdir path would break or mangle it).
    graph = (
        f"[0:v]scale={info.width}:{info.height}:flags=bicubic,split=2[g1][g2];"
        f"[1:v]split=2[o1][o2];"
        f"[g1][o1]ssim=stats_file={ssim_log.name}:shortest=1[ssim];"
        f"[g2][o2]psnr=stats_file={psnr_log.name}:shortest=1[psnr]"
    )
    proc = _run(["ffmpeg", "-hide_banner", "-nostdin", "-y",
                 "-i", str(gen), "-i", str(orig),
                 "-filter_complex", graph,
                 "-map", "[ssim]", "-map", "[psnr]",
                 "-an", "-f", "null", "-"], cwd=dest)

    output = (proc.stderr or "") + "\n" + (proc.stdout or "")
    ssim_match = _SSIM_ALL.search(output)
    psnr_match = _PSNR_AVG.search(output)
    ssim = (_metric_value(ssim_match.group(1)) if ssim_match
            else _from_stats_file(ssim_log, _STATS_SSIM))
    psnr = (_metric_value(psnr_match.group(1)) if psnr_match
            else _from_stats_file(psnr_log, _STATS_PSNR))
    if ssim is None or psnr is None:
        raise RuntimeError("could not parse SSIM/PSNR from ffmpeg output")

    detail: dict = {
        "ssim_stats_file": str(ssim_log),
        "psnr_stats_file": str(psnr_log),
        "scaled_to": [info.width, info.height],
    }
    if ssim_match:
        detail["ssim_summary"] = ssim_match.group(0)
    if psnr_match:
        detail["psnr_summary"] = psnr_match.group(0)
    if math.isinf(psnr):
        detail["psnr_inf"] = True
        psnr = PSNR_CAP
    if math.isinf(ssim):  # never happens in practice; stay finite anyway
        ssim = 1.0
    return VideoMetrics(ssim=float(ssim), psnr=float(psnr), detail=detail)


def critique_shots(provider: VisionProvider, original_frames: list[list[Path]],
                   generated_frames: list[list[Path]],
                   contexts: list[str]) -> list[CritiqueNote]:
    """Ask the vision provider to compare each shot's original vs generated frames.

    Shots are zipped positionally; empty critiques (and shots whose
    critique call fails) are skipped.
    """
    notes: list[CritiqueNote] = []
    for idx, (orig, gen, context) in enumerate(
            zip(original_frames, generated_frames, contexts)):
        if not orig or not gen:
            continue
        try:
            text = provider.critique_frames(list(orig), list(gen), context)
        except Exception:  # one bad shot must not sink the comparison
            continue
        if not text or not text.strip():
            continue
        differences, fixes = _split_critique(text)
        notes.append(CritiqueNote(shot_idx=idx, differences=differences,
                                  prompt_fixes=fixes))
    return notes


def revise_prompts(db: Database, *, pass_id: int,
                   critiques: list[CritiqueNote],
                   brain: TextBrain | None = None) -> list[int]:
    """Fold critique fixes into revision-2 describe prompts (recursive loop).

    Every existing revision-1 prompt whose shot idx appears in ``critiques``
    gets a revision-2 sibling: the old prompt plus REVISION NOTES (or a
    brain-polished merge when a TextBrain is supplied and succeeds).
    """
    fixes_by_idx: dict[int, str] = {}
    for note in critiques:
        fix = (note.prompt_fixes or note.differences or "").strip()
        if not fix:
            continue
        prev = fixes_by_idx.get(note.shot_idx)
        fixes_by_idx[note.shot_idx] = f"{prev}\n{fix}" if prev else fix
    if not fixes_by_idx:
        return []

    idx_by_shot_id = {s["id"]: s["idx"] for s in db.shots_for_pass(pass_id)}
    new_ids: list[int] = []
    for old in db.prompts_for_pass(pass_id, revision=1):
        shot_id = old.get("shot_id")
        if shot_id is None or idx_by_shot_id.get(shot_id) not in fixes_by_idx:
            continue
        fixes = fixes_by_idx[idx_by_shot_id[shot_id]]
        new_prompt = f"{old['prompt']}{REVISION_MARKER}{fixes}"
        if brain is not None:
            try:
                polished = brain.complete(
                    REVISION_SYSTEM,
                    f"ORIGINAL PROMPT:\n{old['prompt']}\n\n"
                    f"CRITIQUE NOTES:\n{fixes}\n\nRevised prompt:")
                if polished and polished.strip():
                    new_prompt = polished.strip()
            except Exception:
                pass  # brain failure never blocks the revision loop
        new_ids.append(db.add_describe_prompt(
            pass_id=old["pass_id"], scene_id=old.get("scene_id"),
            shot_id=shot_id, revision=int(old.get("revision", 1)) + 1,
            target=old.get("target", "generic"), prompt=new_prompt,
            continuity=old.get("continuity") or {}))
    return new_ids


def run_comparison(db: Database, *, project_id: int, pass_id: int,
                   generated: Path,
                   provider: VisionProvider | None = None) -> dict:
    """Full comparison pass: metrics, critiques, revised prompts, artifacts.

    Registers the GENERATED_VIDEO artifact, runs SSIM/PSNR into the
    project's compare/ dir, extracts one keyframe per shot from both
    videos, critiques them shot-by-shot, emits revision-2 prompts, and
    stores a COMPARISON_REPORT json artifact.
    """
    project = db.get_project(project_id)
    if project is None:
        raise ValueError(f"unknown project id: {project_id}")
    pass_row = db.get_pass(pass_id)
    if pass_row is None or pass_row.get("project_id") != project_id:
        raise ValueError(f"pass {pass_id} does not belong to project {project_id}")

    gen = _validate_file(Path(generated), "generated video")
    original = _validate_file(Path(project["video_path"]), "original video")
    compare_dir = config.project_dir(project["slug"]) / "compare"
    compare_dir.mkdir(parents=True, exist_ok=True)

    db.add_artifact(project_id=project_id,
                    kind=ArtifactKind.GENERATED_VIDEO.value,
                    ref=str(gen), pass_id=pass_id)

    metrics = compare_videos(original, gen, compare_dir)

    shots = db.shots_for_pass(pass_id)
    spans = [(float(s["start_s"]), float(s["end_s"])) for s in shots]
    contexts = [_shot_context(s) for s in shots]
    number = int(pass_row.get("number") or 0)
    errors: list[str] = []
    orig_frames: list[list[Path]] = []
    gen_frames: list[list[Path]] = []
    if spans:
        try:
            orig_frames = extract_keyframes(
                original, spans, compare_dir / f"pass{number}_orig", per_shot=1)
            gen_frames = extract_keyframes(
                gen, spans, compare_dir / f"pass{number}_gen", per_shot=1)
        except RuntimeError as exc:  # keep metrics even if frame grabs fail
            errors.append(f"keyframe extraction failed: {exc}")
            orig_frames, gen_frames = [], []

    if provider is None:
        from scripty.vision.providers import get_provider  # lazy: parallel build
        provider = get_provider()

    critiques = critique_shots(provider, orig_frames, gen_frames, contexts)
    revised = revise_prompts(db, pass_id=pass_id, critiques=critiques)

    report = {
        "project_id": project_id,
        "pass_id": pass_id,
        "generated_video": str(gen),
        "metrics": asdict(metrics),
        "critiques": [asdict(c) for c in critiques],
        "revised_prompt_ids": revised,
        "errors": errors,
    }
    report_path = compare_dir / f"pass{number}_comparison.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True))
    db.add_artifact(project_id=project_id,
                    kind=ArtifactKind.COMPARISON_REPORT.value,
                    ref=str(report_path), pass_id=pass_id,
                    meta={"critiques": len(critiques),
                          "revised_prompts": len(revised)})

    return {"metrics": asdict(metrics), "critiques": len(critiques),
            "revised_prompts": len(revised)}
