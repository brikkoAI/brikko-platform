"""Per-provider balance adapters.

Каждый адаптер инкапсулирует один HTTP-call к публичному balance API
конкретного upstream-провайдера. Все адаптеры implement ``BalanceAdapter``
ABC из ``base.py``.

В фазе 1 (Sprint 14) реализованы:
* ``DeepSeekBalanceAdapter`` — GET /user/balance, USD.
* ``SberBalanceAdapter``     — GET /balance + OAuth, токены (не валюта).
* ``MoonshotBalanceAdapter`` — GET /v1/users/me/balance, USD.
* ``MiniMaxBalanceAdapter``  — GET /v1/billing/wallet/balance, RMB/USD.
* ``ZhipuBalanceAdapter``    — GET /api/paas/v4/account/balance + JWT, CNY.
* ``ManualAdapter``          — wrapper для manually-entered значений
                                (не fetch'ит — возвращает stored).

Фаза 2: OpenAI/Anthropic/Together — Playwright-scrape (см. ScrapeRemoteAdapter).
"""

from __future__ import annotations

from voltari_gateway.balance_monitor.adapters.base import (
    BalanceAdapter,
    BalanceSnapshot,
)
from voltari_gateway.balance_monitor.adapters.deepseek import DeepSeekBalanceAdapter
from voltari_gateway.balance_monitor.adapters.manual import ManualAdapter
from voltari_gateway.balance_monitor.adapters.minimax import MiniMaxBalanceAdapter
from voltari_gateway.balance_monitor.adapters.moonshot import MoonshotBalanceAdapter
from voltari_gateway.balance_monitor.adapters.sber import SberBalanceAdapter
from voltari_gateway.balance_monitor.adapters.scrape_remote import ScrapeRemoteAdapter
from voltari_gateway.balance_monitor.adapters.zhipu import ZhipuBalanceAdapter

__all__ = [
    "BalanceAdapter",
    "BalanceSnapshot",
    "DeepSeekBalanceAdapter",
    "ManualAdapter",
    "MiniMaxBalanceAdapter",
    "MoonshotBalanceAdapter",
    "SberBalanceAdapter",
    "ScrapeRemoteAdapter",
    "ZhipuBalanceAdapter",
]
