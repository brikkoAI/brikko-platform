# MSW Service Worker

Файл `mockServiceWorker.js` в этой папке генерируется командой:

```bash
pnpm dlx msw init public/ --save
```

или (если pnpm не используется)

```bash
npx msw init public/ --save
```

Команду нужно запустить:
- один раз после `pnpm install` — чтобы worker появился в `apps/web/public/`;
- повторно после обновления пакета `msw`.

Файл намеренно не коммитится в репозиторий (сгенерированный артефакт),
исключение в `.gitignore`. В `package.json` это можно обернуть в `postinstall`-хук
(см. ниже §1 в `apps/web/README.md`).
