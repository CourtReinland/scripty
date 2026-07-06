"""Renderers: fountain text -> plain-text Hollywood layout / standalone HTML.

Both renderers use the same parse-free line classification
(`scripty.script.fountain.classify_lines`), so they always agree.
"""
from __future__ import annotations

import html as _html
import textwrap

from .fountain import classify_lines

CONTENT_COLS = 60
CHARACTER_INDENT = 22
PAREN_INDENT = 16
DIALOGUE_INDENT = 10
DIALOGUE_WIDTH = 35
PAGE_LINES = 55

_CSS = """
* { box-sizing: border-box; }
body { background: #23262b; margin: 0; padding: 2rem 0.5rem; }
.page {
  background: #fff; color: #16181c; max-width: 8.5in; min-height: 11in;
  margin: 0 auto; padding: 1in 1in 1in 1.5in;
  font-family: 'Courier Prime', 'Courier New', Courier, monospace;
  font-size: 12pt; line-height: 1.2;
  box-shadow: 0 3px 20px rgba(0, 0, 0, 0.5);
}
.page p { margin: 0; }
.title-page { text-align: center; margin: 2.2in 0 1.4in; }
.title-page .title { font-weight: bold; text-decoration: underline; }
.title-page .byline { margin-top: 1em; }
.title-page .author { margin-top: 1em; }
.slug { font-weight: bold; margin: 1.4em 0 0.7em; }
.action { margin: 0.7em 0; white-space: pre-wrap; }
.character { margin: 1em 0 0 2.2in; }
.paren { margin: 0 0 0 1.6in; max-width: 2.6in; }
.dialogue { margin: 0 0 0 1in; max-width: 3.5in; }
""".strip()


def _centered(text: str) -> str:
    return text.center(CONTENT_COLS).rstrip()


def render_text(fountain_text: str) -> str:
    """Plain-text Hollywood layout in a 60-column content region.

    Sluglines and action sit at column 0, character names are indented
    22, parentheticals 16, dialogue 10 (wrapped at 35 characters). Scene
    numbers are omitted. A page-break marker ``--- p.N ---`` is emitted
    every 55 content lines.
    """
    title_page, items = classify_lines(fountain_text)
    out: list[str] = []
    count = 0

    def emit(line: str = "") -> None:
        nonlocal count
        if count and count % PAGE_LINES == 0:
            out.append(f"--- p.{count // PAGE_LINES + 1} ---")
        out.append(line.rstrip())
        count += 1

    title = (title_page.get("title") or "").strip()
    author = (title_page.get("author") or title_page.get("authors") or "").strip()
    if title:
        emit(_centered(title.upper()))
        emit()
        if author:
            emit(_centered("by"))
            emit()
            emit(_centered(author))
        emit()
        emit()

    for kind, line in items:
        if kind == "blank":
            emit()
        elif kind == "slug":
            emit(line.upper())
        elif kind == "character":
            emit(" " * CHARACTER_INDENT + line.upper())
        elif kind == "paren":
            emit(" " * PAREN_INDENT + line)
        elif kind == "dialogue":
            for piece in textwrap.wrap(line, DIALOGUE_WIDTH) or [""]:
                emit(" " * DIALOGUE_INDENT + piece)
        else:  # action
            for piece in textwrap.wrap(line, CONTENT_COLS) or [""]:
                emit(piece)
    return "\n".join(out).rstrip("\n") + "\n"


def render_html(fountain_text: str, title: str = "") -> str:
    """Standalone HTML: white-page screenplay look, Courier at 12pt.

    Uses .slug/.action/.character/.paren/.dialogue classes and the same
    line classification as :func:`render_text`.
    """
    title_page, items = classify_lines(fountain_text)
    esc = _html.escape
    page_title = (title or title_page.get("title") or "Screenplay").strip()

    body: list[str] = []
    tp_title = (title_page.get("title") or "").strip()
    if tp_title:
        author = (title_page.get("author")
                  or title_page.get("authors") or "").strip()
        body.append('<div class="title-page">')
        body.append(f'<p class="title">{esc(tp_title.upper())}</p>')
        if author:
            body.append('<p class="byline">by</p>')
            body.append(f'<p class="author">{esc(author)}</p>')
        body.append("</div>")

    class_for = {"slug": "slug", "character": "character",
                 "paren": "paren", "dialogue": "dialogue"}
    for kind, line in items:
        if kind == "blank":
            continue
        cls = class_for.get(kind, "action")
        text = line.upper() if kind in ("slug", "character") else line
        body.append(f'<p class="{cls}">{esc(text)}</p>')

    joined = "\n".join(body)
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{esc(page_title)}</title>\n"
        f"<style>\n{_CSS}\n</style>\n</head>\n<body>\n"
        f"<div class=\"page\">\n{joined}\n</div>\n</body>\n</html>\n"
    )
