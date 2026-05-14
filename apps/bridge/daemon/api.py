"""HTTP routes for daemon.

Bot on Aeza calls these endpoints over the SSH reverse tunnel
(Aeza:8090 → here:9090). All traffic is loopback only on this machine,
encrypted in flight by SSH.
"""
import asyncio

from fastapi import APIRouter, Body
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from daemon import auth_tokens, process_inspector, runner_registry
from daemon.approval_state import DEFAULT_STORE
from daemon.claude_runner import run_claude
from daemon.config import get_settings
from daemon.follow_registry_holder import get_shared_follow_registry
from daemon.sdk_runner import get_shared_registry
from daemon.session_discovery import list_sessions

router = APIRouter()


class SendPromptRequest(BaseModel):
    prompt: str
    cwd: str | None = None  # optional working dir override
    force: bool = False     # skip CLI-vs-bridge mutex (set by /yolo)


class TokenConsumeRequest(BaseModel):
    chat_id: int


async def _claude_version() -> str:
    """Run `claude --version` and return the output line.

    Falls back to "unknown" if the binary is missing or errors.
    """
    settings = get_settings()
    try:
        proc = await asyncio.create_subprocess_exec(
            settings.claude_binary,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
    except (FileNotFoundError, asyncio.TimeoutError):
        return "unknown"
    return out.decode(errors="replace").strip() or "unknown"


@router.get("/health")
async def health() -> dict:
    """Liveness probe.

    Returned fields:
      * status         — "ok" if process is alive (this endpoint responding)
      * version        — daemon version (for compat checks from bot)
      * claude_version — what `claude --version` says, so bot can warn
                         when major version changes break stream-json shape
    """
    settings = get_settings()
    return {
        "status": "ok",
        "version": settings.bridge_version,
        "claude_version": await _claude_version(),
    }


@router.get("/sessions")
async def get_sessions() -> dict:
    """List Claude Code sessions on this PC.

    Bot displays this as a numbered list — user picks one with /switch <n>.
    Sessions are sorted by last activity, most recent first.
    """
    return {"sessions": list_sessions()}


@router.post("/auth/tokens/{token}/consume")
async def consume_token(token: str, body: TokenConsumeRequest = Body(...)) -> dict:
    """Bot calls this when /start <token> arrives in Telegram.

    Marks token as consumed by chat_id. Idempotent for same chat_id (re-auth
    on new device): if token already used by THIS chat_id, returns ok=True.
    Otherwise returns ok=False (token unknown / expired / used by someone else).
    """
    if auth_tokens.is_token_consumed_by(token, body.chat_id):
        return {"ok": True, "already_paired": True}
    if auth_tokens.consume_token(token, body.chat_id):
        return {"ok": True, "already_paired": False}
    return {"ok": False, "reason": "invalid_or_expired_or_taken"}


@router.post("/sessions/{session_id}/cancel")
async def cancel_session(session_id: str) -> dict:
    """Kill the live claude session for ``session_id``, if any.

    For the new SDK runner: calls ``client.interrupt()`` on the matching
    SessionRunner. For the legacy subprocess runner: terminates the process.
    Both paths are tried — whichever returns true wins.

    Returns ``{ok: True, killed: True}`` if something was terminated,
    ``{ok: True, killed: False, reason: 'not_running'}`` otherwise. The
    client treats both as success — there's nothing to cancel either way.
    """
    killed = runner_registry.cancel(session_id)
    sdk_reg = get_shared_registry()
    if not killed and sdk_reg is not None:
        killed = await sdk_reg.cancel(session_id)
    return {
        "ok": True,
        "killed": killed,
        "reason": None if killed else "not_running",
    }


@router.get("/sessions/active")
async def get_active_runs() -> dict:
    """Sessions that have a live claude run right now (legacy or SDK)."""
    active = set(runner_registry.active_session_ids())
    sdk_reg = get_shared_registry()
    if sdk_reg is not None:
        active.update(sdk_reg.active_session_ids())
    return {"active": sorted(active)}


@router.get("/sessions/{session_id}/permissions")
async def get_permissions(session_id: str) -> dict:
    """Return the in-memory always-allow / always-deny cache for the session.

    Used by the bot's ``/permissions`` command to show the CEO what's cached.
    """
    snap = DEFAULT_STORE.snapshot(session_id)
    return {
        "session_id": session_id,
        "always_allow": sorted(snap.always_allow),
        "always_deny": sorted(snap.always_deny),
    }


@router.post("/sessions/{session_id}/permissions/reset")
async def reset_permissions(session_id: str) -> dict:
    """Clear all always-allow / always-deny for this session.

    CEO decision 2026-05-11 #4: emergency-reset button in case "always allow"
    was tapped by mistake.
    """
    DEFAULT_STORE.clear_session(session_id)
    return {"ok": True, "session_id": session_id}


@router.get("/follows")
async def list_follows(chat_id: int) -> dict:
    """List active CLI-mirror follows for ``chat_id``.

    Read-only — does not start/stop anything. Mutations go through the
    Redis ``bridge:follow-control`` channel so the daemon and bot remain
    decoupled.
    """
    reg = get_shared_follow_registry()
    if reg is None:
        return {"follows": []}
    snapshot = reg.snapshot(chat_id=chat_id)
    return {
        "follows": [
            {
                "chat_id": s.chat_id,
                "session_id": s.session_id,
                "path": s.path,
                "bytes_read": s.bytes_read,
                "lines_parsed": s.lines_parsed,
                "events_published": s.events_published,
                "started_at": s.started_at,
                "last_activity_at": s.last_activity_at,
                "ended": s.ended,
            }
            for s in snapshot
        ]
    }


@router.get("/processes")
async def list_processes() -> dict:
    """Every ``claude`` process visible on this PC, classified.

    The bot's ``/status`` calls this to show whether the user has an
    interactive (CLI) Claude running locally — relevant because two procs
    writing to the same session jsonl can corrupt it.
    """
    procs = process_inspector.list_claude_processes()
    return {
        "processes": [
            {
                "pid": p.pid,
                "ppid": p.ppid,
                "kind": p.kind,
                "cwd": p.cwd,
                "started_at": p.started_at,
            }
            for p in procs
        ],
    }


@router.post("/sessions/{session_id}/send")
async def send_prompt(
    session_id: str,
    body: SendPromptRequest = Body(...),
) -> StreamingResponse:
    """Send a prompt to Claude (SDK or legacy subprocess), stream events as SSE.

    Path selection (in order):
      1. ``BRIDGE_DAEMON_YOLO_MODE=true`` → legacy subprocess +
         --dangerously-skip-permissions (no approvals at all, hard-override).
      2. ``BRIDGE_DAEMON_LEGACY_SUBPROCESS_RUNNER=true`` → legacy subprocess,
         no override of approvals (Claude prompts on stdin which we can't
         answer; falls back to YOLO too in practice). Kept for Day 1-7 tests.
      3. Default → SDK runner via ApprovalBroker (Phase 8 flow).

    Bot opens this connection, reads ``data: <event-json>\\n\\n`` lines until
    StreamEnd or connection drop.
    """
    settings = get_settings()
    sdk_reg = get_shared_registry()

    use_legacy = (
        settings.yolo_mode
        or settings.legacy_subprocess_runner
        or sdk_reg is None
    )

    async def gen_legacy():
        async for event in run_claude(
            session_id=session_id,
            prompt=body.prompt,
            cwd=body.cwd,
            force=body.force,
        ):
            yield f"data: {event.model_dump_json()}\n\n"

    async def gen_sdk():
        assert sdk_reg is not None
        runner = await sdk_reg.get_or_create(
            session_id=session_id, cwd=body.cwd
        )
        async for event in runner.send(body.prompt, force=body.force):
            yield f"data: {event.model_dump_json()}\n\n"

    if use_legacy:
        return StreamingResponse(gen_legacy(), media_type="text/event-stream")
    return StreamingResponse(gen_sdk(), media_type="text/event-stream")
