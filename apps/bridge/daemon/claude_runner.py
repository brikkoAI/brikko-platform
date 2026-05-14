"""Spawn `claude --print` subprocess and yield BridgeEvents asynchronously.

Architecture decision (DAY1_PROTOTYPE_RESULTS.md):
We always pass `--dangerously-skip-permissions` because `--print` mode
does not support interactive approval injection. Tool calls are streamed
to the bot in real time so the user sees what's happening; pre-flight
heuristic on bot side warns about obviously dangerous prompts.
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from daemon import process_inspector, runner_registry
from daemon.config import get_settings
from daemon.stream_parser import parse_stream_lines
from shared.events import BridgeEvent, ErrorEvent

log = logging.getLogger(__name__)


async def run_claude(
    session_id: str,
    prompt: str,
    cwd: str | None = None,
    force: bool = False,
) -> AsyncIterator[BridgeEvent]:
    """Spawn `claude --resume <id> --print --output-format=stream-json` and yield events.

    Always uses `--dangerously-skip-permissions`. Bot is responsible for
    pre-flight approval checks before calling this function.

    Args:
      session_id: Claude Code session UUID to resume.
      prompt:     User's text message.
      cwd:        Working directory for claude. Required for /resume to
                  find the session jsonl, also used by the mutex check.
      force:      Skip the CLI-vs-bridge mutex. Set when the user prefixed
                  their prompt with /yolo — they accepted the risk that an
                  interactive Claude in the same folder might be writing
                  to the same session jsonl.

    Yields:
      BridgeEvent objects (SystemInit, TextDelta, ToolUse, ToolResult,
      StreamEnd, ErrorEvent) as they arrive from claude's stream-json.
    """
    settings = get_settings()

    # CLI-vs-bridge mutex: refuse to spawn if an interactive Claude is
    # already running in the same project folder. Both procs would write
    # into the same ~/.claude/projects/<encoded>/<id>.jsonl which can
    # corrupt the conversation history. We compare cwd directly — Claude
    # only resolves sessions from its launch cwd, so equal cwd ⇒ equal
    # session pool. The user's options when blocked: close the CLI
    # Claude, use it for this prompt instead, or override with /yolo.
    if cwd and not force:
        cli_procs = process_inspector.find_cli_processes_for_cwd(cwd)
        if cli_procs:
            pids = ", ".join(str(p.pid) for p in cli_procs[:3])
            yield ErrorEvent(
                message=(
                    f"🔒 Сессия занята: в проекте `{cwd}` уже работает "
                    f"CLI-Claude (PID {pids}). Закрой её и попробуй снова "
                    "— одновременная запись в jsonl может его испортить. "
                    "Уверен что хочешь продолжить — пришли с префиксом /yolo."
                ),
                code="session_busy_cli",
            )
            return

    args = [
        settings.claude_binary,
        "--resume", session_id,
        "--print", prompt,
        "--output-format=stream-json",
        "--verbose",
        "--dangerously-skip-permissions",
    ]

    log.info("spawning claude: session=%s, prompt_len=%d", session_id, len(prompt))

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            cwd=cwd,
        )
    except FileNotFoundError:
        yield ErrorEvent(
            message=f"claude binary not found at {settings.claude_binary!r}",
            code="binary_missing",
        )
        return
    except OSError as e:
        yield ErrorEvent(message=f"failed to spawn claude: {e}", code="spawn_failed")
        return

    assert proc.stdout is not None

    # Register so /cancel can find and kill us. Evicts any prior proc for
    # the same session_id (parallel-prompt protection).
    runner_registry.register(session_id, proc)

    # Stream-parse line by line as they arrive
    line_buffer: list[str] = []
    try:
        async for raw_line in proc.stdout:
            try:
                line = raw_line.decode("utf-8", errors="replace")
            except Exception:
                continue
            if not line.strip():
                continue
            # parse_stream_lines yields multiple events per "assistant" line,
            # so we feed it line-by-line via a 1-element list
            for event in parse_stream_lines([line]):
                yield event
    except Exception as e:
        log.exception("error reading claude stdout")
        yield ErrorEvent(message=f"stream read error: {e}", code="stream_error")
    finally:
        runner_registry.unregister(session_id, proc)

    # Wait for claude to fully exit, capture stderr if non-zero exit
    rc = await proc.wait()
    if rc != 0:
        stderr_bytes = b""
        if proc.stderr is not None:
            try:
                stderr_bytes = await asyncio.wait_for(proc.stderr.read(), timeout=2)
            except asyncio.TimeoutError:
                pass
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")[:500]
        log.warning("claude exited rc=%d stderr=%s", rc, stderr_text)
        yield ErrorEvent(
            message=f"claude exited code {rc}: {stderr_text}",
            code=f"exit_{rc}",
        )
