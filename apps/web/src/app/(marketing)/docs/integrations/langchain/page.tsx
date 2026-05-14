import type { Metadata } from 'next';
import type { Route } from 'next';
import { ComingSoon } from '@/components/docs/ComingSoon';

export const metadata: Metadata = {
  title: 'Интеграция с LangChain · Brikko',
  description: 'Документация в работе.',
  alternates: { canonical: '/docs/integrations/langchain' },
};

export default function LangchainDocPage() {
  return (
    <ComingSoon
      title="LangChain"
      description="Инструкция по использованию Brikko в LangChain (Python и JS): ChatBrikko provider, embedding wrapper, конфигурация Smart Router и PII-protect через runtime config."
      eta="V2 (Sprint 17)"
      alternative={{
        label: 'OpenAI SDK интеграция (LangChain работает поверх)',
        href: '/docs/integrations/openai-sdk' as Route,
      }}
    />
  );
}
