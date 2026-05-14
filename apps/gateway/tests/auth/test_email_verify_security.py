"""Email verification token security — replay / expiry / wrong-email / tamper.

Sprint 4 Поток L (QA implement) — реализация плана Sprint 3 Поток K секции
``test_email_verify_security.py``.

Дополняет существующий ``test_email_verification.py`` (там — pure unit на
serializer / hash). Здесь — end-to-end через ``POST /v1/auth/verify-email``,
с реальным User'ом в БД, чтобы пинить контракты:

* Token consumed once → второй вызов 400.
* Expired token (TTL прошёл, моделируется через max_age=-1 на сериализаторе) → 400.
* Token signed for user_A presented as user_B (через подмену email) → 400.
* Login без verify → 403 ``email_not_verified``.
* HMAC verify (TD-012 closure regression) — фиксируем что сервер требует
  именно HMAC-keyed hash и не принимает плоский SHA-256.
* Tampered token → 400, не 500.
* Empty / garbage / whitespace token → 400.

Все тесты идут через ASGI-клиент (использует существующие fixtures
``client``, ``db``).
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from voltari_gateway.auth.email_verification import (
    generate_verification_token,
    hash_token,
)
from voltari_gateway.auth.password import hash_password
from voltari_gateway.db.models import (
    Account,
    AccountStatus,
    Tariff,
    User,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_PASSWORD = "S0meStrongPasswordForTest!"


async def _seed_unverified_user(db: Any, *, email: str | None = None) -> User:
    """Create an unverified user + signed verification token + DB-stored
    HMAC hash. Returns the User; the plaintext token is on
    ``user._test_plain_token`` (not a real attribute, just sticky on the
    instance for test convenience).
    """
    if email is None:
        email = f"verify-{uuid.uuid4().hex[:10]}@example.com"
    user = User(
        email=email,
        password_hash=hash_password(_PASSWORD),
        email_verified=False,
    )
    db.add(user)
    await db.flush()

    token_plain = generate_verification_token(user.id, email)
    user.verification_token = hash_token(token_plain)

    db.add(
        Account(
            owner_id=user.id,
            name="Verify test",
            balance_kopecks=0,
            tariff=Tariff.PAYG,
            status=AccountStatus.ACTIVE,
            store_prompts=True,
        )
    )
    await db.commit()
    await db.refresh(user)
    user._test_plain_token = token_plain  # type: ignore[attr-defined]
    return user


# ---------------------------------------------------------------------------
# 1) Happy path + replay
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_token_happy_path_then_replay_blocked(client: AsyncClient, db: Any) -> None:
    """Первый verify — 200 verified=True. Второй с тем же токеном — должен
    распознаться как already-verified (200 verified=True welcome=0) ИЛИ
    400 invalid_token. Главное — НЕ создаётся вторая транзакция welcome.
    """
    user = await _seed_unverified_user(db)
    token = user._test_plain_token  # type: ignore[attr-defined]

    r1 = await client.post("/v1/auth/verify-email", json={"token": token})
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1["verified"] is True
    welcome_first = body1["welcome_credit_kop"]

    # Replay.
    r2 = await client.post("/v1/auth/verify-email", json={"token": token})
    # Either 200 (idempotent already-verified path) или 400 (token wiped).
    # Оба валидны; контракт — никакой welcome credit вторично.
    assert r2.status_code in (200, 400), r2.text
    if r2.status_code == 200:
        body2 = r2.json()
        assert body2.get("welcome_credit_kop", 0) == 0
        assert body2.get("verified") is True
    else:
        err = r2.json()["error"]
        assert err["code"] == "invalid_token"
    # На всякий случай: первый welcome был выписан (либо 0 если уже был).
    assert welcome_first >= 0


# ---------------------------------------------------------------------------
# 2) Expired token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_verify_token_rejected_400(client: AsyncClient, db: Any) -> None:
    """Токен с истёкшим max_age (TTL прошёл) → 400 invalid_token.

    Мы не sleep'аем 24 часа — генерим токен через сериализатор с
    timestamp в прошлом. ``URLSafeTimedSerializer.loads(max_age=...)``
    проверяет timestamp signed → SignatureExpired.

    Реалистичный сценарий: пользователь открыл ссылку через 25 часов.
    """
    user = await _seed_unverified_user(db)
    # We don't need to instantiate the serializer ourselves — the simplest
    # way to force expiry deterministically is to set TTL=0 hours in env
    # and let ``_serializer().loads(...)`` raise SignatureExpired on the
    # next tick. ``get_settings()`` is monkey-patched to re-read after the
    # env override below.
    # itsdangerous signs `payload + b'.' + b64(now_ts)` with HMAC.
    # Easiest reliable expiry: rely on signed-but-old-cert approach via
    # patching `time.time` on the module. Use monkeypatch via dependency
    # injection: use `_serializer().loads(token, max_age=-1)` semantics —
    # but since we don't control verify_email's max_age, we simulate by
    # generating the token with a salt+issued_at offset.
    #
    # Simpler approach — sign with a salt matching production but
    # delegate validation behavior tests via the unit test in
    # ``test_email_verification.py``. Here we POST a CLEARLY expired
    # token by abusing the legacy serializer trick: a token signed
    # with a fake stale timestamp is rejected on the server with the
    # configured ``email_verification_ttl_hours``.
    #
    # We'll force expiry by setting the env to TTL=0 hours, then
    # generating a fresh-but-already-stale token. Каждый тест в conftest
    # делает cache_clear на settings, так что наш override живёт только
    # в этом test'е.
    import os

    os.environ["EMAIL_VERIFICATION_TTL_HOURS"] = "0"
    from voltari_gateway import config as cfg

    cfg.get_settings.cache_clear()

    token = generate_verification_token(user.id, user.email)
    # Re-stamp DB hash so a "fresh" verify would otherwise succeed.
    user.verification_token = hash_token(token)
    await db.commit()

    # Wait the smallest possible time (1 second tick) — itsdangerous
    # rounds to seconds. With TTL=0 the token is expired the moment
    # the server checks it.
    import time

    time.sleep(1.1)

    r = await client.post("/v1/auth/verify-email", json={"token": token})

    # Cleanup env override.
    os.environ.pop("EMAIL_VERIFICATION_TTL_HOURS", None)
    cfg.get_settings.cache_clear()

    assert r.status_code == 400, r.text
    err = r.json()["error"]
    assert err["code"] == "invalid_token"
    assert err["type"] == "invalid_request_error"


# ---------------------------------------------------------------------------
# 3) Token signed for user_A used as user_B
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_signed_for_other_user_rejected(client: AsyncClient, db: Any) -> None:
    """Token подписан для user_A. Если user_B (с другой DB-row) предъявит
    этот токен — backend ищет user_id из payload, находит user_A,
    но stored_hash в user_A не совпадает (потому что мы установили
    другому пользователю токен в БД). Должно быть 400.

    Сценарий атаки: украли email A, подписали токен, но в БД hash от
    реального токена A. Нельзя подделать токен без знания HMAC-секрета.
    Здесь же — pin что подделать «свой токен» с user_id чужого ника
    нельзя: stored_hash mismatch.
    """
    user_a = await _seed_unverified_user(db, email="alice-target@example.com")
    user_b = await _seed_unverified_user(db, email="bob-attacker@example.com")

    # Атакующий подписал токен с user_id=A но email=B (или email=A).
    # Backend разрешит payload, но в БД user_a.verification_token уже есть
    # hash настоящего токена user_a. Этот наш «подделанный» — другой
    # bytes — hash mismatch → 400.
    fake_token_for_a = generate_verification_token(user_a.id, user_b.email)
    # ПРИ ЭТОМ настоящий хэш в БД у user_a — от valid token.
    # Серверу не понравится, потому что compare_digest hash(stored) vs hash(fake) → False.
    r = await client.post("/v1/auth/verify-email", json={"token": fake_token_for_a})
    assert r.status_code == 400, r.text
    err = r.json()["error"]
    assert err["code"] == "invalid_token"


# ---------------------------------------------------------------------------
# 4) Login без verify
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unverified_user_cannot_login_403(client: AsyncClient, db: Any) -> None:
    """Login на email_verified=false → 403 ``email_not_verified``.

    Защищает от ошибки в auth-flow: пользователь не должен получить
    cookie до подтверждения email'а — иначе welcome-credit раздаётся
    без подтверждения (массовый абуз).
    """
    user = await _seed_unverified_user(db)
    r = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": _PASSWORD},
    )
    assert r.status_code == 403, r.text
    err = r.json()["error"]
    assert err["code"] == "email_not_verified"


# ---------------------------------------------------------------------------
# 5) HMAC verify regression (TD-012)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plain_sha256_in_db_does_not_validate(client: AsyncClient, db: Any) -> None:
    """Регресс на TD-012 closure: stored_hash должен быть HMAC-SHA256, не
    плоский SHA-256.

    Если кто-то откатит реализацию назад на ``sha256(plaintext).hexdigest()``,
    эта проверка падает: подсунем плоский SHA-256 в БД — реальный
    ``hash_token(plaintext)`` (HMAC) с ним не совпадёт → 400.

    Тест НЕ ходит через приватный API; он переписывает
    ``user.verification_token`` напрямую и проверяет что endpoint
    отвергает.
    """
    user = await _seed_unverified_user(db)
    token = user._test_plain_token  # type: ignore[attr-defined]

    # Подсовываем плоский SHA-256.
    plain_sha = hashlib.sha256(token.encode()).hexdigest()
    user.verification_token = plain_sha
    await db.commit()
    await db.refresh(user)

    r = await client.post("/v1/auth/verify-email", json={"token": token})
    # Если HMAC применён — hash(token) ≠ plain_sha → 400.
    # Если регрессия (вернули plain SHA) — 200. Pin: должен быть 400.
    assert r.status_code == 400, (
        f"Регрессия TD-012: stored_hash должен быть HMAC, не plain SHA-256. "
        f"Status={r.status_code}, body={r.text[:200]}"
    )


# ---------------------------------------------------------------------------
# 6) Tampered / garbage tokens — never 500
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_token",
    [
        "totally-fake.not.itsdangerous",
        "",
        " ",
        "a",
        "..",
        "x" * 10_000,  # 10KB token
        "valid.start.but.tampered.signature.zzzzzz",
        "\x00\x01\x02",  # control bytes
    ],
)
async def test_tampered_or_empty_token_returns_400_not_500(
    client: AsyncClient, db: Any, bad_token: str
) -> None:
    """Любая ненормальная нагрузка на verify-email — 400, никогда 500.

    Защищает обработчик от паники на уровне serializer / itsdangerous
    при странных payload'ах. Сейчас ``verify_verification_token``
    ловит ``Exception`` и возвращает None — endpoint translates в 400.
    """
    r = await client.post("/v1/auth/verify-email", json={"token": bad_token})
    assert r.status_code == 400, (r.status_code, r.text[:200])
    body = r.json()
    err = body["error"]
    # Может быть как invalid_token (если пустой/garbage прошёл вглубь),
    # так и invalid_body (если pydantic поймал на min_length).
    assert err["code"] in ("invalid_token", "invalid_body")
    assert err["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_token_field_missing_returns_400(client: AsyncClient) -> None:
    """``{"token": null}`` или отсутствующий — 400 validation error."""
    r = await client.post("/v1/auth/verify-email", json={})
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["type"] == "invalid_request_error"
