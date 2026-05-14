"""POST /v1/anonymize, POST /v1/restore — standalone PII masking endpoints.

Sprint M2 — stateless surface for SDK/skill integrations (Brikko PII Skill,
n8n nodes, presidio drop-in replacement). Unlike /v1/chat/completions where
masking is implicit (gateway маскирует input, отправляет в LLM, восстанавливает
output), these endpoints expose the masking как переиспользуемые primitives:

    1. Клиент шлёт plaintext → /v1/anonymize → получает masked text + mapping_id
    2. Клиент сам отправляет masked text в любой LLM (Claude/GPT/local)
       — текст уже без ПД
    3. Клиент шлёт ответ LLM + mapping_id → /v1/restore → получает текст
       с восстановленными плейсхолдерами

Contract
--------

    POST /v1/anonymize
      Body: {"text": "...", "ttl_seconds": 3600}
      → 200 {
          "masked_text": "...",
          "mapping_id": "<uuid>",
          "count": 3,
          "audit": [{"type": "INN", "count": 1, "placeholders": ["<INN_1>"]}, ...],
          "expires_at_unix": 1234567890
        }

    POST /v1/restore
      Body: {"text": "...", "mapping_id": "<uuid>"}
      → 200 {"restored_text": "..."}
      → 404 если mapping_id не найден / истёк TTL

Auth: Bearer API key (sk-brk-...) — same surface as /v1/chat/completions.

Billing (V2 pivot, BRIEF_v2_pivot.md, CEO 2026-05-14)
-----------------------------------------------------

Pay-per-use: **100 запросов в день бесплатно** (reset 00:00 МСК), затем
**0.02 ₽ / запрос** списываются с депозита. Welcome-кредит для новых
аккаунтов — 100 ₽ (= 5000 платных вызовов), начисляется в signup-flow
отдельной transaction с meta.kind == "welcome_anonymize". При нулевом
балансе после исчерпания дневной квоты — 402 Payment Required с указателем
на ``brikko.ru/app/billing``.

``/v1/restore`` намеренно **БЕСПЛАТНО** и не считается в дневной квоте:
клиент уже заплатил за маскинг при создании mapping_id, restore — просто
Redis lookup поверх него.

Подробности — ``voltari_gateway/billing/anonymize_billing.py``.
"""

from __future__ import annotations

import time
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from voltari_gateway.auth.middleware import AuthPrincipal, get_redis, require_api_key
from voltari_gateway.billing.anonymize_billing import (
    REJECT_ACCOUNT_NOT_FOUND,
    REJECT_QUOTA_EXCEEDED,
    TOPUP_URL,
    check_and_charge_anonymize,
)
from voltari_gateway.db.session import get_db
from voltari_gateway.pii import (
    PiiMapping,
    PiiMappingStore,
    audit_summary,
    mask_text,
    unmask_text,
)
from voltari_gateway.utils.logging import get_logger

router = APIRouter()
log = get_logger(__name__)

DEFAULT_TTL_SECONDS = 3600
MAX_TTL_SECONDS = 86400  # 24 hours — больше нет смысла, текст устаревает.
MAX_TEXT_BYTES = 1_000_000  # 1 MB — защита от DoS через гигантский body.


class AnonymizeRequest(BaseModel):
    text: str = Field(..., min_length=1)
    ttl_seconds: int | None = Field(
        default=DEFAULT_TTL_SECONDS,
        ge=60,
        le=MAX_TTL_SECONDS,
        description="Сколько секунд хранить mapping в Redis (default 3600).",
    )

    model_config = {"extra": "ignore"}


class AnonymizeAuditEntry(BaseModel):
    type: str
    count: int
    placeholders: list[str]


class AnonymizeResponse(BaseModel):
    masked_text: str
    mapping_id: str
    count: int
    audit: list[AnonymizeAuditEntry]
    expires_at_unix: int


class RestoreRequest(BaseModel):
    text: str = Field(..., min_length=1)
    mapping_id: str = Field(..., min_length=8, max_length=128)

    model_config = {"extra": "ignore"}


class RestoreResponse(BaseModel):
    restored_text: str


@router.post(
    "/anonymize",
    response_model=AnonymizeResponse,
    summary="Маскирует ПД в тексте, возвращает плейсхолдеры и mapping_id",
)
async def anonymize(
    body: AnonymizeRequest,
    principal: Annotated[AuthPrincipal, Depends(require_api_key)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AnonymizeResponse | JSONResponse:
    """Маскирует все известные категории ПД (ФИО, ИНН, СНИЛС, ОГРН, ОГРНИП,
    паспорт, телефон, email, IP, банковские карты) и сохраняет mapping
    в Redis под `mapping_id` с TTL.

    Использование на клиенте:
        r = brikko.anonymize("Иванов Иван, ИНН 7707083893, +7 999 123 4567")
        # r.masked_text → "<NAME_1>, ИНН <INN_1>, <PHONE_1>"
        # отправляешь masked_text в любую LLM
        llm_resp = openai.chat.completions.create(...)
        # потом восстанавливаешь
        final = brikko.restore(llm_resp.text, r.mapping_id)
    """
    if len(body.text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Text exceeds {MAX_TEXT_BYTES} bytes. Split into chunks.",
        )

    redis = get_redis()
    if redis is None:
        # Redis underlies both billing (daily counter) and mapping (TTL store).
        # No point partially proceeding — fail fast with 503.
        raise HTTPException(
            status_code=503,
            detail="Mapping store temporarily unavailable. Try again in a moment.",
        )

    # --- Billing gate (V2 pivot) -------------------------------------------
    # Free 100/day → 0.02 ₽ from balance → 402 with topup hint. The debit
    # (if any) lands inside our caller-commit AsyncSession; we commit it
    # explicitly below before sending the response so the client never
    # sees a "charged but no mapping" outcome under a server crash.
    allowed, reject_reason = await check_and_charge_anonymize(
        account_id=principal.account_id,
        redis=redis,
        db=db,
    )
    if not allowed:
        if reject_reason == REJECT_QUOTA_EXCEEDED:
            return JSONResponse(
                status_code=402,
                content={
                    "error": "quota_exceeded",
                    "message": (f"Free quota 100/day exhausted. Top up at {TOPUP_URL}"),
                    "topup_url": TOPUP_URL,
                },
            )
        if reject_reason == REJECT_ACCOUNT_NOT_FOUND:
            # Should not happen — require_api_key resolved the account row.
            raise HTTPException(status_code=401, detail="Account not found.")
        # Future reject reasons → generic 402.
        return JSONResponse(
            status_code=402,
            content={"error": reject_reason or "billing_error"},
        )

    mapping = PiiMapping()
    masked = mask_text(body.text, mapping)

    store = PiiMappingStore(redis)
    mapping_id = uuid.uuid4().hex
    ttl = body.ttl_seconds or DEFAULT_TTL_SECONDS

    if not mapping.is_empty():
        await store.save(mapping_id, mapping, ttl_seconds=ttl)

    # Commit the billing debit (no-op if request fell inside the free quota
    # — the session has no pending changes in that case). Done AFTER the
    # mapping save so a Redis failure on save doesn't leave the client
    # charged for a mapping they can't restore.
    await db.commit()

    expires = int(time.time()) + ttl
    # Group placeholders by type. PiiAuditEntry holds only (pii_type, count)
    # — placeholders we derive from the mapping itself, parsing the
    # ``<TYPE_N>`` shape.
    audit_entries = audit_summary(mapping)
    placeholders_by_type: dict[str, list[str]] = {}
    for placeholder in mapping.reverse:
        # Placeholder shape: <TYPE_N>. Strip brackets and split on the LAST '_'
        # so multi-underscore types (e.g. <PHONE_NUM_1>) split correctly.
        inner = placeholder.strip("<>")
        t = inner.rsplit("_", 1)[0] if "_" in inner else inner
        placeholders_by_type.setdefault(t, []).append(placeholder)

    audit_payload = [
        AnonymizeAuditEntry(
            type=e.pii_type,
            count=e.count,
            placeholders=sorted(placeholders_by_type.get(e.pii_type, [])),
        )
        for e in audit_entries
    ]

    log.info(
        "anonymize_ok",
        account_id=str(principal.account_id),
        api_key_id=str(principal.api_key_id),
        mapping_id=mapping_id,
        pii_count=mapping.size,
        text_len=len(body.text),
    )

    return AnonymizeResponse(
        masked_text=masked,
        mapping_id=mapping_id if not mapping.is_empty() else "",
        count=mapping.size,
        audit=audit_payload,
        expires_at_unix=expires,
    )


@router.post(
    "/restore",
    response_model=RestoreResponse,
    summary="Восстанавливает ПД в тексте, используя сохранённый mapping_id",
)
async def restore(
    body: RestoreRequest,
    principal: Annotated[AuthPrincipal, Depends(require_api_key)],
) -> RestoreResponse:
    """Заменяет плейсхолдеры (`<NAME_1>`, `<INN_1>` и т.д.) обратно на оригинальные
    значения из сохранённого ранее mapping_id.

    404, если mapping_id не существует или истёк TTL.

    Идемпотентно: повторный вызов с тем же входом вернёт идентичный результат
    до тех пор, пока mapping живёт в Redis.

    Биллинг: **бесплатно**. Клиент уже оплатил соответствующий /v1/anonymize
    (либо квотой, либо 0.02 ₽), restore — просто Redis-lookup поверх
    сохранённого mapping. Не считается в дневной квоте 100 запросов.
    """
    redis = get_redis()
    if redis is None:
        raise HTTPException(
            status_code=503,
            detail="Mapping store temporarily unavailable.",
        )
    store = PiiMappingStore(redis)
    mapping = await store.load(body.mapping_id)

    if mapping.is_empty():
        raise HTTPException(
            status_code=404,
            detail=(
                f"mapping_id '{body.mapping_id}' not found or expired. "
                "Call /v1/anonymize again to create a fresh mapping."
            ),
        )

    restored = unmask_text(body.text, mapping)

    log.info(
        "restore_ok",
        account_id=str(principal.account_id),
        api_key_id=str(principal.api_key_id),
        mapping_id=body.mapping_id,
        text_len=len(body.text),
    )

    return RestoreResponse(restored_text=restored)
