"""Третьесторонние интеграции (telegram, slack, ...).

Sprint 4 Поток M добавил Telegram-бота @VoltariBot. Основные точки входа —
``telegram_bot.handle_update`` (webhook handler) и
``telegram_bot.send_alert`` (push-алерты от бэкенда: low-balance, failover,
new key created).
"""

from __future__ import annotations
