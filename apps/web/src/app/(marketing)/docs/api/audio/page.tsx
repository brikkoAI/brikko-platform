import type { Metadata } from 'next';
import type { Route } from 'next';
import { ComingSoon } from '@/components/docs/ComingSoon';

export const metadata: Metadata = {
  title: 'POST /v1/audio/transcriptions — Speech-to-Text · Brikko',
  description:
    'Документация в работе. Endpoint доступен — пишите support@brikko.ru за технической спецификацией.',
  alternates: { canonical: '/docs/api/audio' },
};

export default function AudioDocPage() {
  return (
    <ComingSoon
      title="Audio (Whisper) API"
      description="OpenAI-совместимый Speech-to-Text endpoint. Whisper-1, multipart/form-data, файл до 25 MB, 50+ языков включая русский. Биллинг по минутам аудио (55.2 ₽/минута, округление вверх до 0.1 мин)."
      eta="Sprint 14"
      alternative={{
        label: 'Обзор всех endpoint в /docs',
        href: '/docs' as Route,
      }}
    />
  );
}
