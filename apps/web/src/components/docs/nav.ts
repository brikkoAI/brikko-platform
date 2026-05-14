/**
 * Navigation config для /docs sidebar — single source of truth.
 *
 * UX-обоснование:
 *   - Группы упорядочены по journey: «начни → используй API → пойми концепции →
 *     подключи свой стек → закрой compliance». Это стандартный порядок чтения
 *     dev-доки (Stripe / Linear / Vercel).
 *   - На каждой странице sidebar один и тот же — пользователь не теряется при
 *     переходе между разделами (consistency principle, Nielsen #4).
 *   - `comingSoon: true` маркирует placeholder-разделы, чтобы UI мог их
 *     отрисовать визуально приглушёнными — пользователь видит roadmap, но
 *     не разочаровывается при клике (we set expectations, then meet them).
 *   - `external: true` — для ссылок на разделы вне /docs (например, Smart
 *     Router живёт на /docs/smart-routing исторически, не /docs/concepts/*).
 */

export interface DocLink {
  href: string;
  label: string;
  comingSoon?: boolean;
  external?: boolean;
}

export interface DocGroup {
  title: string;
  links: ReadonlyArray<DocLink>;
}

export const DOCS_NAV: ReadonlyArray<DocGroup> = [
  {
    title: 'Начало',
    links: [
      { href: '/docs', label: 'Обзор' },
      { href: '/docs/getting-started', label: 'Getting Started' },
    ],
  },
  {
    title: 'API Reference',
    links: [
      { href: '/docs/api/chat-completions', label: 'Chat Completions' },
      { href: '/docs/api/messages', label: 'Messages (Anthropic)', comingSoon: true },
      { href: '/docs/api/embeddings', label: 'Embeddings', comingSoon: true },
      { href: '/docs/api/audio', label: 'Audio (Whisper)', comingSoon: true },
      { href: '/docs/api/anonymize', label: 'Anonymize / Restore' },
      { href: '/docs/api/models', label: 'Models', comingSoon: true },
    ],
  },
  {
    title: 'CLI и инструменты',
    links: [
      { href: '/docs/cli', label: 'brikko-cli' },
      { href: '/docs/skills/pii-mask', label: 'PII Skill' },
    ],
  },
  {
    title: 'Концепции',
    links: [
      { href: '/docs/smart-routing', label: 'Smart Routing', external: true },
      { href: '/docs/concepts/privacy-v2', label: 'Privacy v2 (PII)' },
      { href: '/docs/concepts/pricing', label: 'Биллинг и цены', comingSoon: true },
    ],
  },
  {
    title: 'Интеграции',
    links: [
      { href: '/docs/integrations/openai-sdk', label: 'OpenAI SDK', comingSoon: true },
      { href: '/docs/integrations/anthropic-sdk', label: 'Anthropic SDK', comingSoon: true },
      { href: '/docs/integrations/n8n', label: 'n8n' },
      { href: '/docs/integrations/langchain', label: 'LangChain', comingSoon: true },
    ],
  },
  {
    title: 'Compliance',
    links: [
      { href: '/docs/compliance/152-fz', label: '152-ФЗ чеклист', comingSoon: true },
    ],
  },
];
