"""Provider balance monitor — отслеживание остатков на upstream-аккаунтах.

Sprint 14 (2026-05-10).

Зачем
=====
CEO Brikko хочет на одной странице видеть, сколько денег / токенов
осталось на каждом upstream-аккаунте провайдеров (OpenAI, Anthropic,
DeepSeek, Sber, Moonshot, MiniMax, Zhipu, Together, Yandex, Google).
Если баланс кончится — gateway начнёт отдавать 502 клиентам, что
недопустимо для legal-AI-platform.

Архитектура
===========

Один ORM-class ``ProviderBalance`` (см. ``db.models``), один ряд на
провайдера. Refresh происходит из трёх источников:

* ``api``    — публичный balance endpoint (DeepSeek/Sber/Moonshot/MiniMax/Zhipu).
               Реализованы в ``adapters/{deepseek,sber,moonshot,minimax,zhipu}.py``.
* ``manual`` — POST /v1/account/admin/provider_balances/{provider}/manual
               (OpenAI/Anthropic/Together — без публичного balance API,
               см. ScrapeRemoteAdapter в фазе 2).
* ``scrape`` — Phase 2, отдельный Playwright Docker сервис (см. roadmap).

Public API
==========

* ``BalanceAdapter`` — ABC из ``adapters.base``.
* ``BalanceSnapshot`` — dataclass с native value + currency + raw response.
* ``service.refresh_all()``     — пробежать всем API-adapter'ам параллельно.
* ``service.set_manual()``      — записать manual value.
* ``service.get_all()``         — вернуть все строки + computed runway.
* ``service.balance_refresh_loop()`` — long-running cron task (вызывается
                                       из main.lifespan).
"""

from __future__ import annotations

from voltari_gateway.balance_monitor.adapters.base import (
    BalanceAdapter,
    BalanceSnapshot,
)

__all__ = ["BalanceAdapter", "BalanceSnapshot"]
