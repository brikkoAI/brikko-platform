import type { Metadata } from 'next';
import type { Route } from 'next';
import { ComingSoon } from '@/components/docs/ComingSoon';

export const metadata: Metadata = {
  title: 'Интеграция с OpenAI SDK · Brikko',
  description:
    'Документация в работе. Краткая инструкция доступна на /docs/getting-started.',
  alternates: { canonical: '/docs/integrations/openai-sdk' },
};

export default function OpenAiSdkDocPage() {
  return (
    <ComingSoon
      title="OpenAI SDK"
      description="Полная инструкция по миграции существующего кода на openai SDK (Python/Node/Go) на Brikko. Подмена base_url, передача brikko-специфичных полей, обработка ошибок, рекомендации по retries и timeouts."
      eta="Sprint 14"
      alternative={{
        label: 'Quick start (5 минут)',
        href: '/docs/getting-started' as Route,
      }}
    />
  );
}
