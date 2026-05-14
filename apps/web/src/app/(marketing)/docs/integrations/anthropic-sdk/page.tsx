import type { Metadata } from 'next';
import type { Route } from 'next';
import { ComingSoon } from '@/components/docs/ComingSoon';

export const metadata: Metadata = {
  title: 'Интеграция с Anthropic SDK · Brikko',
  description:
    'Документация в работе. Endpoint /v1/messages уже работает.',
  alternates: { canonical: '/docs/integrations/anthropic-sdk' },
};

export default function AnthropicSdkDocPage() {
  return (
    <ComingSoon
      title="Anthropic SDK"
      description="Инструкция по работе с anthropic SDK через Brikko: смена base_url, native Messages API, content blocks, streaming, разница с OpenAI-совместимым endpoint."
      eta="Sprint 15"
      alternative={{
        label: 'Quick start (5 минут)',
        href: '/docs/getting-started' as Route,
      }}
    />
  );
}
