"""Runtime configuration. Everything overridable via environment variables.

No secrets live here: the Anthropic SDK resolves credentials itself
(ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, or an `ant auth login` profile).
"""
from __future__ import annotations

import os
import re
from pathlib import Path


def home() -> Path:
    """Root data directory (~/.scripty by default)."""
    root = Path(os.environ.get("SCRIPTY_HOME", Path.home() / ".scripty"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def db_path() -> Path:
    """One global database → lessons transfer across films."""
    return home() / "scripty.db"


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "untitled"


def project_dir(slug: str) -> Path:
    d = home() / "projects" / slug
    for sub in ("keyframes", "audio", "artifacts", "compare"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


# ---- model + pipeline knobs ------------------------------------------------

VISION_MODEL = os.environ.get("SCRIPTY_VISION_MODEL", "claude-opus-4-8")
TEXT_MODEL = os.environ.get("SCRIPTY_TEXT_MODEL", "claude-opus-4-8")

SCENE_THRESHOLD = float(os.environ.get("SCRIPTY_SCENE_THRESHOLD", "0.30"))
FRAMES_PER_SHOT = int(os.environ.get("SCRIPTY_FRAMES_PER_SHOT", "3"))
MIN_SHOT_SECONDS = float(os.environ.get("SCRIPTY_MIN_SHOT_SECONDS", "0.40"))

RECALL_LESSONS = int(os.environ.get("SCRIPTY_RECALL_LESSONS", "8"))
RECALL_EXAMPLES = int(os.environ.get("SCRIPTY_RECALL_EXAMPLES", "5"))

SERVER_PORT = int(os.environ.get("SCRIPTY_PORT", "8787"))


def _anthropic_credentials_present() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    cfg = Path(os.environ.get("ANTHROPIC_CONFIG_DIR",
                              Path.home() / ".config" / "anthropic"))
    creds = cfg / "credentials"
    return creds.is_dir() and any(creds.iterdir())


def default_provider() -> str:
    """'anthropic' when credentials are resolvable, else 'mock'."""
    forced = os.environ.get("SCRIPTY_PROVIDER")
    if forced:
        return forced
    return "anthropic" if _anthropic_credentials_present() else "mock"


def default_transcriber() -> str:
    forced = os.environ.get("SCRIPTY_TRANSCRIBER")
    if forced:
        return forced
    try:
        import faster_whisper  # noqa: F401
        return "whisper"
    except ImportError:
        return "none"
