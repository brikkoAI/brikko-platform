import { Hero } from '@/components/marketing/Hero';
import { ChannelsGrid } from '@/components/marketing/ChannelsGrid';
import { StudioAnchor } from '@/components/marketing/StudioAnchor';
import { Features } from '@/components/marketing/Features';
import { PricingCards } from '@/components/marketing/PricingCards';
import { FAQ } from '@/components/marketing/FAQ';

/**
 * Landing — Cream Studio v6 + PII pivot (2026-05-14).
 *
 * Структура после pivot на «AI Privacy Ecosystem» (BRIEF_v2_pivot.md):
 *   1. Hero — «AI без утечки 152-ФЗ» + curl-окно anonymize-endpoint.
 *   2. ChannelsGrid — 6 distribution channels (Shield/Studio/CLI/Skill/n8n/Presidio).
 *   3. StudioAnchor — детальный анонс Studio v0.3.0 для AI-команд.
 *   4. Features — 6 differentiator-карточек (Natasha NER, RU entities,
 *      reversible unmask, локальная обработка, observability, 152-ФЗ).
 *   5. PricingCards — 4 тарифа (Free / Pro 290 / Team 1990 / Enterprise).
 *   6. FAQ.
 *
 * Что убрано с главной после pivot:
 *   - ProofCounters (38 / 6 / 200₽ / 99.9%) — про gateway-каталог моделей.
 *   - ModelsShowcase (6 hand-picked LLM) — каталог LLM, не privacy-фокус.
 *   - RoadmapSection (gateway roadmap) — переедет на /roadmap dedicated page.
 *   - CookbookTeaser (gateway-сценарии) — на /cookbook остаётся, с главной убран.
 *   - ObservabilityShowcase — встроено в Features (карточка `lens`), отдельная
 *     секция дублирует.
 * Все компоненты сохранены в репо для использования на dedicated страницах
 * (/models, /benchmarks, /cookbook) или возврата если pivot не выстрелит.
 *
 * UX-обоснование порядка: Hero — что/зачем → ChannelsGrid — конкретный
 * way-in (главный CTA pivot'а) → StudioAnchor — детали для AI-команд →
 * Features — почему мы лучше западных → Pricing → FAQ.
 */
export default function LandingPage() {
  return (
    <>
      <Hero />
      <SectionDivider />
      <ChannelsGrid />
      <SectionDivider />
      <StudioAnchor />
      <SectionDivider />
      <Features />
      <SectionDivider />
      <PricingCards />
      <SectionDivider />
      <FAQ />
    </>
  );
}

function SectionDivider() {
  return <div className="brikko-divider" aria-hidden="true" />;
}
