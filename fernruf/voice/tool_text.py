"""Keep tool-call syntax out of anything that can be spoken.

Models sometimes emit ``end_call(...)``, a JSON tool blob, or ``<tool_call>`` as
plain assistant text. That text must never reach TTS. A textual ``end_call``
becomes a real tool call; every other tool-shaped span is dropped.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_FUNC_NAMES = ("end_call", "send_dtmf", "mark_answer")
_HOLD_MARKERS = (
    "end_call",
    "send_dtmf",
    "mark_answer",
    "<tool_call",
    "<function_call",
    "<tool>",
    "<function>",
    "tool_call",
    "function_call",
)


@dataclass(frozen=True)
class TextualEndCall:
    reason: str
    goodbye: str


def _is_boundary(text: str, index: int) -> bool:
    if index <= 0:
        return True
    prev = text[index - 1]
    return not (prev.isalnum() or prev == "_")


def _hold_len(text: str) -> int:
    """Length of a suffix that can still grow into a tool marker."""
    if not text:
        return 0
    lower = text.lower()
    best = 0
    limit = min(len(text), 20)
    for n in range(1, limit + 1):
        start = len(text) - n
        if not _is_boundary(text, start):
            continue
        suf = lower[start:]
        for marker in _HOLD_MARKERS:
            if marker.startswith(suf) and len(suf) < len(marker):
                if suf[:1].isalpha() and n < 3:
                    continue
                best = max(best, n)
        if suf in {"{", "<"}:
            best = max(best, 1)
    return best


def _skip_string(text: str, i: int) -> int:
    quote = text[i]
    i += 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == quote:
            return i + 1
        i += 1
    return len(text)


def _match_balanced(text: str, start: int, open_ch: str, close_ch: str) -> int | None:
    """Index after the closer matching ``text[start]``, or None if it stays open."""
    if start >= len(text) or text[start] != open_ch:
        return None
    depth = 0
    i = start
    while i < len(text):
        ch = text[i]
        if ch in "\"'":
            i = _skip_string(text, i)
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def _looks_like_tool_json(blob: str) -> bool:
    if not any(name in blob.lower() for name in _FUNC_NAMES):
        return False
    return bool(
        re.search(r'"(?:name|function|tool)"\s*:', blob, re.IGNORECASE)
        or re.search(r'"(?:end_call|send_dtmf|mark_answer)"', blob, re.IGNORECASE)
    )


def _maybe_tool_json_prefix(fragment: str) -> bool:
    body = fragment[1:].lstrip() if fragment.startswith("{") else fragment.lstrip()
    if not body:
        return True
    if body[0] not in "\"{":
        return False
    low = body.lower()
    probes = (
        '"name"',
        '"function"',
        '"tool"',
        '"arguments"',
        '"parameters"',
        '"end_call"',
        '"send_dtmf"',
        '"mark_answer"',
        "end_call",
        "send_dtmf",
        "mark_answer",
    )
    if any(probe in low for probe in probes):
        return True
    return len(body) < 24


_KW = re.compile(
    r"""(?ix)
    \b(reason|goodbye)\s*=\s*
    (?:
        "((?:\\.|[^"\\])*)"
      | '((?:\\.|[^'\\])*)'
      | ([^,)\}]*)
    )
    """
)


def _kw_args(body: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for match in _KW.finditer(body):
        key = match.group(1).lower()
        raw = next(group for group in match.groups()[1:] if group is not None)
        found[key] = raw.strip()
    return found


def _end_call_from_mapping(data: dict) -> TextualEndCall | None:
    name = str(data.get("name") or data.get("tool") or "")
    function = data.get("function")
    args: object = data.get("arguments", data.get("parameters", data))
    if isinstance(function, dict):
        name = str(function.get("name") or name)
        args = function.get("arguments", args)
    if name.lower() != "end_call":
        if "goodbye" not in data and "reason" not in data:
            return None
        if name and name.lower() != "end_call":
            return None
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = _kw_args(args)
    if not isinstance(args, dict):
        args = {}
    reason = str(args.get("reason") or data.get("reason") or "end_call").strip() or "end_call"
    goodbye = str(args.get("goodbye") or data.get("goodbye") or "").strip()
    return TextualEndCall(reason=reason, goodbye=goodbye)


def _parse_call_body(name: str, body: str) -> TextualEndCall | None:
    if name.lower() != "end_call":
        return None
    body = body.strip()
    if body.startswith("{"):
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            parsed = _end_call_from_mapping({"name": "end_call", **data})
            if parsed is not None:
                return parsed
    kwargs = _kw_args(body)
    if kwargs:
        return TextualEndCall(
            reason=(kwargs.get("reason") or "end_call").strip() or "end_call",
            goodbye=(kwargs.get("goodbye") or "").strip(),
        )
    if body:
        return TextualEndCall(reason=body.strip(" \"'"), goodbye="")
    return TextualEndCall(reason="end_call", goodbye="")


def _parse_tool_json(blob: str) -> TextualEndCall | None:
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return _end_call_from_mapping(data)


def _func_at(text: str, pos: int) -> tuple[int, int | None, TextualEndCall | None] | None:
    match = re.match(r"(?i)(end_call|send_dtmf|mark_answer)\b", text[pos:])
    if match is None or not _is_boundary(text, pos):
        return None
    after = text[pos + match.end() :]
    call_match = re.match(r"\s*\(", after)
    if call_match is None:
        if after.strip() == "":
            return (pos, None, None)
        return (pos, pos + match.end(), None)
    open_at = pos + match.end() + call_match.end() - 1
    end = _match_balanced(text, open_at, "(", ")")
    if end is None:
        return (pos, None, None)
    body = text[open_at + 1 : end - 1]
    return (pos, end, _parse_call_body(match.group(1), body))


def _tag_at(text: str, pos: int) -> tuple[int, int | None, TextualEndCall | None] | None:
    match = re.match(r"(?i)<(?:tool_call|function_call)\b[^>]*>", text[pos:])
    if match is None:
        return None
    close = re.search(r"(?i)</(?:tool_call|function_call)>", text[pos + match.end() :])
    if close is None:
        return (pos, None, None)
    end = pos + match.end() + close.start()
    inner = text[pos + match.end() : end]
    region_end = pos + match.end() + close.end()
    call = _first_end_call(inner)
    return (pos, region_end, call)


def _json_at(text: str, pos: int) -> tuple[int, int | None, TextualEndCall | None] | None:
    if text[pos] != "{":
        return None
    end = _match_balanced(text, pos, "{", "}")
    if end is None:
        if _maybe_tool_json_prefix(text[pos:]):
            return (pos, None, None)
        return None
    blob = text[pos:end]
    if not _looks_like_tool_json(blob):
        return None
    return (pos, end, _parse_tool_json(blob))


def _first_region(
    text: str,
) -> tuple[int, int | None, TextualEndCall | None] | None:
    """Earliest tool-shaped span.

    The middle value is None when the span is still incomplete and must be held.
    """
    best: tuple[int, int | None, TextualEndCall | None] | None = None
    i = 0
    while i < len(text):
        found: tuple[int, int | None, TextualEndCall | None] | None = None
        lower = text[i].lower()
        if lower in {name[0] for name in _FUNC_NAMES}:
            found = _func_at(text, i)
        elif text[i] == "<":
            found = _tag_at(text, i)
        elif text[i] == "{":
            found = _json_at(text, i)
        if found is not None and (best is None or found[0] < best[0]):
            best = found
            if found[0] == 0 or found[1] is None:
                # Still scan for an earlier start only; this is the leftmost.
                break
        i += 1
    return best


def _first_end_call(text: str) -> TextualEndCall | None:
    remaining = text
    while remaining:
        found = _first_region(remaining)
        if found is None or found[1] is None:
            return None
        _start, end, call = found
        if call is not None:
            return call
        remaining = remaining[end:]
    return None


def _tidy(text: str) -> str:
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" +([,.;:!?])", r"\1", text)
    return text


class ToolTextGate:
    """Split a streaming assistant turn into speech and textual end_call calls."""

    def __init__(self) -> None:
        self._buf = ""
        self.calls: list[TextualEndCall] = []

    def feed(self, chunk: str) -> str:
        if not chunk:
            return ""
        self._buf += chunk
        return self._drain(final=False)

    def finish(self) -> str:
        spoken = self._drain(final=True)
        self._buf = ""
        return spoken

    def _drain(self, *, final: bool) -> str:
        out: list[str] = []
        removed = False
        while self._buf:
            found = _first_region(self._buf)
            if found is None:
                if final:
                    hold = _hold_len(self._buf)
                    if hold:
                        removed = True
                        out.append(self._buf[:-hold])
                    else:
                        out.append(self._buf)
                    self._buf = ""
                else:
                    hold = _hold_len(self._buf)
                    if hold:
                        out.append(self._buf[:-hold])
                        self._buf = self._buf[-hold:]
                    else:
                        out.append(self._buf)
                        self._buf = ""
                break
            start, end, call = found
            if end is None:
                if start:
                    out.append(self._buf[:start])
                    self._buf = self._buf[start:]
                if final:
                    removed = True
                    self._buf = ""
                break
            if start:
                out.append(self._buf[:start])
            if call is not None and not self.calls:
                self.calls.append(call)
            removed = True
            self._buf = self._buf[end:]
        text = "".join(out)
        if removed:
            text = _tidy(text)
        return text


def speakable_only(text: str) -> str:
    """Return ``text`` with every tool-shaped span removed."""
    gate = ToolTextGate()
    spoken = gate.feed(text)
    spoken += gate.finish()
    return _tidy(spoken).strip()
