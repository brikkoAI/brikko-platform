# Brikko Load Tests

Скрипты для нагрузочного тестирования Brikko gateway.
См. полный план в `02_Product/17_load_test_plan.md`.

## Файлы

| Файл | Сценарий | Длительность | Цель |
|---|---|---|---|
| `baseline.js` | LT-1 | ~10 мин | 200 RPM устойчиво, p95 < 500 мс |
| `spike.js` | LT-2 | ~6 мин | 0 → 1000 RPM спайк за 30 сек |
| `stress.js` | LT-3 | ~15 мин | Найти точку отказа (до 5000 RPM) |
| `soak.js` | LT-4 | 24 часа | Memory leaks, deadlocks |
| `mixed.js` | LT-5 | 30 мин | Реалистичная пропорция трафика |
| `locustfile.py` | альтернатива | любая | Python-версия с UI |

## Быстрый старт

```bash
# Установка k6
brew install k6   # macOS
choco install k6  # Windows

# Базовый прогон
k6 run -e BASE_URL=https://staging-api.brikko.ru \
       -e API_KEY=$STAGING_LOAD_KEY \
       baseline.js

# Locust (Python alternative)
pip install locust
BRIKKO_API_KEY=$STAGING_LOAD_KEY locust -f locustfile.py \
  --host https://staging-api.brikko.ru
```

## Что НЕ делать

- НЕ запускать на production (ломает live-клиентам).
- НЕ запускать stress-test без согласования (всколыхнёт алерты).
- НЕ оставлять soak-test без мониторинга (24 часа — много времени проспать падение).
