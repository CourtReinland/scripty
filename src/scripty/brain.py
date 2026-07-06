"""Text brains: Anthropic-backed completion plus a deterministic offline twin.

Used for distilling lessons and polishing describe prompts. Both classes
satisfy the ``scripty.core.interfaces.TextBrain`` protocol.
"""
from __future__ import annotations

import re
from typing import Any

from scripty.core import config
from scripty.core.interfaces import TextBrain

try:  # the SDK is a hard dependency, but stay importable without it
    import anthropic
except ImportError:  # pragma: no cover
    anthropic = None  # type: ignore[assignment]


class AnthropicBrain:
    """Plain-text completion via the Anthropic Messages API.

    Model parameters follow the project contract: no temperature/top_p/top_k,
    adaptive thinking, model name from ``scripty.core.config``. Credentials
    are resolved by the SDK itself (``anthropic.Anthropic()`` with no args).
    """

    name = "anthropic"

    def __init__(self, model: str | None = None):
        if anthropic is None:  # pragma: no cover
            raise RuntimeError("the 'anthropic' SDK is not installed")
        self.model = model or config.TEXT_MODEL
        self._client: Any = None  # created lazily so tests stay offline

    def _client_or_create(self) -> Any:
        if self._client is None:
            self._client = anthropic.Anthropic()
        return self._client

    def complete(self, system: str, user: str) -> str:
        """One-shot completion; raises RuntimeError (with cause) on failure."""
        client = self._client_or_create()
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=2000,
                thinking={"type": "adaptive"},
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.APIStatusError as exc:
            raise RuntimeError(
                f"anthropic API error ({getattr(exc, 'status_code', '?')}): {exc}"
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise RuntimeError(f"anthropic connection error: {exc}") from exc
        if getattr(response, "stop_reason", None) == "refusal":
            raise RuntimeError("anthropic refused the completion request")
        parts = [
            block.text
            for block in getattr(response, "content", [])
            if getattr(block, "type", "") == "text"
        ]
        return "".join(parts).strip()


class MockBrain:
    """Deterministic offline twin: summarizes by echoing the first sentences."""

    name = "mock"

    def complete(self, system: str, user: str) -> str:
        text = " ".join(user.split()).strip()
        if not text:
            return ""
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        return " ".join(sentences[:2])[:400]


def get_brain(name: str | None = None) -> TextBrain:
    """Resolve a TextBrain by name; ``None`` follows config.default_provider()."""
    resolved = (name or config.default_provider()).strip().lower()
    if resolved == "anthropic":
        return AnthropicBrain()
    if resolved in ("mock", "none", ""):
        return MockBrain()
    raise ValueError(f"unknown brain: {name!r}")
