/**
 * Single source of truth for brand naming.
 * Renaming Brikko -> Routera = править только этот файл и messages/*.json.
 * См. 02_Product/05_frontend_spec.md, Приложение A.
 *
 * Реквизиты исполнителя (самозанятый) — для публичной оферты, ЮKassa-проверки,
 * страницы /legal/info. Обновлено 2026-05-01: получен статус самозанятого.
 */
export const BRAND = {
  name: 'Brikko',
  domain: 'brikko.ru',
  apiDomain: 'api.brikko.ru',
  supportEmail: 'gridchin.pismorf@gmail.com',
  // Юр. реквизиты исполнителя (самозанятого, плательщика НПД).
  legalEntity: 'Гридчин Максим Дмитриевич' as string | undefined,
  legalStatus: 'Самозанятый (плательщик НПД)' as string | undefined,
  inn: '680503607889' as string | undefined,
  phone: '+7 995 620-95-98' as string | undefined,
} as const;

export type Brand = typeof BRAND;

/**
 * Build-time guard: производственная сборка с включённым `NEXT_PUBLIC_REQUIRE_LEGAL=true`
 * провалится, если юр. реквизиты не заполнены. По умолчанию проверка выключена —
 * self-launch и Спринты 1-2 идут на внутреннее тестирование, юр. поля никому не
 * показываются. Включаем флаг в `infra/.env` ровно перед публичным запуском.
 */
if (
  typeof process !== 'undefined' &&
  process.env.NODE_ENV === 'production' &&
  process.env.NEXT_PUBLIC_REQUIRE_LEGAL === 'true' &&
  (!BRAND.legalEntity || !BRAND.inn)
) {
  throw new Error(
    'BRAND.legalEntity and BRAND.inn must be set before public production deploy. ' +
      'See docs/tech_debt_registry.md → "Оформить ИП".',
  );
}
