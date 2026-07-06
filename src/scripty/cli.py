"""Typer command-line interface for Scripty (`scripty <command>`).

Heavy modules (server, truth, compare, script) are imported inside command
bodies so `scripty --help` stays fast and the CLI works while sibling
modules are still being built.
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
                  help="Scripty - a machine script supervisor.")
lessons_app = typer.Typer(no_args_is_help=True, help="Distill and inspect lessons.")
describe_app = typer.Typer(no_args_is_help=True, help="Describe-track prompt tools.")
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
    """Start the dashboard server."""
    import uvicorn
    from scripty.server.app import create_app

    typer.echo(f"dashboard on http://{host}:{port}/")
    uvicorn.run(create_app(), host=host, port=port)


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


def main() -> None:
    """Console-script entry point (pyproject [project.scripts])."""
    app()


if __name__ == "__main__":
    main()
