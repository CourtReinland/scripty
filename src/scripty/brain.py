"""Text brains: xAI Grok, Anthropic, and a deterministic offline twin."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
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

    def complete(self, system: str, user: str, *,
                 temperature: float | None = None,
                 seed: int | None = None,
                 max_tokens: int | None = None) -> str:
        """One-shot completion; raises RuntimeError (with cause) on failure.

        The film/distill path (no temperature) keeps adaptive thinking and
        omits sampling knobs. The writer path passes temperature so two
        generates are not identical; seed is echoed in the user turn
        because the Messages API has no portable seed field.
        """
        client = self._client_or_create()
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": int(max_tokens or 2000),
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if temperature is None:
            payload["thinking"] = {"type": "adaptive"}
        else:
            payload["temperature"] = max(0.0, min(1.0, float(temperature)))
        if seed is not None:
            payload["messages"] = [{
                "role": "user",
                "content": f"[variation seed {int(seed)}]\n\n{user}",
            }]
        try:
            response = client.messages.create(**payload)
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


class XaiBrain:
    """Fiction drafts via the xAI Chat Completions API (grok-4.6 + xhigh)."""

    name = "xai"

    def __init__(self, model: str | None = None,
                 reasoning_effort: str | None = None,
                 api_key: str | None = None,
                 base_url: str | None = None):
        self.model = model or config.writer_model()
        self.reasoning_effort = reasoning_effort or config.writer_reasoning_effort()
        self.api_key = api_key if api_key is not None else os.environ.get("XAI_API_KEY", "")
        self.base_url = (base_url or config.XAI_BASE_URL).rstrip("/")

    def complete(self, system: str, user: str, *,
                 temperature: float | None = None,
                 seed: int | None = None,
                 max_tokens: int | None = None) -> str:
        if not self.api_key:
            raise RuntimeError("XAI_API_KEY is not set")
        content = user if seed is None else f"[variation seed {int(seed)}]\n\n{user}"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            "reasoning_effort": self.reasoning_effort,
        }
        if temperature is not None:
            payload["temperature"] = max(0.0, min(2.0, float(temperature)))
        if max_tokens is not None:
            payload["max_tokens"] = int(max_tokens)
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=3600) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise RuntimeError(
                f"xAI API error ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"xAI connection error: {exc}") from exc
        data = json.loads(raw)
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("xAI returned no choices")
        message = (choices[0] or {}).get("message") or {}
        return str(message.get("content") or "").strip()


class MockBrain:
    """Deterministic offline twin.

    Distill/polish: echoes the first sentences. Fiction drafts: a seed-
    driven story so two writer generates are not identical, without any
    copyrighted corpus or network.
    """

    name = "mock"

    def complete(self, system: str, user: str, *,
                 temperature: float | None = None,
                 seed: int | None = None,
                 max_tokens: int | None = None) -> str:
        from scripty.write.mock_prose import is_writer_prompt, mock_fiction

        if is_writer_prompt(system, user):
            return mock_fiction(system, user, seed=seed, temperature=temperature)
        text = " ".join(user.split()).strip()
        if not text:
            return ""
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        return " ".join(sentences[:2])[:400]


def get_brain(name: str | None = None) -> TextBrain:
    """Resolve a TextBrain by name; ``None`` follows writer-provider default."""
    resolved = (name or config.default_writer_provider()).strip().lower()
    if resolved in ("xai", "grok"):
        return XaiBrain()
    if resolved == "anthropic":
        return AnthropicBrain()
    if resolved in ("mock", "none", ""):
        return MockBrain()
    raise ValueError(f"unknown brain: {name!r}")
