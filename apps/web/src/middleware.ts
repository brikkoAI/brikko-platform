import { NextResponse, type NextRequest } from 'next/server';

/**
 * Auth middleware — лёгкий cookie-presence-check, без JWT-валидации.
 *
 * Зачем не валидируем JWT здесь:
 *   1. Validation требует секрет, а Edge runtime запускается не на нашем сервере, а на CDN.
 *   2. Backend — единственный источник истины: 401 от него на любой /v1/* инициирует
 *      redirect через `lib/api.ts` (или refresh-flow). Middleware решает только
 *      «есть ли смысл вообще рендерить /app/*» (UX-anti-flicker).
 *
 * Cookie name = `vlt_access` (см. gateway/auth/cookies.py:32). HttpOnly выставлен
 * backend'ом, так что мы тут проверяем только присутствие, не содержимое.
 *
 * Edge cases:
 *   - В MSW-режиме (NEXT_PUBLIC_API_BASE_URL пустой) cookie не существует — middleware
 *     отключаем через NEXT_PUBLIC_AUTH_MIDDLEWARE_DISABLED=true в .env.local.
 *     Иначе e2e тесты на MSW бесконечно редиректили бы на /login.
 *   - На /login и /signup — наоборот, пускаем дальше если cookie уже есть, чтобы
 *     не делать «уже залогинен — иди на /app» (это разруливает страница сама,
 *     если backend вернёт 200 на /v1/account).
 */

const ACCESS_COOKIE = 'vlt_access';

const PROTECTED_PREFIXES = ['/app'];
const AUTH_PAGES = ['/login', '/signup'];

function isProtected(pathname: string): boolean {
  return PROTECTED_PREFIXES.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));
}

function isAuthPage(pathname: string): boolean {
  return AUTH_PAGES.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));
}

export function middleware(request: NextRequest) {
  // Dev-bypass: в MSW-режиме backend не выставит реальный cookie, а сессия эмулируется
  // в service worker'е → middleware не должен мешать.
  if (process.env.NEXT_PUBLIC_AUTH_MIDDLEWARE_DISABLED === 'true') {
    return NextResponse.next();
  }

  const { pathname, search } = request.nextUrl;
  const hasAccess = request.cookies.has(ACCESS_COOKIE);

  if (isProtected(pathname) && !hasAccess) {
    const url = request.nextUrl.clone();
    url.pathname = '/login';
    // Сохраняем reason для UI-баннера, плюс next= для возврата после login.
    url.search = `?reason=session_expired&next=${encodeURIComponent(pathname + search)}`;
    return NextResponse.redirect(url);
  }

  if (isAuthPage(pathname) && hasAccess) {
    // Исключение: /signup/verify-email требует залогиненного-без-verified юзера.
    // Backend сам разберётся с конкретной ошибкой; не редиректим эту страницу.
    if (pathname.startsWith('/signup/verify-email')) {
      return NextResponse.next();
    }
    const url = request.nextUrl.clone();
    url.pathname = '/app';
    url.search = '';
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  // Не дёргаем middleware на ассетах, API-роутах Next.js, OG-картинках и фавиконе —
  // незачем тратить Edge-runtime на статику.
  matcher: ['/((?!_next/static|_next/image|favicon\\.ico|api/|mockServiceWorker\\.js|.*\\..*).*)'],
};
