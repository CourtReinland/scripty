"""Typer command-line interface for Scripty (`scripty <command>`).

The product surface is the human-in-the-loop writer (`scripty write`).
Film supervisor commands remain as a legacy toolkit.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from scripty import pipeline
from scripty.core import config
from scripty.core.db import Database

app = typer.Typer(no_args_is_help=True, add_completion=False,
                  help="Scripty — a human-in-the-loop fiction trainer.")
lessons_app = typer.Typer(no_args_is_help=True, help="Distill and inspect film-supervisor lessons.")
describe_app = typer.Typer(no_args_is_help=True, help="Describe-track prompt tools (legacy film).")
write_app = typer.Typer(no_args_is_help=True, help="Start and judge fiction drafts.")
app.add_typer(write_app, name="write")
app.add_typer(lessons_app, name="lessons")
app.add_typer(describe_app, name="describe")


def _db() -> Database:
    return Database(config.db_path())


def _fail(message: str) -> typer.Exit:
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(code=1)


@app.command()
def create(video: Path = typer.Argument(..., help="Path to the source video file"),
           name: Optional[str] = typer.Option(None, "--name", help="Project name")) -> None:
    """Register a video as a new Scripty project."""
    db = _db()
    try:
        project_id = pipeline.create_project(db, video, name=name)
    except FileNotFoundError as exc:
        raise _fail(str(exc))
    typer.echo(f"created project {project_id}")


@app.command("pass")
def run_pass_cmd(project_id: int,
                 provider: Optional[str] = typer.Option(None, "--provider"),
                 transcriber: Optional[str] = typer.Option(None, "--transcriber"),
                 brain: Optional[str] = typer.Option(None, "--brain")) -> None:
    """Run one full supervision pass over a project."""
    db = _db()
    try:
        pass_id = pipeline.run_pass(
            db, project_id, provider_name=provider, transcriber_name=transcriber,
            brain_name=brain, progress=lambda line: typer.echo(f"  {line}"))
    except (ValueError, FileNotFoundError) as exc:
        raise _fail(str(exc))
    typer.echo(f"pass {pass_id} complete")


@app.command()
def serve(port: int = typer.Option(config.SERVER_PORT, "--port"),
          host: str = typer.Option("127.0.0.1", "--host")) -> None:
    """Start the writer dashboard (film supervisor at /film)."""
    import uvicorn
    from scripty.server.app import create_app

    typer.echo(f"writer on http://{host}:{port}/   (legacy film at /film)")
    uvicorn.run(create_app(), host=host, port=port)


def _preview(text: str, limit: int = 700) -> str:
    body = (text or "").strip()
    return body if len(body) <= limit else body[:limit].rstrip() + "\n…"


def _print_session(view: dict) -> None:
    session = view["session"]
    desk = view.get("desk") or {}
    typer.echo(
        f"session {session['id']}  desk={desk.get('slug')}  "
        f"tone={session['tone']!r}  length={session['length']}  "
        f"unit={session['unit_kind']} {session['unit_index']}")
    champ = view.get("champion")
    chall = view.get("challenger")
    if champ:
        sig = champ.get("signals") or {}
        typer.echo(f"\nCHAMPION  #{champ['id']}  words={sig.get('word_count')}  "
                   f"seed={champ['seed']}  mutation={champ.get('mutation')}")
        typer.echo(_preview(champ["text"]))
    else:
        typer.echo("\nCHAMPION  (none yet — run `scripty write generate "
                   f"{session['id']}`)")
    if chall:
        sig = chall.get("signals") or {}
        typer.echo(f"\nCHALLENGER  #{chall['id']}  words={sig.get('word_count')}  "
                   f"novelty={sig.get('lexical_novelty')}  "
                   f"seed={chall['seed']}  mutation={chall.get('mutation')}")
        notes = sig.get("notes") or []
        if notes:
            typer.echo("signals (advisory): " + "; ".join(notes))
        typer.echo(_preview(chall["text"]))
        typer.echo(f"\njudge:  scripty write judge {session['id']} better|worse")
    elif champ:
        typer.echo(f"\nnext:  scripty write generate {session['id']}")


@write_app.command("start")
def write_start(
    genre: str = typer.Option(..., "--genre", "-g",
                              help="horror, literary, romance, thriller, "
                                   "slice_of_life, science_fiction, custom, …"),
    tone: str = typer.Option("measured", "--tone", "-t"),
    length: str = typer.Option("short", "--length", "-l",
                               help="short (complete piece) | medium | long (chapters)"),
    summary: str = typer.Option(..., "--summary", "-s"),
    provider: Optional[str] = typer.Option(None, "--provider"),
    draft: bool = typer.Option(True, "--draft/--no-draft",
                               help="Generate the first draft immediately"),
    ref_file: Optional[Path] = typer.Option(
        None, "--ref-file", help="Private training chunk (never printed back)"),
    ref_kind: str = typer.Option("user_excerpt", "--ref-kind"),
) -> None:
    """Open a session on a genre desk and (by default) write the first draft."""
    from scripty.write import generate, start_session

    ref_text = None
    if ref_file is not None:
        path = Path(ref_file).expanduser()
        if not path.is_file():
            raise _fail(f"file not found: {path}")
        ref_text = path.read_text(encoding="utf-8")
    db = _db()
    try:
        view = start_session(db, genre=genre, tone=tone, length=length,
                             summary=summary, provider=provider,
                             reference_text=ref_text,
                             reference_title=ref_file.name if ref_file else None,
                             reference_kind=ref_kind)
        if draft:
            view = generate(db, int(view["session"]["id"]))
    except ValueError as exc:
        raise _fail(str(exc))
    _print_session(view)


@write_app.command("generate")
def write_generate(session_id: int) -> None:
    """Write the first draft, or a randomized challenger against the champion."""
    from scripty.write import generate

    db = _db()
    try:
        view = generate(db, session_id)
    except (ValueError, RuntimeError) as exc:
        raise _fail(str(exc))
    _print_session(view)


@write_app.command("judge")
def write_judge(
    session_id: int,
    result: str = typer.Argument(..., help="better | worse"),
    note: str = typer.Option("", "--note", "-n"),
) -> None:
    """You are the only judge. better promotes the challenger; worse discards it."""
    from scripty.write import judge

    db = _db()
    try:
        view = judge(db, session_id, result, note=note)
    except ValueError as exc:
        raise _fail(str(exc))
    typer.echo(f"verdict: {result}")
    _print_session(view)


@write_app.command("show")
def write_show(session_id: int) -> None:
    """Print champion, pending challenger, and desk lessons."""
    from scripty.write import session_view

    db = _db()
    try:
        view = session_view(db, session_id)
    except ValueError as exc:
        raise _fail(str(exc))
    _print_session(view)
    lessons = view.get("lessons") or []
    if lessons:
        typer.echo("\nDESK LESSONS")
        for row in lessons:
            typer.echo(f"  - {row.get('rule')}")


@write_app.command("history")
def write_history(session_id: int) -> None:
    """List champion lineage for the current unit."""
    from scripty.write import session_view

    db = _db()
    try:
        view = session_view(db, session_id)
    except ValueError as exc:
        raise _fail(str(exc))
    rows = view.get("history") or []
    if not rows:
        typer.echo("no champions yet")
        return
    for row in rows:
        sig = row.get("signals") or {}
        typer.echo(
            f"#{row['id']}  {row['role']:9}  words={sig.get('word_count')}  "
            f"seed={row['seed']}  {row.get('created_at', '')}")


@write_app.command("lessons")
def write_lessons(genre: str = typer.Option(..., "--genre", "-g")) -> None:
    """List lessons saved on a genre desk."""
    from scripty.write import normalize_genre
    from scripty.write.store import desk_by_slug, lessons_for_desk

    db = _db()
    try:
        desk = desk_by_slug(db, normalize_genre(genre))
    except ValueError as exc:
        raise _fail(str(exc))
    rows = lessons_for_desk(db, int(desk["id"]))
    if not rows:
        typer.echo(f"no lessons on the {desk['slug']} desk yet")
        return
    for row in rows:
        typer.echo(f"[{row['id']}] {row['rule']}")


@write_app.command("ref")
def write_ref(
    genre: str = typer.Option(..., "--genre", "-g"),
    kind: str = typer.Option(..., "--kind", help="user_excerpt | public_domain"),
    title: str = typer.Option(..., "--title"),
    text: Optional[str] = typer.Option(None, "--text"),
    file: Optional[Path] = typer.Option(None, "--file"),
) -> None:
    """Attach a rights-cleared excerpt to a desk (never scrape living authors)."""
    from scripty.write import add_reference

    body = text
    if file is not None:
        path = Path(file).expanduser()
        if not path.is_file():
            raise _fail(f"file not found: {path}")
        body = path.read_text(encoding="utf-8")
    if not (body or "").strip():
        raise _fail("provide --text or --file")
    db = _db()
    try:
        info = add_reference(db, desk_slug=genre, kind=kind, title=title,
                             text=body or "")
    except ValueError as exc:
        raise _fail(str(exc))
    typer.echo(f"stored ref {info['id']} on {info['desk']} ({info['kind']})")


@write_app.command("next")
def write_next(session_id: int) -> None:
    """Advance to the next chapter (medium/long sessions)."""
    from scripty.write import advance

    db = _db()
    try:
        view = advance(db, session_id)
    except (ValueError, RuntimeError) as exc:
        raise _fail(str(exc))
    _print_session(view)


@write_app.command("list")
def write_list() -> None:
    """Recent writing sessions."""
    from scripty.write import list_sessions

    rows = list_sessions(_db())
    if not rows:
        typer.echo("no sessions yet")
        return
    for row in rows:
        typer.echo(
            f"{row['id']:>4}  {row.get('desk_slug', row['genre']):16}  "
            f"{row['length']:6}  {row['status']:7}  "
            f"{(row['summary'] or '')[:60]}")


@app.command()
def truth(project_id: int,
          script_path: Path = typer.Argument(..., help="Known script (.fountain/.txt)")) -> None:
    """Link a known (ground-truth) script to a project."""
    from scripty.truth.align import link_script

    db = _db()
    try:
        truth_id = link_script(db, project_id=project_id, script_path=script_path)
    except (FileNotFoundError, ValueError) as exc:
        raise _fail(str(exc))
    typer.echo(f"linked truth {truth_id}")


@app.command()
def align(pass_id: int,
          emit_corrections: bool = typer.Option(
              True, "--emit-corrections/--no-emit-corrections",
              help="Record ground-truth diffs as corrections")) -> None:
    """Align a pass against the project's ground-truth script."""
    from scripty.truth.align import align_pass, emit_auto_corrections

    db = _db()
    try:
        report = align_pass(db, pass_id=pass_id)
    except (ValueError, FileNotFoundError) as exc:
        raise _fail(str(exc))
    emitted = (emit_auto_corrections(db, pass_id=pass_id, report=report)
               if emit_corrections else 0)
    dialogue = report.get("dialogue", {})
    typer.echo(f"scene pairs: {len(report.get('scene_pairs', []))}  "
               f"slugline accuracy: {report.get('slugline_accuracy')}")
    typer.echo(f"dialogue matched: {dialogue.get('matched')}/{dialogue.get('truth_lines')}"
               f"  character accuracy: {dialogue.get('character_accuracy')}")
    typer.echo(f"auto-corrections emitted: {emitted}")


@lessons_app.command("distill")
def lessons_distill(project: Optional[int] = typer.Option(None, "--project")) -> None:
    """Distill fresh corrections into imperative lessons."""
    from scripty.brain import get_brain
    from scripty.learn.distill import distill

    db = _db()
    try:
        brain = get_brain()
    except Exception:  # no credentials / SDK trouble -> offline twin
        brain = get_brain("mock")
    new_ids = distill(db, brain, project_id=project)
    suffix = f": {new_ids}" if new_ids else ""
    typer.echo(f"distilled {len(new_ids)} lesson(s){suffix}")


@lessons_app.command("list")
def lessons_list(project: Optional[int] = typer.Option(None, "--project")) -> None:
    """List active lessons (weight-ranked)."""
    db = _db()
    rows = db.lessons(project_id=project)
    if not rows:
        typer.echo("no lessons yet")
        return
    for row in rows:
        typer.echo(f"[{row['id']:>4}] ({row['field']}, w={float(row['weight']):.1f}) "
                   f"{row['rule']}")


@describe_app.command("export")
def describe_export(pass_id: int,
                    target: Optional[str] = typer.Option(None, "--target"),
                    revision: Optional[int] = typer.Option(None, "--revision")) -> None:
    """Print the describe-prompt sheet for a pass."""
    from scripty.describe import export_text

    db = _db()
    prompts = db.prompts_for_pass(pass_id, revision=revision)
    if target:
        prompts = [p for p in prompts if p.get("target") == target]
    typer.echo(export_text(prompts))


@app.command()
def compare(project_id: int, pass_id: int,
            generated_video: Path = typer.Argument(..., help="Gen-AI output video")) -> None:
    """Compare a generated video back against the original (recursive loop)."""
    from scripty.compare.video import run_comparison

    resolved = Path(generated_video).expanduser().resolve()
    if not resolved.is_file():
        raise _fail(f"generated video not found: {resolved}")
    db = _db()
    result = run_comparison(db, project_id=project_id, pass_id=pass_id,
                            generated=resolved)
    typer.echo(json.dumps(result, indent=2, sort_keys=True, default=str))


@app.command()
def demo() -> None:
    """Build a synthetic film and run a fully offline pass on it."""
    db = _db()
    project_id, pass_id = pipeline.demo(db)
    typer.echo(f"demo ready: project {project_id}, pass {pass_id}")
    typer.echo(f"next: `scripty serve` then open http://127.0.0.1:{config.SERVER_PORT}/")


@app.command()
def log(pass_id: int) -> None:
    """Print the classic cut log for a pass."""
    from scripty.script.assemble import cut_log

    db = _db()
    pass_row = db.get_pass(pass_id)
    if pass_row is None:
        raise _fail(f"no such pass: {pass_id}")
    project = db.get_project(int(pass_row["project_id"]))
    fps = float(project["fps"]) if project else 24.0
    typer.echo(cut_log(db.shots_for_pass(pass_id), fps=fps))


@app.command("export-canon")
def export_canon(
    project_id: int = typer.Argument(..., help="Scripty project id"),
    out: Path = typer.Option(..., "--out", "-o", help="Output JSON path"),
    pass_id: Optional[int] = typer.Option(
        None, "--pass", help="Pass id (default: latest complete)"),
    tier: str = typer.Option("UNRANKED", "--tier", help="S|A|B|C|UNRANKED"),
    director: Optional[list[str]] = typer.Option(
        None, "--director", help="Director name (repeatable)"),
    genre: Optional[list[str]] = typer.Option(
        None, "--genre", help="Genre tag (repeatable)"),
    theme: str = typer.Option("", "--theme"),
    logline: str = typer.Option("", "--logline"),
    plot_summary: str = typer.Option("", "--plot-summary"),
) -> None:
    """Export a pass as a director-bot canon work bundle (JSON).

    Import with: director-bot canon import <file>
    """
    from scripty.canon_export import build_canon_export, write_canon_export

    db = _db()
    try:
        bundle = build_canon_export(
            db, project_id, pass_id,
            tier=tier,
            directors=list(director or []),
            genres=list(genre or []),
            theme=theme,
            logline=logline,
            plot_summary=plot_summary,
        )
    except ValueError as exc:
        raise _fail(str(exc))
    path = write_canon_export(bundle, out)
    n_shots = len(bundle.get("shot_moments") or [])
    n_cards = len(bundle.get("scene_cards") or [])
    typer.echo(f"wrote {path}  cards={n_cards} shots={n_shots} "
               f"tier={tier}")


def main() -> None:
    """Console-script entry point (pyproject [project.scripts])."""
    app()


if __name__ == "__main__":
    main()
