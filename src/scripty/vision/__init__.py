"""Vision module: shot taxonomy, prompt building, and pluggable providers."""
from __future__ import annotations

from scripty.vision.providers import AnthropicVision, MockVision, get_provider
from scripty.vision.taxonomy import (
    SHOT_SCALE_GUIDE,
    build_shot_prompt,
    parse_shot_json,
)

__all__ = [
    "SHOT_SCALE_GUIDE",
    "AnthropicVision",
    "MockVision",
    "build_shot_prompt",
    "get_provider",
    "parse_shot_json",
]
