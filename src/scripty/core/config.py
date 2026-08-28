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
WRITER_MODEL = os.environ.get("SCRIPTY_WRITER_MODEL", "grok-4.6")
WRITER_REASONING_EFFORT = os.environ.get("SCRIPTY_WRITER_REASONING_EFFORT", "xhigh")
XAI_BASE_URL = os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1")

#: older Grok ids, plus Claude — never used for champion/challenger prose
_BLOCKED_WRITER_MODELS = frozenset({
    "grok-2", "grok-3", "grok-4", "grok-4.5",
})


def writer_model() -> str:
    """Live fiction model. Default grok-4.6; refuse older Grok / Claude ids."""
    raw = (os.environ.get("SCRIPTY_WRITER_MODEL") or "grok-4.6").strip()
    lowered = raw.lower()
    if (not raw or lowered in _BLOCKED_WRITER_MODELS
            or lowered.startswith("claude") or lowered.startswith("grok-2")
            or lowered.startswith("grok-3")):
        return "grok-4.6"
    return raw


def writer_reasoning_effort() -> str:
    """Always send an effort. Empty/unknown → xhigh, never the API's 'high'."""
    raw = (os.environ.get("SCRIPTY_WRITER_REASONING_EFFORT") or "xhigh").strip().lower()
    if raw not in ("low", "medium", "high", "xhigh"):
        return "xhigh"
    return raw

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


def _xai_credentials_present() -> bool:
    return bool(os.environ.get("XAI_API_KEY"))


def default_provider() -> str:
    """Film/vision default: Anthropic when credentials resolve, else mock.

    ``SCRIPTY_PROVIDER`` still forces a name. Writer prose uses
    ``default_writer_provider()`` so an xAI key does not break vision.
    """
    forced = os.environ.get("SCRIPTY_PROVIDER")
    if forced:
        return forced
    return "anthropic" if _anthropic_credentials_present() else "mock"


def default_writer_provider() -> str:
    """Champion/challenger prose: xAI when a key is present, else mock.

    Never Anthropic/Claude. ``SCRIPTY_PROVIDER=mock`` keeps pytest offline.
    """
    forced = (os.environ.get("SCRIPTY_PROVIDER") or "").strip().lower()
    if forced in ("mock", "none"):
        return "mock"
    if _xai_credentials_present() or forced in ("xai", "grok"):
        return "xai"
    return "mock"


def default_transcriber() -> str:
    forced = os.environ.get("SCRIPTY_TRANSCRIBER")
    if forced:
        return forced
    try:
        import faster_whisper  # noqa: F401
        return "whisper"
    except ImportError:
        return "none"
