"""Locust load test для Voltari gateway — Python-альтернатива k6.

Запуск:
    locust -f infra/load-tests/locustfile.py --host https://staging-api.brikko.ru
    # Открыть http://localhost:8089 → задать users + spawn rate

Headless для CI:
    locust -f infra/load-tests/locustfile.py \
           --host https://staging-api.brikko.ru \
           --headless -u 50 -r 10 --run-time 10m \
           --csv=baseline-locust

Эквиваленты сценариев:
    LT-1 baseline:  -u 50  -r 10  --run-time 10m   (≈200 RPM)
    LT-2 spike:     -u 250 -r 250 --run-time 6m
    LT-3 stress:    -u 1000 -r 100 --run-time 15m
    LT-4 soak:      -u 50 -r 5 --run-time 24h
    LT-5 mixed:     -u 50 -r 10 --run-time 30m
"""
from __future__ import annotations

import os
import random
from locust import HttpUser, between, task, events

API_KEY = os.environ.get("VOLTARI_API_KEY")
if not API_KEY:
    raise RuntimeError("VOLTARI_API_KEY env var required")

PROMPTS = [
    "say ok",
    "what is 2+2?",
    "translate hello to russian",
    "one word answer: capital of france",
]

LONG_CTX = "Summarize this: " + ("Lorem ipsum dolor sit amet. " * 800)


class VoltariUser(HttpUser):
    """Имитирует реального клиента — посылает один запрос каждые 1-3 секунды.

    Realistic mix:
      60% auto:cheap, 25% auto:smart stream, 10% pinned, 3% ru-legal, 2% long-ctx.
    """

    wait_time = between(1, 3)

    def on_start(self) -> None:
        self.client.headers.update({
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        })

    @task(60)
    def chat_cheap(self) -> None:
        self._post(
            {"model": "auto:cheap", "messages": [{"role": "user", "content": random.choice(PROMPTS)}], "max_tokens": 10},
            name="/v1/chat/completions [cheap]",
        )

    @task(25)
    def chat_smart_stream(self) -> None:
        self._post(
            {"model": "auto:smart", "messages": [{"role": "user", "content": "explain http in one sentence"}],
             "max_tokens": 30, "stream": True},
            name="/v1/chat/completions [smart-stream]",
            stream=True,
        )

    @task(10)
    def chat_pinned(self) -> None:
        model = random.choice(["gpt-5.4-mini", "claude-haiku-4.5", "gemini-3-flash"])
        self._post(
            {"model": model, "messages": [{"role": "user", "content": "ok"}], "max_tokens": 5},
            name="/v1/chat/completions [pinned]",
        )

    @task(3)
    def chat_ru_legal(self) -> None:
        self._post(
            {"model": "auto:ru-legal", "messages": [{"role": "user", "content": "Скажи привет"}], "max_tokens": 10},
            name="/v1/chat/completions [ru-legal]",
        )

    @task(2)
    def chat_long_context(self) -> None:
        self._post(
            {"model": "auto:smart", "messages": [{"role": "user", "content": LONG_CTX}], "max_tokens": 100},
            name="/v1/chat/completions [long-ctx]",
        )

    def _post(self, payload: dict, *, name: str, stream: bool = False) -> None:
        with self.client.post(
            "/v1/chat/completions",
            json=payload,
            name=name,
            stream=stream,
            timeout=30,
            catch_response=True,
        ) as r:
            if r.status_code != 200:
                r.failure(f"HTTP {r.status_code}: {r.text[:200]}")
                return
            if stream:
                # Прочитать и отбросить — только подтверждаем что streaming доходит.
                got_done = False
                for line in r.iter_lines():
                    if line.endswith(b"[DONE]"):
                        got_done = True
                        break
                if not got_done:
                    r.failure("stream did not end with [DONE]")
                    return
            else:
                if "X-Router-Decision" not in r.headers:
                    r.failure("missing X-Router-Decision header")
                    return
                try:
                    body = r.json()
                    if not body.get("choices", [{}])[0].get("message", {}).get("content"):
                        r.failure("empty content")
                        return
                except Exception as exc:
                    r.failure(f"bad json: {exc}")
                    return
            r.success()


# Hard-thresholds на финальном этапе (Locust 2.x).
@events.test_stop.add_listener
def check_thresholds(environment, **_kwargs) -> None:
    stats = environment.stats.total
    if stats.fail_ratio > 0.005:
        environment.process_exit_code = 1
        print(f"FAIL: error rate {stats.fail_ratio*100:.2f}% > 0.5%")
    if stats.get_response_time_percentile(0.95) > 5000:
        environment.process_exit_code = 1
        print(f"FAIL: p95 {stats.get_response_time_percentile(0.95):.0f}ms > 5000ms")
