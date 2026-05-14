/**
 * Schema-contract generator (TD-041).
 *
 * Что делает:
 *   1. Берёт OpenAPI spec из FastAPI (через HTTP или из локального файла).
 *   2. Прогоняет через `openapi-typescript` → пишет в src/lib/types-generated.ts.
 *
 * Когда запускать:
 *   - Локально перед PR: `npm run generate:types` (см. package.json).
 *   - В CI: pre-test step. Если diff с git'ом ≠ 0 → fail (форс-merge backend changes).
 *
 * Почему отдельный файл (types-generated.ts), не подмена types.ts:
 *   - types.ts содержит branded types (Kopecks), JSDoc комментарии и helper'ы —
 *     генератор это не сохранит.
 *   - Контракт-тест (см. tests/schema-contract.test.ts) сравнивает структуры
 *     ВРУЧНУЮ — тогда мы видим осознанные расхождения, а не «вдруг всё перетёрло».
 *
 * Источник spec'а:
 *   1. ENV `OPENAPI_URL`  — http://localhost:8000/openapi.json (типичный dev).
 *   2. ENV `OPENAPI_FILE` — путь к локально сохранённому openapi.json.
 *   3. Default — попытка fetch'а http://localhost:8000/openapi.json.
 *
 * Зависимость `openapi-typescript` — devDependency. Если её нет — exit с понятной
 * подсказкой, не голый ENOENT.
 */

import { writeFileSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const OUT = resolve(process.cwd(), 'src/lib/types-generated.ts');
const DEFAULT_URL = 'http://localhost:8000/openapi.json';

/**
 * Загрузка spec'а — приоритет источников:
 *   1. OPENAPI_FILE env var — explicit override.
 *   2. OPENAPI_URL env var — running gateway (dev/CI с backend).
 *   3. `openapi-snapshot.json` рядом с package.json — versioned snapshot, обновляется
 *      вручную при каждом backend-merge: `cd apps/gateway && python -m voltari_gateway.tools.dump_openapi > ../web/openapi-snapshot.json`.
 *      Этого хватает чтобы CI прогнал `generate:types` без поднятия gateway'а.
 *   4. Fallback — DEFAULT_URL (localhost:8000).
 */
async function loadSpec(): Promise<unknown> {
  const file = process.env.OPENAPI_FILE;
  if (file) {
    return JSON.parse(readFileSync(file, 'utf-8'));
  }
  const snapshotPath = resolve(process.cwd(), 'openapi-snapshot.json');
  try {
    const content = readFileSync(snapshotPath, 'utf-8');
    console.log(`[generate-types] Using snapshot: ${snapshotPath}`);
    return JSON.parse(content);
  } catch {
    // Файла нет — продолжаем к network fallback.
  }
  const url = process.env.OPENAPI_URL ?? DEFAULT_URL;
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`Failed to fetch ${url}: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

async function main(): Promise<void> {
  // openapi-typescript v7 поменял API: дефолт-функция возвращает массив TS-AST-нод (ts-morph),
  // которые нужно сериализовать через `astToString`. v6 возвращал строку напрямую.
  // Здесь обрабатываем оба варианта.
  type OpenapiTSModule = {
    default: (spec: unknown, options?: unknown) => Promise<unknown>;
    astToString?: (ast: unknown) => string;
  };
  let openapiTS: OpenapiTSModule;
  try {
    // Динамический import — пакет в devDependencies. Если разработчик не делал
    // `npm install` (например, на чистом freezе для CI smoke) — даём чёткое сообщение.
    openapiTS = (await import('openapi-typescript')) as unknown as OpenapiTSModule;
  } catch {
    console.error(
      '[generate-types] `openapi-typescript` не установлен. Выполни `npm install --save-dev openapi-typescript` и повтори.',
    );
    process.exit(2);
  }

  const spec = await loadSpec();
  const result = await openapiTS.default(spec);
  const body =
    typeof result === 'string'
      ? result
      : openapiTS.astToString
        ? openapiTS.astToString(result)
        : String(result); // safety fallback — лучше падающий tsc, чем тихая ошибка.
  const header =
    '/* eslint-disable */\n// AUTO-GENERATED. Не редактируй вручную.\n// См. scripts/generate-types.ts (TD-041).\n\n';
  writeFileSync(OUT, header + body, 'utf-8');
  console.log(`[generate-types] Wrote ${OUT}`);
}

main().catch((err) => {
  console.error('[generate-types] Failed:', err);
  process.exit(1);
});
