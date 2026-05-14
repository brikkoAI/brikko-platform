"""Slash command handlers — /sessions /switch /status /cancel /help.

These all interact with the daemon (over the SSH reverse tunnel) and
update the bot-side SQLite state. They're registered AFTER /start (auth)
but BEFORE the prompt catch-all in handlers/__init__.py.
"""
from __future__ import annotations

import datetime as dt
import logging

from aiogram import Dispatcher
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.config import get_settings
from bot.db import audit, get_active_session, recent_audit, set_active_session
from bot.services.daemon_client import (
    DaemonOffline,
    active_sessions,
    cancel_session,
    health,
    list_processes,
    list_sessions,
)

log = logging.getLogger(__name__)


HELP_TEXT = (
    "📚 *Команды Brikko Bridge*\n\n"
    "/sessions — показать Claude-сессии на ПК (нумерованный список)\n"
    "/switch <n> — выбрать n-ю сессию из списка\n"
    "/status — статус daemon и текущая сессия\n"
    "/cancel — прервать текущую Claude-сессию\n"
    "/audit [n] — последние n событий (по умолчанию 20)\n"
    "/permissions [reset] — показать/сбросить always-allow кэш сессии\n"
    "/follow on|off|status — зеркалить CLI-сессию в этот чат\n"
    "/yolo <prompt> — отправить промпт без pre-flight проверки\n"
    "/safe <prompt> — строгая проверка: запросит подтверждение для любого write\n"
    "/help — это сообщение\n\n"
    "Любой другой текст = промпт в Claude (после /switch).\n\n"
    "🔒 *Approval flow:* перед каждым Edit/Write/Bash-with-side-effect "
    "бот спросит — нажми ✅ Allow или ❌ Deny. Для Edit/Write можно "
    "выбрать «Always allow в этой сессии». Кэш живёт до restart daemon.\n\n"
    "🖥 *CLI mirror:* `/follow on` — daemon начнёт зеркалить активную "
    "сессию из CLI на ПК в этот чат. Output Claude и твои промпты в CLI "
    "будут приходить в TG. `/follow off` — выключить."
)

DAEMON_OFFLINE = "❌ Daemon недоступен. Проверь, запущен ли `start-bridge.bat` на ПК."

NO_ACTIVE_SESSION = (
    "У тебя ещё не выбрана сессия. Сначала /sessions, потом /switch <n>."
)


def _format_ago(unix_ts: float) -> str:
    """Human-friendly 'N min ago'."""
    delta = dt.datetime.now() - dt.datetime.fromtimestamp(unix_ts)
    if delta.days > 0:
        return f"{delta.days}d ago"
    hours = delta.seconds // 3600
    if hours > 0:
        return f"{hours}h ago"
    minutes = (delta.seconds // 60) or 1
    return f"{minutes}m ago"


# cwd prefixes that mark a process as a Claude internal worker (Cowork
# edition spawns ~13 of those into system32 / Program Files). They never
# write to a user session jsonl — hiding them keeps /status readable.
_INTERNAL_CWD_PREFIXES = (
    "c:/windows/",
    "c:/program files/",
    "c:/program files (x86)/",
    "/usr/",
    "/system/",
)


def _group_procs_by_cwd(procs: list[dict]) -> list[dict]:
    """Collapse procs sharing the same cwd into single rows.

    The Cowork edition spawns ~10+ idle worker processes per project,
    flooding ``/status`` with duplicates. After this, 14 cli rows
    typically become 1-3 grouped rows.

    Each output dict has: ``label`` (project basename),
    ``count``, and ``oldest_started_at`` (so the user sees how stale the
    project is).

    Returned list is sorted by oldest started time DESC — newest project
    activity first.
    """
    by_cwd: dict[str, dict] = {}
    for p in procs:
        cwd = (p.get("cwd") or "?").replace("\\", "/").rstrip("/")
        bucket = by_cwd.setdefault(
            cwd,
            {
                "label": _project_label(cwd),
                "count": 0,
                "oldest_started_at": float("inf"),
            },
        )
        bucket["count"] += 1
        ts = p.get("started_at") or 0
        if ts and ts < bucket["oldest_started_at"]:
            bucket["oldest_started_at"] = ts
    out = list(by_cwd.values())
    # Most recent project at top
    out.sort(key=lambda g: g["oldest_started_at"], reverse=True)
    # Replace inf if no proc had a real timestamp
    for g in out:
        if g["oldest_started_at"] == float("inf"):
            g["oldest_started_at"] = 0
    return out


def _is_user_proc(p: dict) -> bool:
    cwd = (p.get("cwd") or "").replace("\\", "/").lower()
    if not cwd:
        return True  # unknown — keep, better noisy than silent
    return not any(cwd.startswith(pref) for pref in _INTERNAL_CWD_PREFIXES)


def _project_label(path_or_encoded: str) -> str:
    """Short human-readable project label for the bot UI.

    Accepts either a real path (``C:\\Users\\gridc\\Desktop\\Стартап``) or
    Claude's encoded form (``C--Users-gridc-Desktop----``) and returns the
    last meaningful segment so the user sees ``Стартап`` not the whole path.
    """
    if not path_or_encoded:
        return "?"
    # Real path? (\ or /)
    if "\\" in path_or_encoded or "/" in path_or_encoded:
        norm = path_or_encoded.replace("\\", "/").rstrip("/")
        return norm.rsplit("/", 1)[-1] or norm
    # Encoded form — last non-empty dash-segment
    parts = [p for p in path_or_encoded.split("-") if p]
    return parts[-1] if parts else path_or_encoded


# ============================================================================
# /sessions
# ============================================================================

async def cmd_sessions(message: Message) -> None:
    settings = get_settings()
    try:
        sessions = await list_sessions(settings.daemon_url)
    except DaemonOffline:
        await message.answer(DAEMON_OFFLINE)
        return

    if not sessions:
        await message.answer(
            "📭 На ПК пока нет Claude-сессий. Запусти `claude` где-нибудь, "
            "и сессия появится здесь."
        )
        return

    # Show top 20 to avoid 4096-char overflow
    shown = sessions[:20]
    lines = ["🗂 *Claude-сессии на ПК* (top 20):\n"]
    for i, s in enumerate(shown, start=1):
        # Prefer the real cwd if daemon found one in the session jsonl
        proj = _project_label(s.get("cwd") or s.get("project_path", "?"))
        ago = _format_ago(s.get("last_modified", 0))
        summary = s.get("summary") or "(без описания)"
        if len(summary) > 60:
            summary = summary[:60] + "…"
        lines.append(f"{i}. `{proj}` · {ago}\n   {summary}")
    lines.append("\nВыбрать: `/switch <n>`")
    await message.answer("\n".join(lines))


# ============================================================================
# /switch <n>
# ============================================================================

async def cmd_switch(message: Message, command: CommandObject) -> None:
    settings = get_settings()
    arg = (command.args or "").strip() if command else ""
    if not arg.isdigit():
        await message.answer("Использование: `/switch <номер из /sessions>`")
        return
    n = int(arg)
    if n < 1:
        await message.answer("Номер должен быть от 1.")
        return

    try:
        sessions = await list_sessions(settings.daemon_url)
    except DaemonOffline:
        await message.answer(DAEMON_OFFLINE)
        return

    if n > len(sessions):
        await message.answer(f"Нет сессии #{n}. Всего сессий: {len(sessions)}.")
        return

    chosen = sessions[n - 1]
    session_id = chosen["session_id"]
    # Prefer the real cwd extracted from the session jsonl. Fall back to
    # the encoded directory name if for some reason cwd is missing — that's
    # better than nothing, but `claude --resume` won't find the session.
    real_cwd = chosen.get("cwd") or chosen.get("project_path", "")

    await set_active_session(
        settings.db_path,
        chat_id=message.chat.id,
        session_id=session_id,
        project_path=real_cwd,
    )
    await audit(
        settings.db_path,
        chat_id=message.chat.id,
        event="session_switched",
        session_id=session_id,
    )

    proj = _project_label(real_cwd)
    await message.answer(
        f"✅ Активная сессия: `{session_id[:8]}…` (проект `{proj}`).\n"
        "Теперь можно писать промпты текстом."
    )


# ============================================================================
# /status
# ============================================================================

async def cmd_status(message: Message) -> None:
    settings = get_settings()

    # Daemon health
    try:
        h = await health(settings.daemon_url)
        daemon_line = f"✓ daemon ok · v{h.get('version', '?')} · claude {h.get('claude_version', '?')}"
    except DaemonOffline as e:
        daemon_line = f"❌ daemon offline ({e})"

    # Active session
    sess = await get_active_session(settings.db_path, message.chat.id)
    if sess:
        proj = _project_label(sess.get("project_path", ""))
        sess_line = f"📌 сессия: `{sess['session_id'][:8]}…` · проект `{proj}`"
    else:
        sess_line = "📌 сессия: не выбрана (`/sessions` → `/switch <n>`)"

    # In-flight check (daemon-spawned claude)
    in_flight_line = ""
    try:
        active = await active_sessions(settings.daemon_url)
        if sess and sess["session_id"] in active:
            in_flight_line = "\n🔧 сейчас выполняется (можно `/cancel`)"
    except DaemonOffline:
        pass

    # CLI-claude processes — group by cwd so 13 Cowork workers in the
    # same project show as one row "(×13)" instead of flooding /status.
    cli_lines: list[str] = []
    try:
        procs = await list_processes(settings.daemon_url)
        cli = [p for p in procs if p.get("kind") == "cli" and _is_user_proc(p)]
        if cli:
            groups = _group_procs_by_cwd(cli)
            cli_lines.append(f"\n🖥 CLI Claude · проектов: {len(groups)}, всего: {len(cli)}")
            for g in groups[:5]:
                ago = _format_ago(g["oldest_started_at"])
                count_label = f" (×{g['count']})" if g["count"] > 1 else ""
                cli_lines.append(f"   `{g['label']}`{count_label} · started {ago}")
            if len(groups) > 5:
                cli_lines.append(f"   …и ещё {len(groups) - 5} проектов")
    except DaemonOffline:
        pass

    await message.answer(
        f"{daemon_line}\n{sess_line}{in_flight_line}{''.join(cli_lines)}"
    )


# ============================================================================
# /cancel
# ============================================================================

async def cmd_cancel(message: Message) -> None:
    settings = get_settings()
    sess = await get_active_session(settings.db_path, message.chat.id)
    if not sess:
        await message.answer(NO_ACTIVE_SESSION)
        return

    try:
        result = await cancel_session(settings.daemon_url, sess["session_id"])
    except DaemonOffline:
        await message.answer(DAEMON_OFFLINE)
        return

    await audit(
        settings.db_path,
        chat_id=message.chat.id,
        event="cancel_requested",
        session_id=sess["session_id"],
        killed=result.get("killed", False),
    )
    if result.get("killed"):
        await message.answer("🛑 Прервано.")
    else:
        await message.answer("ℹ️ Нечего прерывать — сессия и так не работает.")


# ============================================================================
# /audit
# ============================================================================

# How event names render in /audit output. Anything missing falls back to
# the raw event name with a 📝 prefix.
_AUDIT_ICONS = {
    "auth_success": "🔐",
    "auth_failed": "❌",
    "session_switched": "🔀",
    "prompt_sent": "→",
    "prompt_done": "✓",
    "prompt_failed": "❌",
    "preflight_blocked": "⚠️",
    "cancel_requested": "🛑",
    "bot_started": "🚀",
}


def _format_audit_row(row: dict) -> str:
    ts = dt.datetime.fromtimestamp(row["ts"]).strftime("%m-%d %H:%M")
    icon = _AUDIT_ICONS.get(row["event"], "📝")
    meta = row.get("meta") or {}
    # Show a short, helpful detail per event type
    detail = ""
    if row["event"] == "session_switched" and "session_id" in meta:
        detail = f" → `{meta['session_id'][:8]}…`"
    elif row["event"] == "prompt_sent" and "prompt_len" in meta:
        detail = f" ({meta['prompt_len']} chars)"
    elif row["event"] == "prompt_done":
        detail = " (with errors)" if meta.get("had_error") else ""
    elif row["event"] == "preflight_blocked" and meta.get("reasons"):
        reasons = ", ".join(meta["reasons"][:3])
        detail = f": `{reasons}`"
    elif row["event"] == "auth_failed" and meta.get("reason"):
        detail = f" ({meta['reason']})"
    elif row["event"] == "cancel_requested":
        detail = " (killed)" if meta.get("killed") else " (idle)"
    return f"`{ts}` {icon} {row['event']}{detail}"


async def cmd_audit(message: Message, command: CommandObject) -> None:
    settings = get_settings()
    arg = (command.args or "").strip() if command else ""
    try:
        limit = int(arg) if arg else 20
    except ValueError:
        limit = 20
    limit = max(1, min(limit, 100))

    rows = await recent_audit(settings.db_path, message.chat.id, limit=limit)
    if not rows:
        await message.answer("📜 Аудит пуст для этого чата.")
        return

    lines = [f"📜 *Последние {len(rows)} событий*:\n"]
    for r in rows:
        lines.append(_format_audit_row(r))
    await message.answer("\n".join(lines))


# ============================================================================
# /help
# ============================================================================

async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)


# ============================================================================
# /yolo <prompt> and /safe <prompt> — modifier commands
# ============================================================================

async def cmd_yolo(message: Message, command: CommandObject) -> None:
    """Forward the rest of the message to the prompt handler with skip-preflight."""
    from bot.handlers.prompt import handle_prompt

    payload = (command.args or "").strip() if command else ""
    if not payload:
        await message.answer("Использование: `/yolo <промпт>` — без pre-flight проверки.")
        return
    # aiogram Message is a frozen pydantic model — pass the prompt as an
    # explicit override instead of mutating message.text.
    await handle_prompt(message, skip_preflight=True, text_override=payload)


async def cmd_safe(message: Message, command: CommandObject) -> None:
    """Tighter pre-flight — flags any write intent verb."""
    from bot.handlers.prompt import handle_prompt

    payload = (command.args or "").strip() if command else ""
    if not payload:
        await message.answer("Использование: `/safe <промпт>` — строгая pre-flight проверка.")
        return
    await handle_prompt(message, strict_preflight=True, text_override=payload)


def register_handlers(dp: Dispatcher) -> None:
    dp.message.register(cmd_sessions, Command("sessions"))
    dp.message.register(cmd_switch, Command("switch"))
    dp.message.register(cmd_status, Command("status"))
    dp.message.register(cmd_cancel, Command("cancel"))
    dp.message.register(cmd_audit, Command("audit"))
    dp.message.register(cmd_help, Command("help"))
    dp.message.register(cmd_yolo, Command("yolo"))
    dp.message.register(cmd_safe, Command("safe"))
