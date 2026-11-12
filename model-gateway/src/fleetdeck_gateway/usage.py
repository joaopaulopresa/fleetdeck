"""Reads token usage and cost out of a provider response without altering it."""

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Usage:
    """What a call consumed."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    output_text: str = ""
    tool_calls: list[str] = field(default_factory=list)

    def absorb(self, usage: dict[str, Any] | None) -> None:
        """Take the numbers from an OpenAI-style ``usage`` object."""
        if not usage:
            return
        self.input_tokens = int(usage.get("prompt_tokens") or 0)
        self.output_tokens = int(usage.get("completion_tokens") or 0)
        details = usage.get("prompt_tokens_details") or {}
        self.cached_tokens = int(details.get("cached_tokens") or 0)
        self.cost_usd = float(usage.get("cost") or 0.0)


class StreamUsageCollector:
    """Follows a server-sent event stream as it passes and keeps usage and the output text."""

    def __init__(self) -> None:
        self.usage = Usage()
        self._buffer = b""
        self._text: list[str] = []

    def feed(self, chunk: bytes) -> None:
        """Inspect one chunk of the stream."""
        self._buffer += chunk
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            self._line(line.strip())

    def _line(self, line: bytes) -> None:
        if not line.startswith(b"data:"):
            return
        payload = line[5:].strip()
        if not payload or payload == b"[DONE]":
            return
        try:
            data = json.loads(payload)
        except ValueError:
            return
        self.usage.absorb(data.get("usage"))
        for choice in data.get("choices") or []:
            delta = choice.get("delta") or {}
            if delta.get("content"):
                self._text.append(str(delta["content"]))
            for call in delta.get("tool_calls") or []:
                name = (call.get("function") or {}).get("name")
                if name:
                    self.usage.tool_calls.append(str(name))

    def finish(self) -> Usage:
        """Return what was collected."""
        self.usage.output_text = "".join(self._text)
        return self.usage


def usage_from_body(body: bytes) -> Usage:
    """Read usage and output from a complete, non-streaming response body."""
    usage = Usage()
    try:
        data = json.loads(body)
    except ValueError:
        return usage
    if not isinstance(data, dict):
        return usage
    usage.absorb(data.get("usage"))
    for choice in data.get("choices") or []:
        message = choice.get("message") or {}
        usage.output_text += str(message.get("content") or "")
        for call in message.get("tool_calls") or []:
            name = (call.get("function") or {}).get("name")
            if name:
                usage.tool_calls.append(str(name))
    return usage
