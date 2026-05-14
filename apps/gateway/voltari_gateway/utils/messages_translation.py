"""Translate between Anthropic Messages API shape and OpenAI Chat Completions shape.

Used by ``/v1/messages`` to support smart-routing across providers without
forcing the Claude Code SDK / Anthropic-shaped clients to switch SDK. The
translation goes in BOTH directions:

* **Inbound** (Anthropic-shape → OpenAI-shape): when the routed primary is
  not Anthropic (e.g. DeepSeek V4 Pro on failover from Sonnet), we cannot
  pass the body straight through. We rebuild it in OpenAI shape so every
  non-Anthropic adapter (which already speaks OpenAI dialect internally)
  can serve it.

* **Outbound** (OpenAI-shape → Anthropic-shape): the response we hand back
  to the client must always look like an Anthropic ``Message``. So when
  the call went through a non-Anthropic provider, we re-shape the
  ``chat.completion`` envelope into ``{type: "message", role: "assistant",
  content: [{type: "text", text: ...}], stop_reason: ...}``.

* **Streaming** (OpenAI chunks → Anthropic SSE events): translate each
  ``chat.completion.chunk`` to an Anthropic event sequence:
  ``message_start`` → ``content_block_start`` → N× ``content_block_delta``
  → ``content_block_stop`` → ``message_delta`` → ``message_stop``.

Trade-offs:

* We do not faithfully translate **all** Anthropic features cross-provider
  (e.g. ``thinking`` blocks, Anthropic's ``citations``, computer-use).
  Those are Anthropic-specific and silently dropped when routing to
  another provider — the alternative is rejecting the request entirely,
  which would break the failover-to-DeepSeek case the whole feature exists
  for. We log when we drop fields so observability sees it.
* Tool-use translation is bidirectional and lossless (Anthropic
  ``tool_use`` ↔ OpenAI ``tool_calls`` ↔ Anthropic ``tool_result``).
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Anthropic-shape → OpenAI-shape  (request translation, used when routing
# an Anthropic-format /v1/messages request to a non-Anthropic provider)
# ---------------------------------------------------------------------------


def anthropic_to_openai_messages(
    *,
    system: str | list[dict[str, Any]] | None,
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert Anthropic ``system`` + ``messages`` into OpenAI-shape messages.

    Anthropic uses a separate top-level ``system`` parameter (string OR a
    list of content blocks with ``cache_control``) plus ``messages`` of
    role ∈ {user, assistant} only. OpenAI puts everything in ``messages``
    with role ∈ {system, user, assistant, tool}.

    Translation rules:

    * ``system`` (string) → leading ``{role: "system", content: <str>}``.
    * ``system`` (block list) → concatenated text → leading system
      message. ``cache_control`` is dropped — non-Anthropic providers
      have no equivalent (OpenAI/DeepSeek do automatic caching).
    * Each Anthropic message:
      - text-only string content → forwarded as-is.
      - block list:
        * ``text`` → flattened into a single string (concatenated).
        * ``tool_use`` → emitted as an assistant message with
          ``tool_calls``. We re-serialise ``input`` to JSON because OpenAI
          ``arguments`` is a JSON-encoded string.
        * ``tool_result`` → emitted as a separate ``role: "tool"`` message
          with ``tool_call_id`` from ``tool_use_id``.
        * ``image`` → translated to OpenAI vision shape
          (``{type: "image_url", image_url: {url: ...}}``).
    """
    out: list[dict[str, Any]] = []

    # System block first.
    if system is not None:
        if isinstance(system, str):
            if system.strip():
                out.append({"role": "system", "content": system})
        elif isinstance(system, list):
            sys_text_parts: list[str] = []
            for block in system:
                if isinstance(block, dict) and block.get("type") == "text":
                    txt = block.get("text", "")
                    if isinstance(txt, str) and txt:
                        sys_text_parts.append(txt)
            if sys_text_parts:
                out.append({"role": "system", "content": "\n\n".join(sys_text_parts)})

    # Then conversation messages.
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if role not in ("user", "assistant"):
            # Anthropic spec only allows user/assistant — defensive skip.
            continue

        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        if not isinstance(content, list):
            # Defensive: empty/null content. Skip silently — OpenAI rejects
            # role=user with content=null but we'd already 4xx upstream.
            continue

        # Block list — split into text, tool_use (assistant), tool_result (user).
        text_parts: list[str] = []
        image_blocks: list[dict[str, Any]] = []
        tool_calls: list[dict[str, Any]] = []
        tool_results: list[tuple[str, str]] = []  # (tool_use_id, result_text)

        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                txt = block.get("text", "")
                if isinstance(txt, str) and txt:
                    text_parts.append(txt)
            elif btype == "image":
                # Anthropic image source → OpenAI ``image_url`` shape.
                # Anthropic accepts {type: "base64", media_type, data} or
                # {type: "url", url}. We map both — OpenAI accepts a
                # data-URL string directly.
                source = block.get("source") or {}
                if source.get("type") == "url":
                    url = source.get("url")
                    if isinstance(url, str):
                        image_blocks.append({"type": "image_url", "image_url": {"url": url}})
                elif source.get("type") == "base64":
                    media_type = source.get("media_type", "image/jpeg")
                    data = source.get("data", "")
                    if isinstance(data, str) and data:
                        image_blocks.append(
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{media_type};base64,{data}"},
                            }
                        )
            elif btype == "tool_use" and role == "assistant":
                tu_id = block.get("id", "")
                tu_name = block.get("name", "")
                tu_input = block.get("input", {})
                tool_calls.append(
                    {
                        "id": str(tu_id),
                        "type": "function",
                        "function": {
                            "name": str(tu_name),
                            "arguments": json.dumps(tu_input or {}, ensure_ascii=False),
                        },
                    }
                )
            elif btype == "tool_result" and role == "user":
                tu_id = block.get("tool_use_id", "")
                inner = block.get("content")
                if isinstance(inner, str):
                    result_text = inner
                elif isinstance(inner, list):
                    # Result content can itself be a block list (text only,
                    # per Anthropic spec). Concatenate text blocks.
                    parts: list[str] = []
                    for ib in inner:
                        if isinstance(ib, dict) and ib.get("type") == "text":
                            t = ib.get("text", "")
                            if isinstance(t, str):
                                parts.append(t)
                    result_text = "".join(parts)
                else:
                    result_text = json.dumps(inner, ensure_ascii=False)
                tool_results.append((str(tu_id), result_text))
            else:
                # Unknown block type (e.g. thinking, citations). Drop with a
                # log line so we can spot abuse in observability.
                log.debug("anth_to_oai_dropped_block", block_type=btype)

        # Compose the OpenAI message(s) from the parsed buckets.
        if role == "assistant":
            msg_out: dict[str, Any] = {"role": "assistant"}
            text_combined = "".join(text_parts)
            if text_combined:
                msg_out["content"] = text_combined
            else:
                # OpenAI requires content OR tool_calls. None is fine when
                # tool_calls is set.
                msg_out["content"] = None
            if tool_calls:
                msg_out["tool_calls"] = tool_calls
            # Skip empty assistant messages (no text, no tools).
            if msg_out["content"] is None and not tool_calls:
                continue
            out.append(msg_out)
        else:  # role == "user"
            # Text + images go in one user message; tool_results in separate
            # role=tool messages (one per tool_use_id) per OpenAI spec.
            user_content: str | list[dict[str, Any]]
            if image_blocks:
                # OpenAI multimodal user content is a list of blocks.
                blocks: list[dict[str, Any]] = []
                if text_parts:
                    blocks.append({"type": "text", "text": "".join(text_parts)})
                blocks.extend(image_blocks)
                user_content = blocks
                if user_content:
                    out.append({"role": "user", "content": user_content})
            elif text_parts:
                out.append({"role": "user", "content": "".join(text_parts)})

            # Tool results AFTER the user message containing them. OpenAI
            # protocol: each tool result is its own role=tool message.
            for tu_id, result_text in tool_results:
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": tu_id,
                        "content": result_text,
                    }
                )

    return out


def anthropic_tools_to_openai(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Anthropic ``tools`` array to OpenAI ``tools`` array.

    Anthropic shape:
        {"name": "...", "description": "...", "input_schema": {...}}
    OpenAI shape:
        {"type": "function", "function": {"name": "...", "description": "...",
                                          "parameters": {...}}}

    ``cache_control`` on tool definitions is dropped (Anthropic-only).
    """
    out: list[dict[str, Any]] = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        out.append(
            {
                "type": "function",
                "function": {
                    "name": t.get("name", ""),
                    "description": t.get("description", "") or "",
                    "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
                },
            }
        )
    return out


def anthropic_tool_choice_to_openai(tool_choice: Any) -> Any:
    """Convert Anthropic ``tool_choice`` to OpenAI shape.

    Anthropic:
        {"type": "auto"} | {"type": "any"} | {"type": "tool", "name": "X"} |
        {"type": "none"}  (rare; usually omitted to mean "auto")
    OpenAI:
        "auto" | "required" | {"type": "function", "function": {"name": "X"}} |
        "none"
    """
    if not isinstance(tool_choice, dict):
        return tool_choice  # already a string or None
    t = tool_choice.get("type")
    if t == "auto":
        return "auto"
    if t == "any":
        return "required"
    if t == "none":
        return "none"
    if t == "tool":
        return {
            "type": "function",
            "function": {"name": tool_choice.get("name", "")},
        }
    return "auto"


# ---------------------------------------------------------------------------
# OpenAI-shape → Anthropic-shape  (response translation, used when the
# /v1/messages call routed to a non-Anthropic provider)
# ---------------------------------------------------------------------------


def _openai_finish_to_anthropic_stop(finish_reason: str | None) -> str:
    """Map OpenAI finish_reason → Anthropic stop_reason."""
    return {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "content_filter": "end_turn",  # closest analog
        "function_call": "tool_use",  # legacy OpenAI
    }.get(finish_reason or "", "end_turn")


def openai_response_to_anthropic_message(
    openai_envelope: dict[str, Any],
    *,
    model_id: str,
) -> dict[str, Any]:
    """Re-shape an OpenAI ``chat.completion`` envelope into an Anthropic ``Message``.

    ``openai_envelope`` is the raw upstream response (already normalised
    to OpenAI shape by every non-Anthropic provider adapter). ``model_id``
    is the public Brikko model id we expose to clients (so the Anthropic
    response carries our id, not the upstream's).
    """
    choices = openai_envelope.get("choices") or []
    choice0 = choices[0] if choices else {}
    msg = choice0.get("message") or {}
    finish_reason = choice0.get("finish_reason")

    # Build content blocks.
    content_blocks: list[dict[str, Any]] = []
    text = msg.get("content")
    if isinstance(text, str) and text:
        content_blocks.append({"type": "text", "text": text})
    elif isinstance(text, list):
        # Vision-style blocks; concatenate text blocks only (rare on
        # response side — most providers respond text-only).
        joined: list[str] = []
        for b in text:
            if isinstance(b, dict) and b.get("type") == "text":
                t = b.get("text", "")
                if isinstance(t, str):
                    joined.append(t)
        if joined:
            content_blocks.append({"type": "text", "text": "".join(joined)})

    tool_calls = msg.get("tool_calls") or []
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        # OpenAI emits ``arguments`` as a JSON string; Anthropic ``input``
        # is the parsed object.
        raw_args = fn.get("arguments", "")
        try:
            parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError:
            parsed_args = {}
        content_blocks.append(
            {
                "type": "tool_use",
                "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:24]}",
                "name": (fn.get("name") or ""),
                "input": parsed_args or {},
            }
        )

    # Empty content is invalid in Anthropic shape — synthesise one empty
    # text block so SDK callers don't crash on iteration.
    if not content_blocks:
        content_blocks.append({"type": "text", "text": ""})

    usage = openai_envelope.get("usage") or {}
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    input_tokens = int(usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or 0)

    return {
        "id": openai_envelope.get("id") or f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": model_id,
        "content": content_blocks,
        "stop_reason": _openai_finish_to_anthropic_stop(finish_reason),
        "stop_sequence": None,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            # Anthropic separates cache_creation vs cache_read; we only
            # have the read side from non-Anthropic providers (their
            # implicit caches don't surface a "creation" count).
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": int(cached),
        },
    }


# ---------------------------------------------------------------------------
# OpenAI streaming chunks → Anthropic SSE events
# ---------------------------------------------------------------------------
#
# Anthropic SSE format (one HTTP body):
#     event: message_start
#     data: {"type":"message_start","message":{...}}
#
#     event: content_block_start
#     data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}
#
#     event: content_block_delta
#     data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"..."}}
#
#     event: content_block_stop
#     data: {"type":"content_block_stop","index":0}
#
#     event: message_delta
#     data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":N}}
#
#     event: message_stop
#     data: {"type":"message_stop"}


def _sse(event: str, data: dict[str, Any]) -> bytes:
    """Render a single Anthropic SSE event."""
    return (f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n").encode()


async def openai_stream_to_anthropic_events(
    openai_chunks: AsyncIterator[bytes],
    *,
    model_id: str,
) -> AsyncIterator[bytes]:
    """Async generator: rewrap an OpenAI SSE stream as Anthropic SSE events.

    ``openai_chunks`` yields raw bytes (one SSE chunk at a time, ``data: ...\\n\\n``).
    We parse each chunk, accumulate text deltas + tool-call deltas, and emit
    Anthropic events. The final ``message_stop`` carries the aggregate usage.

    We deliberately collapse all OpenAI chunks into ONE Anthropic content
    block (index 0) when only text is present; tool calls each get their
    own block index. This matches what Anthropic's own stream looks like
    for short-to-medium responses.
    """
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    created = int(time.time())  # noqa: F841 — kept for parity with OpenAI logs
    text_block_started = False
    text_block_index = 0
    tool_blocks: dict[int, dict[str, Any]] = {}  # tcall_index → {id, name, args_str}
    next_block_index = 0
    finish_reason: str | None = None
    usage_input = 0
    usage_output = 0
    usage_cached = 0

    # Emit message_start eagerly. We don't know input_tokens yet; use 0 and
    # update via message_delta.usage at the end.
    yield _sse(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "model": model_id,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
            },
        },
    )

    async for raw in openai_chunks:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        for line in text.splitlines():
            if not line.startswith("data: "):
                continue
            payload_str = line[len("data: ") :].strip()
            if payload_str == "[DONE]":
                continue
            try:
                payload = json.loads(payload_str)
            except json.JSONDecodeError:
                continue

            # Capture usage if the chunk includes it (OpenAI emits in the
            # final chunk when stream_options.include_usage=true).
            usage = payload.get("usage")
            if isinstance(usage, dict):
                usage_input = int(usage.get("prompt_tokens") or usage_input)
                usage_output = int(usage.get("completion_tokens") or usage_output)
                ptd = usage.get("prompt_tokens_details") or {}
                usage_cached = int(ptd.get("cached_tokens") or usage_cached)

            choices = payload.get("choices") or []
            if not choices:
                continue
            choice0 = choices[0]
            delta = choice0.get("delta") or {}
            chunk_finish = choice0.get("finish_reason")
            if chunk_finish:
                finish_reason = chunk_finish

            # Text deltas
            content = delta.get("content")
            if isinstance(content, str) and content:
                if not text_block_started:
                    text_block_index = next_block_index
                    next_block_index += 1
                    yield _sse(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": text_block_index,
                            "content_block": {"type": "text", "text": ""},
                        },
                    )
                    text_block_started = True
                yield _sse(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": text_block_index,
                        "delta": {"type": "text_delta", "text": content},
                    },
                )

            # Tool call deltas — OpenAI sends incremental ``arguments`` JSON
            # fragments per tool index. We translate to ``input_json_delta``
            # blocks. First time we see a new tool index, emit
            # content_block_start with the function name.
            tcs = delta.get("tool_calls")
            if isinstance(tcs, list):
                for tc in tcs:
                    if not isinstance(tc, dict):
                        continue
                    tc_idx = int(tc.get("index", 0))
                    tcall_state = tool_blocks.get(tc_idx)
                    fn = tc.get("function") or {}
                    name = fn.get("name")
                    args_chunk = fn.get("arguments") or ""
                    tc_id = tc.get("id")
                    if tcall_state is None:
                        # New tool call → start a content block.
                        block_idx = next_block_index
                        next_block_index += 1
                        tool_blocks[tc_idx] = {
                            "block_index": block_idx,
                            "id": tc_id or f"toolu_{uuid.uuid4().hex[:24]}",
                            "name": name or "",
                            "args_buffer": "",
                        }
                        yield _sse(
                            "content_block_start",
                            {
                                "type": "content_block_start",
                                "index": block_idx,
                                "content_block": {
                                    "type": "tool_use",
                                    "id": tool_blocks[tc_idx]["id"],
                                    "name": tool_blocks[tc_idx]["name"],
                                    "input": {},
                                },
                            },
                        )
                    else:
                        block_idx = tcall_state["block_index"]
                        if name and not tcall_state["name"]:
                            tcall_state["name"] = name
                        if tc_id and not tcall_state["id"].startswith("toolu_real_"):
                            tcall_state["id"] = tc_id
                    if args_chunk:
                        tool_blocks[tc_idx]["args_buffer"] += args_chunk
                        yield _sse(
                            "content_block_delta",
                            {
                                "type": "content_block_delta",
                                "index": tool_blocks[tc_idx]["block_index"],
                                "delta": {
                                    "type": "input_json_delta",
                                    "partial_json": args_chunk,
                                },
                            },
                        )

    # Close any open content blocks.
    if text_block_started:
        yield _sse(
            "content_block_stop",
            {"type": "content_block_stop", "index": text_block_index},
        )
    for state in tool_blocks.values():
        yield _sse(
            "content_block_stop",
            {"type": "content_block_stop", "index": state["block_index"]},
        )

    # message_delta — carries the final stop_reason + output_tokens.
    yield _sse(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {
                "stop_reason": _openai_finish_to_anthropic_stop(finish_reason),
                "stop_sequence": None,
            },
            "usage": {
                "input_tokens": usage_input,
                "output_tokens": usage_output,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": usage_cached,
            },
        },
    )
    yield _sse("message_stop", {"type": "message_stop"})


__all__ = [
    "anthropic_to_openai_messages",
    "anthropic_tool_choice_to_openai",
    "anthropic_tools_to_openai",
    "openai_response_to_anthropic_message",
    "openai_stream_to_anthropic_events",
]
