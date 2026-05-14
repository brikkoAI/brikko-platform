import type { Metadata } from 'next';
import { PlaygroundForm } from './PlaygroundForm';

/**
 * /playground — public sandbox без регистрации.
 *
 * PRD: 02_Product/v1.5/31_public_sandbox_prd_2026-05-01.md.
 *
 * Архитектура:
 *   - Этот файл — server component-обёртка с метаданными (Open Graph, sitemap).
 *   - Вся интерактивность вынесена в `PlaygroundForm.tsx` (`'use client'`).
 *
 * Почему отдельная страница, а не модалка на главной:
 *   shareable URL `brikko.ru/playground` критичен — на него ведут комментарии
 *   в Habr/vc.ru. Модалка такого не даёт. См. PRD §3.1.
 */

export const metadata: Metadata = {
  title: 'Playground — попробовать LLM без регистрации | Brikko',
  description:
    'Тест 6 топ-моделей без регистрации и без карты. GPT, Claude, Gemini, DeepSeek, YandexGPT, GigaChat в одной форме. 5 запросов в час бесплатно.',
  alternates: { canonical: '/playground' },
  openGraph: {
    title: 'Brikko Playground — все 6 топ-моделей без регистрации',
    description:
      '5 запросов в час бесплатно. Реальные ответы от LLM через Brikko gateway, без регистрации и без карты.',
    url: '/playground',
    type: 'website',
  },
};

export default function PlaygroundPage() {
  return (
    <div className="mx-auto max-w-6xl px-6 py-16 lg:py-24">
      <PlaygroundForm />
    </div>
  );
}
