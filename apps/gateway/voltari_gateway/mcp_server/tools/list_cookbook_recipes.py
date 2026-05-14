"""``list_cookbook_recipes`` MCP tool — ready-made prompt recipes.

Returns the five hand-curated cookbook entries that ship on
``brikko.ru/cookbook`` so an agent can offer them to a user who's
exploring "what can I actually build with this gateway".

Source of truth is the markdown set under
``apps/web/src/content/cookbook/*.md``. We mirror the frontmatter here
(slug, title, summary, recommended model, audience tag, est. cost) so
the gateway doesn't take a cross-app dependency on the marketing site's
build pipeline — adding a sixth recipe is a one-line PR to both.

Why hardcode instead of parsing the .md files at runtime:

* The gateway container doesn't ship the marketing files (different
  build context, different Docker image). Mounting them would be a
  bigger surface than this tool warrants.
* Recipe set is tiny and stable (5 entries, change quarterly). When we
  grow to 20+ we move to a shared JSON file in ``packages/``.
* Read-only, no agent input — drift risk is "marketing edits the .md
  but forgets the python const". Caught in CI: a follow-up sprint adds
  ``test_cookbook_recipes_in_sync`` that diffs frontmatter.

Public links point at ``brikko.ru/cookbook/{slug}`` — the production
canonical URL.
"""

from __future__ import annotations

from typing import Any

from voltari_gateway.mcp_server.context import current_principal

NAME = "list_cookbook_recipes"
DESCRIPTION = (
    "Return the 5 ready-made Brikko cookbook recipes (CRM lead classification, "
    "support ticket routing, contract PII redaction, email follow-up, essay "
    "grading). Each entry includes slug, title, recommended model, audience "
    "tag, estimated kopecks per 1000 calls, and the public URL. Read-only."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

# Mirrors apps/web/src/content/cookbook/*.md frontmatter as of 2026-05-12.
# Numbers are RUB (not kopecks) per 1000 calls — that's what the .md
# frontmatter carries (``estimated_cost_per_1000_calls`` is RUB). We
# expose both ``rub`` and ``kopecks`` to remove a class of "is this
# RUB or kopecks?" agent mistakes.
_RECIPES: tuple[dict[str, Any], ...] = (
    {
        "slug": "crm-lead-classification",
        "title": "Классификация лидов в CRM: hot/warm/cold за 200 ₽ на 1000 обращений",
        "summary": (
            "Готовый рецепт классификации входящих лидов через DeepSeek V3.2. "
            "Код на Python, расчёт стоимости, пример входа и выхода."
        ),
        "model_recommended": "deepseek-v3.2-chat",
        "audience": "sales",
        "tags": ("crm", "classification", "sales", "hr-tech", "deepseek"),
        "estimated_cost_rub_per_1k_requests": 12,
        "estimated_cost_kopecks_per_1k_requests": 1200,
    },
    {
        "slug": "support-ticket-routing",
        "title": "Автомаршрутизация support-тикетов по компетенциям",
        "summary": (
            "Классифицируй входящий тикет по типу (billing/technical/account/"
            "feature) и отправляй в нужный канал Slack или Telegram."
        ),
        "model_recommended": "deepseek-v3.2-chat",
        "audience": "saas",
        "tags": ("support", "routing", "classification", "saas", "automation"),
        "estimated_cost_rub_per_1k_requests": 14,
        "estimated_cost_kopecks_per_1k_requests": 1400,
    },
    {
        "slug": "contract-pii-redaction",
        "title": "Анализ договоров с обезличиванием ПДн через PII-маскинг",
        "summary": (
            "Как юрфирме прогнать договор через Claude Sonnet 4.6 с "
            "автоматической заменой ФИО, телефонов и адресов. 152-ФЗ-"
            "совместимый рецепт."
        ),
        "model_recommended": "claude-sonnet-4.6",
        "audience": "legal",
        "tags": ("legal", "pii", "redaction", "claude", "compliance", "152-fz"),
        "estimated_cost_rub_per_1k_requests": 980,
        "estimated_cost_kopecks_per_1k_requests": 98_000,
    },
    {
        "slug": "email-followup-generation",
        "title": "Автогенерация follow-up email после звонка с клиентом",
        "summary": (
            "Как из transcript звонка или summary в CRM сгенерить персональный "
            "follow-up email за 2 секунды. Готовый код, шаблон, расчёт стоимости."
        ),
        "model_recommended": "auto:smart",
        "audience": "saas",
        "tags": ("sales", "email", "followup", "automation", "crm"),
        "estimated_cost_rub_per_1k_requests": 220,
        "estimated_cost_kopecks_per_1k_requests": 22_000,
    },
    {
        "slug": "student-essay-grading",
        "title": "AI-проверка эссе с обоснованием оценки",
        "summary": (
            "Как через GPT-5.4 с failover на Claude проверять эссе студентов "
            "по рубрике с оценкой 1-10 и развёрнутым фидбэком."
        ),
        "model_recommended": "gpt-5.4",
        "audience": "edtech",
        "tags": ("edtech", "grading", "essay", "education", "failover"),
        "estimated_cost_rub_per_1k_requests": 480,
        "estimated_cost_kopecks_per_1k_requests": 48_000,
    },
)

_PUBLIC_BASE = "https://brikko.ru/cookbook"


def _render(recipe: dict[str, Any]) -> dict[str, Any]:
    slug = recipe["slug"]
    return {
        "slug": slug,
        "title": recipe["title"],
        "summary": recipe["summary"],
        "model_recommended": recipe["model_recommended"],
        "audience": recipe["audience"],
        "tags": list(recipe["tags"]),
        "estimated_cost_rub_per_1k_requests": recipe["estimated_cost_rub_per_1k_requests"],
        "estimated_cost_kopecks_per_1k_requests": recipe["estimated_cost_kopecks_per_1k_requests"],
        "prompt_template_url": f"{_PUBLIC_BASE}/{slug}",
    }


async def handler(_arguments: dict[str, Any], _db: Any) -> dict[str, Any]:
    """Materialise the recipe list. No filters, no auth-specific data."""
    _ = current_principal()  # auth assertion

    return {
        "recipes": [_render(r) for r in _RECIPES],
        "total": len(_RECIPES),
    }


__all__ = ["DESCRIPTION", "INPUT_SCHEMA", "NAME", "handler"]
