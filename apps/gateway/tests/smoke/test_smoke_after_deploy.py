"""QA P0-1 + P0-8 — Smoke-after-deploy.

Прогоняется ПОСЛЕ каждого deploy (см. .github/workflows/smoke.yml).
Цель: за 60s найти то, что было бы deal-breaker'ом — wget vs curl,
NEXT_PUBLIC ушёл в моки, prerender валится, dual-auth не пашет, миграции
не докатились.

Контракт:
    - Принимает ENV `VOLTARI_SMOKE_BASE_URL` (например, `https://api.brikko.ru`).
    - Принимает ENV `VOLTARI_SMOKE_DATABASE_URL` для verify-email shortcut'а.
    - Прогоняет ~12 шагов в фиксированном порядке (signup → verify-via-DB →
      login → keys → chat → balance → usage → logout → revoked-check).
    - Если ХОТЬ ОДИН шаг fail — exit non-zero. Deploy откатывается через
      G-поток (см. .github/workflows/deploy.yml rollback step).

Зависимости:
    - `httpx` (уже в pyproject)
    - `psycopg[binary]` или `asyncpg` для verify-email shortcut'а

Тесты упорядочены через pytest-ordering — каждый шаг зависит от
предыдущего (state-машина test'а — это компромисс ради короткого
конца к концу). Если шаг 1 fail — все остальные skip'нутся.

Запуск:
    VOLTARI_SMOKE_BASE_URL=http://localhost:8000 \\
    VOLTARI_SMOKE_DATABASE_URL=postgresql://voltari:voltari@localhost:5432/voltari \\
    pytest apps/gateway/tests/smoke/ -v
"""

from __future__ import annotations

import os
import time
import uuid
from typing import ClassVar

import httpx
import pytest

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = os.environ.get("VOLTARI_SMOKE_BASE_URL", "http://localhost:8000").rstrip("/")
DB_URL = os.environ.get("VOLTARI_SMOKE_DATABASE_URL", "")
SKIP_AUTH_FLOW = os.environ.get("VOLTARI_SMOKE_SKIP_AUTH_FLOW", "").lower() in ("1", "true", "yes")
PASSWORD = "SmokeTestPassword123456789"
TEST_EMAIL = f"smoke-{uuid.uuid4().hex[:12]}@smoke.test"
HEADERS = {"X-Requested-With": "voltari-web", "Content-Type": "application/json"}


# State shared across ordered tests (compromise — see module docstring).
class _SmokeState:
    user_id: str | None = None
    account_id: str | None = None
    # ClassVar — это shared state между всеми smoke-тестами в одном run'е
    # (последовательная цепочка signup → login → keys → chat). Mutable shared
    # default здесь намеренный, не подводный камень.
    cookies: ClassVar[dict[str, str]] = {}
    api_key_id: str | None = None
    api_key_plaintext: str | None = None
    balance_after_topup: int | None = None


STATE = _SmokeState()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def http() -> httpx.Client:
    """Sync httpx client. Real-world smoke — мы НЕ тестируем здесь конкурентность."""
    with httpx.Client(base_url=BASE_URL, timeout=10.0, follow_redirects=False) as c:
        yield c


def _verify_email_via_db(email: str) -> None:
    """SHORTCUT: вместо парсинга email-token'а — UPDATE в БД.

    В smoke environment у нас нет SMTP-сервера, и читать email-логи
    хрупко. Альтернатива — SET email_verified=true напрямую. Это валидно
    для smoke-after-deploy: мы тестируем что login-flow работает, а
    email-verify ПУТЬ покрыт unit-тестом ``test_email_verification.py``.
    """
    if not DB_URL:
        pytest.skip("VOLTARI_SMOKE_DATABASE_URL не задан — нельзя verify через БД")

    # Используем psycopg для прямого SQL — без alembic, без ORM.
    psycopg = pytest.importorskip("psycopg")

    sync_url = DB_URL.replace("postgresql+asyncpg://", "postgresql://")
    with psycopg.connect(sync_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE users SET email_verified = true, verification_token = NULL WHERE email = %s",
            (email,),
        )


# ---------------------------------------------------------------------------
# Smoke steps. Ordered execution (pytest -p no:randomly или alphabetical).
# Имена начинаются с test_NN_... чтобы pytest гарантированно запускал в
# алфавитном порядке.
# ---------------------------------------------------------------------------


def test_01_healthz_returns_200(http: httpx.Client) -> None:
    """Gateway alive — самая базовая проверка."""
    r = http.get("/healthz")
    assert r.status_code == 200, f"/healthz != 200: {r.status_code} {r.text}"
    body = r.json()
    assert body.get("status") == "ok", f"/healthz status != ok: {body}"


def test_02_health_ready_returns_200_or_503(http: httpx.Client) -> None:
    """/health/ready — DB+Redis check.

    200 = ready, 503 = down (DB/Redis недоступен). 200 — обязательное
    условие для прохождения деплоя; 503 — сразу exit-non-zero.
    """
    r = http.get("/health/ready")
    if r.status_code == 503:
        pytest.fail(f"/health/ready 503: {r.json()}. DB или Redis недоступны после deploy.")
    assert r.status_code == 200, f"/health/ready неожиданный {r.status_code}: {r.text}"
    body = r.json()
    assert body.get("status") in ("ready", "degraded"), f"unexpected status: {body}"


def test_03_v1_models_requires_auth(http: httpx.Client) -> None:
    """GET /v1/models без auth → 401 (не 200, не 500)."""
    r = http.get("/v1/models")
    assert r.status_code == 401, (
        f"/v1/models без auth должен быть 401, получили {r.status_code}: {r.text}"
    )


def test_03a_cors_preflight_login_allows_csrf_header(http: httpx.Client) -> None:
    """OPTIONS /v1/auth/login preflight с x-csrf-token в Access-Control-Request-Headers.

    Регрессионный тест на TD-040 — браузер делал OPTIONS preflight перед
    POST с X-CSRF-Token, backend отклонял `Disallowed CORS headers`,
    реальный POST вообще не уходил. Login через UI выдавал generic
    "Network error" — никакого 401/422 не было.

    Должно быть:
        OPTIONS → 200
        access-control-allow-headers содержит x-csrf-token
        access-control-allow-credentials: true (cookie-flow)
        access-control-allow-origin совпадает с Origin (не *)

    URL: используем фронт-app (не любой) — точная регрессия.
    """
    origin = "https://brikko.ru"
    r = http.request(
        "OPTIONS",
        "/v1/auth/login",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-csrf-token,content-type",
        },
    )
    assert r.status_code == 200, (
        f"CORS preflight /v1/auth/login: {r.status_code}, expected 200. "
        f"Probably TD-040 regression — X-CSRF-Token не в allow_headers."
    )

    # Проверяем allow-headers (case-insensitive по HTTP spec).
    allow_headers = (r.headers.get("access-control-allow-headers") or "").lower()
    assert "x-csrf-token" in allow_headers, (
        f"X-CSRF-Token не в allow-headers: {allow_headers!r}. "
        f"TD-040: backend CORS не обновлён под double-submit CSRF."
    )
    assert "content-type" in allow_headers, f"Content-Type не в allow-headers: {allow_headers!r}"

    # cookie flow требует allow_credentials=true
    allow_creds = (r.headers.get("access-control-allow-credentials") or "").lower()
    assert allow_creds == "true", (
        f"access-control-allow-credentials != true ({allow_creds!r}). "
        f"Без этого браузер не отправит cookie."
    )

    # Origin echo (не *), потому что allow_credentials=true несовместим с *
    allow_origin = r.headers.get("access-control-allow-origin", "")
    assert allow_origin == origin, (
        f"access-control-allow-origin: {allow_origin!r} != {origin!r}. "
        f"Backend не должен отвечать * при credentials=include."
    )


def test_03b_csrf_endpoint_returns_token_and_cookie(http: httpx.Client) -> None:
    """GET /v1/auth/csrf → 200 + cookie vlt_csrf + body с CSRF token.

    Это первый шаг double-submit CSRF flow: фронт получает токен,
    backend устанавливает cookie с тем же токеном. На POST фронт шлёт
    оба, backend сравнивает.

    Регрессия: если /v1/auth/csrf вернёт 404 / 500 / отсутствует
    Set-Cookie — весь login flow обвалится беззвучно (с генерик-error
    в UI).
    """
    r = http.get(
        "/v1/auth/csrf",
        headers={"Origin": "https://brikko.ru", "X-Requested-With": "voltari-web"},
    )
    assert r.status_code == 200, (
        f"/v1/auth/csrf: {r.status_code} {r.text}. Endpoint должен быть доступен без auth."
    )

    # Cookie set
    assert "vlt_csrf" in r.cookies or "vlt_csrf" in (r.headers.get("set-cookie") or ""), (
        f"vlt_csrf cookie не установлено. Set-Cookie: {r.headers.get('set-cookie')!r}"
    )

    # Body содержит token
    try:
        body = r.json()
    except Exception:
        pytest.fail(f"/v1/auth/csrf не JSON: {r.text!r}")

    token = body.get("token") or body.get("csrf_token")
    assert token, f"/v1/auth/csrf body без token: {body!r}"
    assert len(token) >= 16, f"CSRF token подозрительно короткий: {token!r}"


def test_03c_full_csrf_flow_login_succeeds(http: httpx.Client) -> None:
    """End-to-end CSRF: GET csrf → cookie + token → POST login с обоими.

    Это интеграционный proof что double-submit реально работает на
    деплоированном инстансе. Поток G CSRF backend tests шли через
    TestClient — он не делает CORS preflight, а тут мы дублируем
    real-browser sequence.

    Не падаем если signup не сработает (smoke env может быть rate-limit'ed),
    но падаем если CSRF flow откинет валидную пару → значит protocol broken.
    """
    if SKIP_AUTH_FLOW:
        pytest.skip()

    origin = "https://brikko.ru"

    # 1. GET csrf → берём token + cookie
    r1 = http.get(
        "/v1/auth/csrf",
        headers={"Origin": origin, "X-Requested-With": "voltari-web"},
    )
    if r1.status_code != 200:
        pytest.skip(f"csrf endpoint not ready: {r1.status_code}")

    csrf_token = r1.json().get("token") or r1.json().get("csrf_token")
    csrf_cookie = r1.cookies.get("vlt_csrf")
    if not (csrf_token and csrf_cookie):
        pytest.skip("csrf token/cookie не выданы — скорее всего backend ещё стартует")

    # 2. POST signup с CSRF header + cookie. Используем уникальный email чтобы
    #    не конфликтовать с другими smoke-runs.
    csrf_email = f"smoke-csrf-{uuid.uuid4().hex[:12]}@smoke.test"
    r2 = http.post(
        "/v1/auth/signup",
        json={"email": csrf_email, "password": PASSWORD},
        cookies={"vlt_csrf": csrf_cookie},
        headers={
            "Origin": origin,
            "Content-Type": "application/json",
            "X-CSRF-Token": csrf_token,
            "X-Requested-With": "voltari-web",
        },
    )

    # signup может быть 200 / 409 (existing) / 429 (rate-limited).
    # 403 CSRF mismatch — провал теста: protocol сломан.
    assert r2.status_code != 403, (
        f"CSRF reject на валидной паре: {r2.status_code} {r2.text}. "
        f"token={csrf_token[:8]!r}... cookie={csrf_cookie[:8]!r}... "
        f"Backend csrf.verify_csrf() unexpectedly rejected."
    )

    # 3. Now POST без CSRF → должен быть 403 (proves CSRF реально enforced).
    r3 = http.post(
        "/v1/auth/signup",
        json={"email": f"no-csrf-{uuid.uuid4().hex[:8]}@smoke.test", "password": PASSWORD},
        headers={"Origin": origin, "Content-Type": "application/json"},
    )
    # Если 403 — отлично, CSRF работает.
    # Если 200 / 409 — CSRF middleware не enforced на этот endpoint,
    # это TD-036 (legacy fallback ещё активен). Принимаем но логируем.
    if r3.status_code == 403:
        return  # all good, CSRF strictly enforced

    # Через legacy X-Requested-With path POST может пройти (TD-036). Это
    # известное послабление до окончания миграции FE.
    if r3.status_code in (200, 201, 409):
        # Пометим как warning через pytest stdout — не fail, но видно.
        print(
            "WARN: CSRF не enforced без X-CSRF-Token (TD-036 legacy still active). "
            "После окончания FE migration — удалить legacy ветку, тогда тут будет 403."
        )
        return

    # Иначе — что-то пошло не так:
    pytest.fail(
        f"Unexpected status without CSRF token: {r3.status_code} {r3.text}. "
        f"Should be 403 (strict) или 200/409 (legacy fallback)."
    )


def test_04_signup_creates_user(http: httpx.Client) -> None:
    """POST /v1/auth/signup — создание пользователя."""
    if SKIP_AUTH_FLOW:
        pytest.skip("VOLTARI_SMOKE_SKIP_AUTH_FLOW=1 — auth-flow тесты пропущены")

    r = http.post(
        "/v1/auth/signup",
        json={"email": TEST_EMAIL, "password": PASSWORD},
        headers=HEADERS,
    )
    assert r.status_code == 200, f"signup failed: {r.status_code} {r.text}"
    body = r.json()
    assert "user_id" in body
    STATE.user_id = body["user_id"]


def test_05_verify_email_via_db_shortcut() -> None:
    """SHORTCUT: emailverified=true через прямой UPDATE."""
    if SKIP_AUTH_FLOW:
        pytest.skip()
    if STATE.user_id is None:
        pytest.skip("предыдущий шаг провалился")

    _verify_email_via_db(TEST_EMAIL)


def test_06_login_returns_cookies(http: httpx.Client) -> None:
    """POST /v1/auth/login → 200 + Set-Cookie vlt_access + vlt_refresh."""
    if SKIP_AUTH_FLOW:
        pytest.skip()
    if STATE.user_id is None:
        pytest.skip()

    r = http.post(
        "/v1/auth/login",
        json={"email": TEST_EMAIL, "password": PASSWORD},
        headers=HEADERS,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    body = r.json()
    assert "account_id" in body
    STATE.account_id = body["account_id"]

    # Сохраняем cookies для последующих запросов.
    for cookie_name in ("vlt_access", "vlt_refresh"):
        if cookie_name in r.cookies:
            STATE.cookies[cookie_name] = r.cookies[cookie_name]
    assert "vlt_access" in STATE.cookies, "vlt_access cookie not set"
    assert "vlt_refresh" in STATE.cookies, "vlt_refresh cookie not set"


def test_07_account_get_with_cookies(http: httpx.Client) -> None:
    """GET /v1/account с cookie — 200, returns own account."""
    if SKIP_AUTH_FLOW:
        pytest.skip()
    if not STATE.cookies:
        pytest.skip()

    r = http.get(
        "/v1/account",
        cookies=STATE.cookies,
        headers={"X-Requested-With": "voltari-web"},
    )
    assert r.status_code == 200, f"/v1/account: {r.status_code} {r.text}"
    body = r.json()
    assert body.get("id") == STATE.account_id


def test_08_create_api_key(http: httpx.Client) -> None:
    """POST /v1/keys — создаём ключ для bearer-flow."""
    if SKIP_AUTH_FLOW:
        pytest.skip()
    if not STATE.cookies:
        pytest.skip()

    r = http.post(
        "/v1/keys",
        json={"name": "smoke-key", "scope": "write"},
        cookies=STATE.cookies,
        headers=HEADERS,
    )
    assert r.status_code == 201, f"create key: {r.status_code} {r.text}"
    body = r.json()
    STATE.api_key_id = body["id"]
    STATE.api_key_plaintext = body["full_key"]
    assert STATE.api_key_plaintext.startswith("sk-vt-")


def test_09_chat_completions_with_bearer(http: httpx.Client) -> None:
    """POST /v1/chat/completions с Bearer — успех + balance уменьшился.

    Используем cheapest model в каталоге (deepseek или yandex-lite).
    Если в smoke-окружении нет настроенных провайдеров — тест должен
    либо успешно сходить через mock-stub (smoke-stub-провайдер), либо
    skip'нуться с понятным сообщением.
    """
    if SKIP_AUTH_FLOW:
        pytest.skip()
    if not STATE.api_key_plaintext:
        pytest.skip()

    # Узнаём balance ДО.
    balance_before_resp = http.get(
        "/v1/billing/balance",
        headers={"Authorization": f"Bearer {STATE.api_key_plaintext}"},
    )
    if balance_before_resp.status_code != 200:
        pytest.skip(f"balance pre-check failed: {balance_before_resp.text}")
    balance_before = balance_before_resp.json()["balance_kopecks"]

    r = http.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-5.4-mini",  # самая дешёвая OpenAI model
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 10,
            "failover": True,
        },
        headers={
            "Authorization": f"Bearer {STATE.api_key_plaintext}",
            "Content-Type": "application/json",
        },
    )

    if r.status_code == 402:
        pytest.skip("smoke account без баланса — нельзя протестировать chat")
    if r.status_code == 502:
        # Все провайдеры down ИЛИ не настроены — это критично, но не смоук-fail.
        # smoke fixture для этого: pre-prod должна иметь хотя бы 1 провайдера.
        pytest.fail(f"chat 502 — провайдеры недоступны: {r.text}")
    assert r.status_code == 200, f"chat: {r.status_code} {r.text}"

    # Проверяем balance ПОСЛЕ — должен уменьшиться.
    balance_after_resp = http.get(
        "/v1/billing/balance",
        headers={"Authorization": f"Bearer {STATE.api_key_plaintext}"},
    )
    assert balance_after_resp.status_code == 200
    balance_after = balance_after_resp.json()["balance_kopecks"]
    assert balance_after < balance_before, (
        f"balance не уменьшился после chat! before={balance_before}, after={balance_after}. "
        f"P0-4 регрессия — chat не списывает деньги!"
    )
    STATE.balance_after_topup = balance_after


def test_10_usage_endpoint_with_cookie(http: httpx.Client) -> None:
    """GET /v1/usage с cookie — 200, dual-auth работает (TD-031 регрессия)."""
    if SKIP_AUTH_FLOW:
        pytest.skip()
    if not STATE.cookies:
        pytest.skip()

    r = http.get(
        "/v1/usage",
        cookies=STATE.cookies,
        headers={"X-Requested-With": "voltari-web"},
    )
    assert r.status_code == 200, (
        f"/v1/usage с cookie: {r.status_code} {r.text}. TD-031 регрессия — dual-auth не работает!"
    )
    body = r.json()
    assert "totals" in body


def test_11_logout_clears_cookies(http: httpx.Client) -> None:
    """POST /v1/auth/logout → 200; subsequent /v1/account → 401."""
    if SKIP_AUTH_FLOW:
        pytest.skip()
    if not STATE.cookies:
        pytest.skip()

    r = http.post(
        "/v1/auth/logout",
        cookies=STATE.cookies,
        headers={"X-Requested-With": "voltari-web"},
    )
    assert r.status_code == 200

    # Subsequent /v1/account с теми же cookies — должны быть очищены.
    # Httpx сохраняет cookies — заберём из response.
    cookies_after = dict(r.cookies)
    # Если backend отправил Set-Cookie с пустым value → use тех cookies.
    final_cookies = {**STATE.cookies, **cookies_after}
    # Удаляем те, у которых value стало пустым
    final_cookies = {k: v for k, v in final_cookies.items() if v}

    # Если cookies реально очищены — final_cookies может быть пустым,
    # тогда /v1/account будет без auth → 401.
    r2 = http.get(
        "/v1/account",
        cookies=final_cookies,
        headers={"X-Requested-With": "voltari-web"},
    )
    assert r2.status_code == 401, f"after logout /v1/account должно быть 401, got {r2.status_code}"


def test_12_revoked_api_key_returns_401(http: httpx.Client) -> None:
    """Через прямой UPDATE revoke ключ → bearer call → 401."""
    if SKIP_AUTH_FLOW:
        pytest.skip()
    if not STATE.api_key_plaintext or not DB_URL:
        pytest.skip()

    # Revoke через DB.
    psycopg = pytest.importorskip("psycopg")
    sync_url = DB_URL.replace("postgresql+asyncpg://", "postgresql://")
    with psycopg.connect(sync_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE api_keys SET status = 'revoked', revoked_at = NOW() WHERE id = %s",
            (STATE.api_key_id,),
        )

    # Дать кешу 1s на инвалидацию (если invalidate_cache_for_key не сработала
    # из-за того что мы revoke'нули через прямой SQL мимо API). На smoke
    # этого хватит, на production — есть AUTH_CACHE_TTL_SECONDS=60s в
    # config'е, что значит ключ может всё ещё работать первую минуту через
    # cache. Это известный edge — для smoke принимаем 401 ИЛИ дожидаемся.
    time.sleep(2)

    r = http.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-5.4-mini",
            "messages": [{"role": "user", "content": "x"}],
            "max_tokens": 1,
        },
        headers={
            "Authorization": f"Bearer {STATE.api_key_plaintext}",
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 401, f"revoked key должен 401, получили {r.status_code}: {r.text}"
