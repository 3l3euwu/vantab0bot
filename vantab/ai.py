"""The AI brain behind ``~{question}``.

Vantab speaks the OpenAI *chat completions* protocol, which every serious
provider (OpenAI, OpenRouter, Groq, Together, Mistral, DeepInfra, Ollama,
LM Studio, vLLM…) exposes. That means the bot is not tied to one vendor: change
``AI_BASE_URL``/``AI_MODEL`` and it keeps working.

Design notes
------------
* No content filtering is added by the bot. Whatever the user asks is passed
  through unchanged; only the system persona is prepended.
* Streaming is supported (SSE) and degrades gracefully to a single request if
  the provider does not stream.
* Rate limits and transient 5xx errors are retried with exponential backoff.
* When a model is saturated (``503``, ``429`` …) the client rotates to the next
  model of ``ai_fallback_models`` for a cooling-off period instead of hammering
  the overloaded one.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from dataclasses import dataclass
from time import monotonic
from typing import Any

import aiohttp

from .config import Settings

__all__ = ["AIClient", "AIError", "ChatMessage", "build_messages", "extract_question"]

log = logging.getLogger("vantab.ai")

ChatMessage = dict[str, Any]

#: Models that reject ``max_tokens`` / ``temperature`` in favour of newer params.
_REASONING_PREFIXES = ("o1", "o3", "o4", "gpt-5", "gpt5")

#: Statuses worth retrying, and the subset that means "this model is unavailable".
RETRYABLE_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})
FALLBACK_STATUSES = frozenset({429, 500, 502, 503, 504, 529})

#: How long we stay on a fallback model before trying the primary one again.
FALLBACK_COOLDOWN_SECONDS = 300.0


class AIError(RuntimeError):
    """Raised for any unrecoverable AI backend failure."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _is_reasoning_model(model: str) -> bool:
    base = model.lower().split("/")[-1]
    return base.startswith(_REASONING_PREFIXES)


def build_messages(
    system_prompt: str,
    history: Iterable[tuple[str, str]],
    user_content: str,
    *,
    max_messages: int = 24,
    max_chars: int = 48000,
    extra_system: str | None = None,
) -> list[ChatMessage]:
    """Assemble the request payload messages with size limits applied.

    Keeps the newest messages; always keeps the system prompt and the newest user
    message. ``history`` is an iterable of ``(role, content)`` oldest-first.
    """
    trimmed: list[tuple[str, str]] = []
    total = 0
    for role, content in reversed(list(history)):
        if role not in {"user", "assistant"} or not content:
            continue
        if len(trimmed) >= max(0, max_messages):
            break
        total += len(content)
        if total > max_chars and trimmed:
            break
        trimmed.append((role, content))
    trimmed.reverse()

    system = system_prompt.strip()
    if extra_system:
        system = f"{system}\n\n{extra_system.strip()}" if system else extra_system.strip()

    messages: list[ChatMessage] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.extend({"role": role, "content": content} for role, content in trimmed)
    messages.append({"role": "user", "content": user_content})
    return messages


def extract_question(content: str, prefix: str, known_commands: Iterable[str]) -> str | None:
    """Return the AI question contained in a message, or ``None``.

    Supported shapes (all optional-friendly):

    * ``~{what is a quasar}``  -> ``what is a quasar``
    * ``~ what is a quasar``   -> ``what is a quasar``
    * ``~what is a quasar``    -> ``what is a quasar`` (only if ``what`` is not a command)
    * ``~ai what is a quasar`` -> handled by the AI command, not here
    """
    text = (content or "").strip()
    if not text.startswith(prefix):
        return None
    rest = text[len(prefix) :]
    if not rest:
        return None

    if rest.startswith("{"):
        end = rest.find("}")
        if end == -1:
            return None
        question = rest[1:end].strip()
        trailing = rest[end + 1 :].strip()
        if trailing:
            question = f"{question} {trailing}".strip()
        return question or None

    if rest[0].isspace():
        return rest.strip() or None

    first_word = rest.split(maxsplit=1)[0].strip().lower()
    if first_word in {name.lower() for name in known_commands}:
        return None
    return rest.strip() or None


@dataclass(slots=True)
class AIClient:
    settings: Settings
    session: aiohttp.ClientSession
    max_retries: int = 2
    model_index: int = 0
    _fallback_until: float | None = None

    # ------------------------------------------------------------------ models
    @property
    def models(self) -> tuple[str, ...]:
        """Primary model followed by the configured fallbacks (deduplicated)."""
        chain = [self.settings.ai_model, *self.settings.ai_fallback_models]
        seen: set[str] = set()
        ordered: list[str] = []
        for name in chain:
            candidate = (name or "").strip()
            if candidate and candidate.lower() not in seen:
                seen.add(candidate.lower())
                ordered.append(candidate)
        return tuple(ordered) or ("",)

    @property
    def model(self) -> str:
        """Model used for the next request (falls back to the primary after a cooldown)."""
        chain = self.models
        if self._fallback_until is not None and monotonic() >= self._fallback_until:
            if self.model_index:
                log.info("AI: cooldown elapsed — retrying primary model %s", chain[0])
            self.model_index = 0
            self._fallback_until = None
        return chain[min(self.model_index, len(chain) - 1)]

    def switch_model(self, reason: str = "") -> str | None:
        """Move to the next model in the chain; ``None`` when it is exhausted."""
        chain = self.models
        if self.model_index + 1 >= len(chain):
            return None
        previous = chain[self.model_index]
        self.model_index += 1
        self._fallback_until = monotonic() + FALLBACK_COOLDOWN_SECONDS
        log.warning(
            "AI: %s is unavailable (%s) — switching to %s for %d min",
            previous,
            reason or "overloaded",
            chain[self.model_index],
            int(FALLBACK_COOLDOWN_SECONDS // 60),
        )
        return chain[self.model_index]

    @property
    def endpoint(self) -> str:
        return f"{self.settings.ai_base_url}/chat/completions"

    def headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.settings.ai_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Vantab/1.0 (+https://github.com/3l3euwu/vantab0bot)",
        }
        headers.update(self.settings.ai_extra_headers)
        return headers

    def build_payload(
        self,
        messages: Sequence[ChatMessage],
        *,
        stream: bool | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        model = self.model
        payload: dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "stream": self.settings.ai_stream if stream is None else stream,
        }
        if not _is_reasoning_model(model):
            payload["temperature"] = (
                self.settings.ai_temperature if temperature is None else temperature
            )
            tokens = self.settings.ai_max_tokens if max_tokens is None else max_tokens
            if tokens > 0:
                payload["max_tokens"] = tokens
        else:
            tokens = self.settings.ai_max_tokens if max_tokens is None else max_tokens
            if tokens > 0:
                payload["max_completion_tokens"] = tokens
        if extra:
            payload.update(extra)
        return payload

    async def _request(
        self, payload: dict[str, Any], *, stream: bool
    ) -> aiohttp.ClientResponse:
        """POST with retries **and** model fallback; returns an open response."""
        delay = 1.5
        attempt = 0
        switches = 0
        while True:
            model = self.model
            body = {**payload, "model": model}
            try:
                response = await self.session.post(
                    self.endpoint,
                    json=body,
                    headers=self.headers(),
                    timeout=aiohttp.ClientTimeout(total=self.settings.ai_timeout),
                )
            except (TimeoutError, aiohttp.ClientError) as exc:
                if attempt >= self.max_retries:
                    raise AIError(f"Could not reach the AI backend: {exc}") from exc
                attempt += 1
                log.warning("AI request failed (%s), retrying in %.1fs", exc, delay)
                await asyncio.sleep(delay)
                delay *= 2
                continue

            if response.status in {200, 201}:
                return response

            body_text = await response.text()
            response.release()

            # A saturated model will not recover in two seconds: move on.
            if response.status in FALLBACK_STATUSES and switches < len(self.models) - 1:
                new_model = self.switch_model(f"HTTP {response.status}")
                if new_model is not None:
                    switches += 1
                    attempt = 0
                    delay = 1.5
                    log.warning(
                        "AI backend %s for %s — retrying with %s", response.status, model, new_model
                    )
                    continue

            if response.status in RETRYABLE_STATUSES and attempt < self.max_retries:
                retry_after = response.headers.get("Retry-After")
                wait = delay
                if retry_after and retry_after.replace(".", "", 1).isdigit():
                    wait = min(float(retry_after), 20.0)
                attempt += 1
                log.warning("AI backend %s (%s), retrying in %.1fs", response.status, body_text[:200], wait)
                await asyncio.sleep(wait)
                delay *= 2
                continue

            raise AIError(_describe_error(response.status, body_text), status=response.status)

    async def complete(self, messages: Sequence[ChatMessage], **kwargs: Any) -> str:
        """Non-streaming completion."""
        payload = self.build_payload(messages, stream=False, **kwargs)
        response = await self._request(payload, stream=False)
        try:
            data = await response.json(content_type=None)
        except (json.JSONDecodeError, aiohttp.ContentTypeError) as exc:
            raw = await response.text()
            raise AIError(f"Invalid JSON from AI backend: {raw[:300]}") from exc
        finally:
            response.release()
        return _extract_content(data)

    async def stream(self, messages: Sequence[ChatMessage], **kwargs: Any) -> AsyncIterator[str]:
        """Yield text deltas. Falls back to a single chunk when unsupported."""
        payload = self.build_payload(messages, stream=True, **kwargs)
        response = await self._request(payload, stream=True)
        try:
            content_type = response.headers.get("Content-Type", "")
            if "text/event-stream" not in content_type:
                # Provider ignored ``stream`` and sent plain JSON.
                data = await response.json(content_type=None)
                text = _extract_content(data)
                if text:
                    yield text
                return

            buffer = ""
            async for raw_chunk in response.content.iter_any():
                buffer += raw_chunk.decode("utf-8", errors="ignore")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line or line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        return
                    try:
                        event = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    if error := event.get("error"):
                        raise AIError(str(error))
                    for choice in event.get("choices", []):
                        delta = choice.get("delta") or {}
                        piece = delta.get("content")
                        if piece:
                            yield piece
                        # Some providers put the final text in message.content.
                        if not piece:
                            message = choice.get("message") or {}
                            if message.get("content") and choice.get("finish_reason"):
                                yield message["content"]
        finally:
            response.release()

    async def test(self) -> str:
        """Cheap connectivity check used by ``~ai status``."""
        return await self.complete(
            [{"role": "user", "content": "Reply with exactly: OK"}], max_tokens=16, temperature=0
        )


def _extract_content(data: Mapping[str, Any]) -> str:
    if not isinstance(data, Mapping):
        raise AIError("Unexpected AI response shape")
    if error := data.get("error"):
        detail = error.get("message") if isinstance(error, Mapping) else str(error)
        raise AIError(f"AI backend error: {detail}")
    choices = data.get("choices") or []
    if not choices:
        raise AIError("AI backend returned no choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        # Some providers return content parts.
        content = "".join(part.get("text", "") for part in content if isinstance(part, Mapping))
    if not content:
        reasoning = message.get("reasoning_content") or choices[0].get("text")
        content = reasoning or ""
    return str(content).strip()


def _describe_error(status: int, body: str) -> str:
    detail = body.strip()
    try:
        parsed = json.loads(body)
        if isinstance(parsed, Mapping):
            error = parsed.get("error")
            if isinstance(error, Mapping):
                detail = str(error.get("message") or detail)
            elif error:
                detail = str(error)
            elif parsed.get("message"):
                detail = str(parsed["message"])
    except json.JSONDecodeError:
        pass
    detail = detail[:400] or "no details"
    hints = {
        401: "invalid API key",
        403: "key lacks access to this model",
        404: "model or base URL not found",
        429: "rate limited",
    }
    hint = hints.get(status)
    return f"AI backend returned {status}{f' ({hint})' if hint else ''}: {detail}"
