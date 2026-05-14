"""Smart routing for Voltari gateway.

The router decides which upstream LLM to call for a given chat request.
It supports four auto-strategies (`auto:cheap`, `auto:smart`, `auto:fast`,
`auto:ru-legal`) plus pinned model ids; for each request it returns a
primary model id and an ordered fallback chain used by the failover layer.

Public surface (kept minimal — everything else is implementation detail):

    from voltari_gateway.router import (
        Router,
        RoutingDecision,
        RouterRequest,
        AccountContext,
        Strategy,
        TaskCategory,
        ModelSpec,
        CATALOG,
        FailoverError,
        with_failover,
    )
"""

from voltari_gateway.router.catalog import CATALOG, ModelSpec, ModelTier, Provider
from voltari_gateway.router.failover import (
    FailoverError,
    FailoverEvent,
    ProviderCallable,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    with_failover,
)
from voltari_gateway.router.router import (
    AccountContext,
    Router,
    RouterRequest,
    RoutingDecision,
)
from voltari_gateway.router.strategies import (
    Strategy,
    TaskCategory,
    categorize_request,
    select_cheap,
    select_fast,
    select_ru_legal,
    select_smart,
)

__all__ = [
    "CATALOG",
    "AccountContext",
    "FailoverError",
    "FailoverEvent",
    "ModelSpec",
    "ModelTier",
    "Provider",
    "ProviderCallable",
    "ProviderError",
    "ProviderRateLimitError",
    "ProviderTimeoutError",
    "Router",
    "RouterRequest",
    "RoutingDecision",
    "Strategy",
    "TaskCategory",
    "categorize_request",
    "select_cheap",
    "select_fast",
    "select_ru_legal",
    "select_smart",
    "with_failover",
]
