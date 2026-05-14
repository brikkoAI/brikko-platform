/**
 * Healthcheck endpoint для Docker `HEALTHCHECK` директивы и внешнего мониторинга.
 *
 * Контракт минимальный: возвращает 200 + JSON, если Next.js процесс жив.
 * Не делаем round-trip к gateway/БД (это отдельный `/api/ready` или прямой /healthz
 * на gateway). Idempotent, без побочных эффектов, без аутентификации.
 */
export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

export function GET(): Response {
  return Response.json({
    status: 'ok',
    service: 'voltari-web',
    timestamp: new Date().toISOString(),
  });
}
