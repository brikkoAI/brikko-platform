import { redirect } from 'next/navigation';

/**
 * /docs/concepts/smart-routing → /docs/smart-routing.
 *
 * Smart Routing исторически живёт на /docs/smart-routing (не под /concepts/),
 * чтобы не ломать backlink'и vc.ru / Habr / TG-каналов. Этот роут — для тех,
 * кто пришёл по новой структуре («концепции» в sidebar).
 */
export default function SmartRoutingConceptRedirect(): never {
  redirect('/docs/smart-routing');
}
