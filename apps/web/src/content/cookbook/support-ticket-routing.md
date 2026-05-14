---
title: "Автомаршрутизация support-тикетов по компетенциям"
slug: support-ticket-routing
description: "Классифицируй входящий тикет по типу (billing/technical/account/feature) и отправляй в нужный канал Slack или Telegram. Готовый код."
model_recommended: deepseek-v4-flash
tags: [support, routing, classification, saas, automation]
audience: saas
estimated_cost_per_1000_calls: 14
created_at: 2026-05-01
---

# Автомаршрутизация support-тикетов по компетенциям

## Зачем это нужно

В SaaS-стартапе на 5-15 человек support-команды как таковой нет: тикеты из Intercom/Crisp/email падают в общий канал, и кто-то из разработчиков или продактов берёт первый. Технический баг попадает к биллинговому, billing-вопрос — к разработчику. Каждый раз одно и то же: «это не мой, передам». Среднее время первого ответа растягивается до 4-8 часов вместо обещанных 30 минут.

Решение — классификатор на LLM, который читает тикет и проставляет тип: `billing` / `technical` / `account` / `feature_request` / `general`. Тикет автоматически уходит в нужный канал Slack или Telegram-группу с упоминанием дежурного. Время до первого человека-исполнителя падает с часов до минут, никто не «передаёт».

## Какая модель и почему

`auto:cheap` (DeepSeek V3.2 Chat). Задача — пятиклассовая классификация по короткому тексту (1-3 предложения тикета). Точность DeepSeek на тестовом наборе 300 размеченных тикетов — 93%, ошибки в основном между `general` и `feature_request` (граница реально размытая).

Если 7% ошибок неприемлемо — переключайся на Gemini 3 Flash (через `auto:smart`), точность поднимется до 96%, цена вырастет в 4 раза. На объёме до 5 000 тикетов в месяц разница в абсолютных рублях — 50-200 ₽.

## Готовый код

```python
from openai import OpenAI
import json
import requests

client = OpenAI(
    api_key="brk_live_...",
    base_url="https://api.brikko.ru/v1",
)

SYSTEM_PROMPT = """Ты классифицируешь тикеты в support SaaS-продукта.

Категории:
- billing: вопросы об оплате, счетах, актах, тарифах, возвратах.
- technical: баги, ошибки API, не работает фича, 500-ки, медленно.
- account: доступ, пароль, smena seat, удаление аккаунта, SSO.
- feature_request: просьба добавить фичу или интеграцию.
- general: всё остальное (документация, общие вопросы, спасибо).

Верни JSON: {"category": "...", "urgency": "low|normal|high", "reason": "1 фраза"}.
urgency=high — если упомянут downtime, потеря денег, или клиент злой.
"""

ROUTING = {
    "billing": ("https://hooks.slack.com/services/...", "@billing-team"),
    "technical": ("https://hooks.slack.com/services/...", "@dev-on-call"),
    "account": ("https://hooks.slack.com/services/...", "@admin"),
    "feature_request": ("https://hooks.slack.com/services/...", "@product"),
    "general": ("https://hooks.slack.com/services/...", "@everyone"),
}

def classify_and_route(ticket_text: str, ticket_url: str) -> dict:
    response = client.chat.completions.create(
        model="auto:cheap",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": ticket_text},
        ],
        response_format={"type": "json_object"},
        temperature=0,
        max_tokens=80,
    )
    result = json.loads(response.choices[0].message.content)
    webhook, mention = ROUTING[result["category"]]
    requests.post(webhook, json={
        "text": (
            f"{mention} новый тикет ({result['urgency']}): {ticket_url}\n"
            f"> {ticket_text[:200]}"
        ),
    })
    return result
```

## Примеры

**Кейс 1. Технический баг:**

Вход: «Получаю 500 на /v1/chat/completions с моделью claude-sonnet-4-6 уже 10 минут. Прод стоит, теряем деньги. request-id req_abc123.»

Выход:
```json
{"category": "technical", "urgency": "high", "reason": "Production down, явный 500, упомянут request-id"}
```

Slack-сообщение в канал `#support-technical`: упоминание `@dev-on-call`, инцидент берут в работу за 2-5 минут.

**Кейс 2. Билинг:**

Вход: «Здравствуйте, нам нужен акт за апрель и счёт-фактура. Закрываем квартал, бухгалтер просит до конца недели.»

Выход:
```json
{"category": "billing", "urgency": "normal", "reason": "Запрос закрывающих документов, есть срок но не критичный"}
```

Уходит в `#support-billing` с `@billing-team`.

## Калькулятор стоимости

Тикет — 50-300 токенов вход, 30 токенов выход (короткий JSON).

| Параметр | Значение |
|---|---|
| Модель | DeepSeek V3.2 Chat (через `auto:cheap`) |
| Цена входа | 30 ₽ / 1М токенов |
| Цена выхода | 70 ₽ / 1М токенов |
| **1 тикет (средний)** | **~0,014 ₽** |
| 1 000 тикетов | ~14 ₽ |
| 10 000 тикетов | ~140 ₽ |
| 50 000 тикетов в месяц | ~700 ₽ |

Welcome-бонуса 200 ₽ хватает на ~14 000 тикетов — больше, чем у большинства SaaS на 5-50 чел в месяц.

## Что улучшить дальше

1. **Prompt caching на категории.** System prompt с описанием категорий не меняется — закэшируй (доступно в V2 Brikko), сэкономь до 60% на входных токенах при объёме от 200 тикетов в день.
2. **Логировать решения для дообучения.** Сохраняй пары `(тикет → категория → правильно ли)` в БД. Через 2-3 месяца будет датасет на 5-10к примеров — на нём можно либо файнтюнить open-source модель и снижать стоимость в 10 раз, либо просто улучшать system prompt по разбору ошибок.
3. **PII в тикетах.** Клиенты часто пишут с email, телефоном, ID платежа в теле тикета. Включи PII-маскинг — в LLM-провайдера эти данные не уходят, классификация от этого не страдает (категория от телефона не зависит).

## Связанные рецепты

- [Классификация лидов в CRM](/cookbook/crm-lead-classification) — та же логика классификации, но для входящих лидов и температуры.
- [Автогенерация follow-up email](/cookbook/email-followup-generation) — следующий шаг после ответа на тикет: автоматический follow-up через 2 дня.
