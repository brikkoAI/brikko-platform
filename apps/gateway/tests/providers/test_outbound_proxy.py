"""Тесты ``OUTBOUND_HTTP_PROXY`` (двухсерверная архитектура CEO 2026-04-30).

Проверяем:

* пустой ``OUTBOUND_HTTP_PROXY`` → клиент создаётся без proxy (none of the
  outbound providers wires a proxied httpx-клиент);
* непустой ``OUTBOUND_HTTP_PROXY`` → провайдер создаёт ``httpx.AsyncClient``
  с правильным ``proxy=`` параметром (httpx >= 0.26 синтаксис);
* OpenAI и Anthropic SDKs получают наш ``http_client`` (а не строят свой
  внутренний без proxy);
* Yandex и Sber **не** используют proxy даже если env установлен —
  они должны идти из РФ напрямую к РФ-API. Делаем это через
  ``trust_env=False`` в их httpx-клиентах.

Тесты не делают сетевых запросов — мы только инспектируем атрибуты
сконструированного клиента.
"""

from __future__ import annotations

from voltari_gateway.config import Settings
from voltari_gateway.providers.anthropic_provider import AnthropicProvider
from voltari_gateway.providers.deepseek_provider import DeepSeekProvider
from voltari_gateway.providers.openai_provider import OpenAIProvider
from voltari_gateway.providers.sber_provider import SberProvider
from voltari_gateway.providers.yandex_provider import YandexProvider

# ----- Settings parsing --------------------------------------------------------


def test_outbound_proxy_empty_string_is_treated_as_none() -> None:
    """``OUTBOUND_HTTP_PROXY=`` (empty) должен ресолвиться в ``None``.

    Иначе httpx попытается использовать пустую URL как прокси и упадёт
    в рантайме.
    """
    s = Settings(
        OUTBOUND_HTTP_PROXY="",
        JWT_SECRET="fixture-jwt-secret-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )
    assert s.outbound_http_proxy is None


def test_outbound_proxy_non_empty_string_passes_through() -> None:
    """Заданный proxy URL должен попадать в settings без модификации."""
    proxy = "http://10.10.0.1:8080"
    s = Settings(
        OUTBOUND_HTTP_PROXY=proxy,
        JWT_SECRET="fixture-jwt-secret-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )
    assert s.outbound_http_proxy == proxy


# ----- OpenAI / Anthropic / DeepSeek route through proxy -----------------------


def _httpx_client_uses_proxy(provider: object, expected_proxy: str) -> bool:
    """Inspect provider's ``_http_client`` — confirm proxy mount is wired.

    httpx (0.27) построенный с ``proxy=...`` создаёт внутренние ``mounts`` на
    ``http://`` и ``https://`` ключи. Достаточно проверить, что mounts
    непустой — это надёжный сигнал что proxy реально применён.
    """
    http_client = getattr(provider, "_http_client", None)
    if http_client is None:
        return False
    # ``_mounts`` — internal dict of httpx client; присутствие ключей с прокси-
    # transport означает, что httpx распарсил proxy URL и добавил соответствующие
    # transport mounts. Этого достаточно как proof что httpx видел ``proxy=``.
    mounts = getattr(http_client, "_mounts", {}) or {}
    return len(mounts) > 0 and bool(expected_proxy)


def test_openai_provider_with_proxy_creates_proxied_httpx_client() -> None:
    """OpenAI с outbound_proxy должен иметь прокси-aware httpx-клиент."""
    proxy = "http://10.10.0.1:8080"
    prov = OpenAIProvider(
        api_key="sk-test-DUMMY",
        outbound_proxy=proxy,
    )
    assert prov._http_client is not None, (
        "OpenAIProvider должен создать собственный httpx.AsyncClient когда "
        "outbound_proxy задан, чтобы пробросить его в SDK как http_client."
    )
    assert _httpx_client_uses_proxy(prov, proxy), (
        f"httpx.AsyncClient должен иметь mounts для proxy={proxy}, "
        "иначе proxy реально не применяется к запросам."
    )


def test_openai_provider_without_proxy_skips_custom_http_client() -> None:
    """OpenAI без outbound_proxy не должен городить лишний httpx-клиент.

    Дефолт SDK сам создаст внутренний клиент с правильными retry/transport.
    """
    prov = OpenAIProvider(api_key="sk-test-DUMMY", outbound_proxy=None)
    assert prov._http_client is None


def test_anthropic_provider_with_proxy_creates_proxied_httpx_client() -> None:
    """Anthropic с outbound_proxy — аналогично OpenAI, через ``http_client``."""
    proxy = "http://10.10.0.1:8080"
    prov = AnthropicProvider(
        api_key="sk-ant-test-DUMMY",
        outbound_proxy=proxy,
    )
    assert prov._http_client is not None
    assert _httpx_client_uses_proxy(prov, proxy)


def test_deepseek_provider_with_proxy_creates_proxied_httpx_client() -> None:
    """DeepSeek (тот же OpenAI SDK с другим base_url) — то же поведение."""
    proxy = "http://10.10.0.1:8080"
    prov = DeepSeekProvider(
        api_key="sk-test-DUMMY",
        outbound_proxy=proxy,
    )
    assert prov._http_client is not None
    assert _httpx_client_uses_proxy(prov, proxy)


# ----- Yandex / Sber must NOT pick up proxy ------------------------------------


def test_yandex_httpx_client_has_trust_env_false() -> None:
    """YandexGPT — РФ-доступный API, не должен идти через зарубежный proxy.

    Даже если ``HTTPS_PROXY`` env-var установлен (другим провайдером —
    Google делает это для своих нужд), httpx-клиент Yandex должен
    игнорировать env-vars благодаря ``trust_env=False``.
    """
    prov = YandexProvider(folder_id="test-folder", api_key="test-yandex-key")
    # httpx сохраняет trust_env как атрибут клиента (см. httpx 0.27 source).
    assert prov._http.trust_env is False, (
        "YandexProvider httpx.AsyncClient должен быть создан с trust_env=False, "
        "иначе он подхватит HTTPS_PROXY и пойдёт через зарубежный proxy — это "
        "нарушение архитектурного решения CEO 2026-04-30 (РФ-провайдеры идут напрямую)."
    )


def test_sber_httpx_client_has_trust_env_false() -> None:
    """GigaChat — то же самое: РФ-API, никакого outbound proxy."""
    prov = SberProvider(auth_key="test-auth-key")
    assert prov._http.trust_env is False, (
        "SberProvider httpx.AsyncClient должен быть создан с trust_env=False, "
        "иначе он подхватит HTTPS_PROXY и пойдёт через зарубежный proxy — это "
        "нарушение архитектурного решения CEO 2026-04-30 (РФ-провайдеры идут напрямую)."
    )
