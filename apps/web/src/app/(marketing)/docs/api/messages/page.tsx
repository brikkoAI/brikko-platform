import type { Metadata } from 'next';
import type { Route } from 'next';
import { ComingSoon } from '@/components/docs/ComingSoon';

export const metadata: Metadata = {
  title: 'POST /v1/messages — Anthropic Messages API · Brikko',
  description:
    'Документация в работе. Endpoint доступен — пишите support@brikko.ru за технической спецификацией.',
  alternates: { canonical: '/docs/api/messages' },
};

export default function MessagesDocPage() {
  return (
    <ComingSoon
      title="Anthropic Messages API"
      description="Нативный POST /v1/messages — полная схема Anthropic: system, tools, content blocks, streaming через SSE. Нужен для Claude Code SDK и кода, написанного под anthropic SDK напрямую."
      eta="Sprint 14 (примерно 2-3 недели)"
      alternative={{
        label: 'Chat Completions API (OpenAI-совместимый)',
        href: '/docs/api/chat-completions' as Route,
      }}
    />
  );
}
