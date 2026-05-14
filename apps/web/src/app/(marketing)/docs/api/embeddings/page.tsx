import type { Metadata } from 'next';
import type { Route } from 'next';
import { ComingSoon } from '@/components/docs/ComingSoon';

export const metadata: Metadata = {
  title: 'POST /v1/embeddings — Embeddings API · Brikko',
  description:
    'Документация в работе. Endpoint доступен — пишите support@brikko.ru за технической спецификацией.',
  alternates: { canonical: '/docs/api/embeddings' },
};

export default function EmbeddingsDocPage() {
  return (
    <ComingSoon
      title="Embeddings API"
      description="OpenAI-совместимый embeddings endpoint для RAG/семантического поиска. Модели text-embedding-3-small (1536-dim, 184 ₽/1M токенов) и text-embedding-3-large (3072-dim, 1196 ₽/1M токенов). Поддержка string и array input."
      eta="Sprint 14"
      alternative={{
        label: 'Обзор всех endpoint в /docs',
        href: '/docs' as Route,
      }}
    />
  );
}
