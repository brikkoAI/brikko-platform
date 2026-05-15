'use client';

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from '@tanstack/react-query';
import {
  ApiClientError,
  accountApi,
  adminApi,
  analyticsApi,
  authApi,
  billingApi,
  keysApi,
  mcpKeysApi,
  tracesApi,
  usageApi,
  type AdminCheckResponse,
  type AdminStatusResponse,
  type AnalyticsSummaryParams,
  type AnalyticsSummaryResponse,
  type LoginPayload,
  type SignupPayload,
  type TelegramLinkResponse,
  type TraceDetail,
  type TracesListResponse,
  type TracesQueryParams,
} from './api';
import { clearCsrfToken, setCsrfTokenFromResponse } from './csrf';
import type {
  Account,
  AccountClosureStatus,
  ActivityEvent,
  ApiKey,
  ApiKeyCreated,
  ApiKeyScope,
  McpToken,
  McpTokenCreated,
  McpTokenScope,
  AuthSession,
  AutorefillState,
  AutorefillUpdate,
  Balance,
  DataExportRequest,
  PendingInvite,
  RoutingPreferences,
  RoutingPreferencesUpdate,
  Seat,
  SeatRole,
  TariffChangeResponse,
  TariffSlug,
  TransactionsPage,
  TransactionsQuery,
  TwoFactorSetupResponse,
  UsageGroupBy,
  UsageResponse,
} from './types';

// ============================================================
// Account / current user
// ============================================================

/** Текущий аккаунт. null = неавторизован, undefined = ещё грузим. */
export function useAccount(): UseQueryResult<Account | null> {
  return useQuery({
    queryKey: ['account'],
    queryFn: async () => {
      try {
        return await accountApi.me();
      } catch (err) {
        if (err instanceof ApiClientError && err.status === 401) return null;
        throw err;
      }
    },
    staleTime: 30_000,
    retry: false,
  });
}

/** Баланс — refetchOnFocus, чтобы цифра не врала после top-up в другой вкладке. */
export function useBalance(): UseQueryResult<Balance> {
  return useQuery({
    queryKey: ['billing', 'balance'],
    queryFn: () => billingApi.balance(),
    staleTime: 15_000,
    refetchOnWindowFocus: true,
  });
}

// ============================================================
// Auth mutations
// ============================================================

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: LoginPayload) => authApi.login(payload),
    onSuccess: (data) => {
      // Backend ротирует CSRF token на каждый login — берём свежий из response
      // вместо лишнего GET /auth/csrf round-trip (см. csrf_protocol.md §4).
      setCsrfTokenFromResponse(data);
      // После логина у нас выставлена httpOnly cookie — перезапросим /account.
      void qc.invalidateQueries({ queryKey: ['account'] });
    },
  });
}

export function useSignup() {
  return useMutation({
    mutationFn: (payload: SignupPayload) => authApi.signup(payload),
    onSuccess: () => {
      // Signup не выдаёт сессию (`verification_required`), но и старый CSRF
      // (если был — например, после пред. logout'а) не нужен. Sweep-clear.
      clearCsrfToken();
    },
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => authApi.logout(),
    onSuccess: () => {
      // Сессии больше нет — следующий вход потребует новый CSRF token.
      clearCsrfToken();
      qc.clear();
    },
  });
}

export function useVerifyEmail() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (token: string) => authApi.verifyEmail(token),
    onSuccess: () => {
      // welcome 200 ₽ начислили — обновим и аккаунт, и баланс.
      void qc.invalidateQueries({ queryKey: ['account'] });
      void qc.invalidateQueries({ queryKey: ['billing', 'balance'] });
    },
  });
}

// ============================================================
// API keys
// ============================================================

export function useApiKeys(): UseQueryResult<ApiKey[]> {
  return useQuery({
    queryKey: ['keys'],
    queryFn: () => keysApi.list(),
    staleTime: 60_000,
  });
}

export function useCreateKey() {
  const qc = useQueryClient();
  return useMutation<ApiKeyCreated, ApiClientError, { name: string; scope: ApiKeyScope }>({
    mutationFn: (payload) => keysApi.create(payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['keys'] });
    },
  });
}

export function useRenameKey() {
  const qc = useQueryClient();
  return useMutation<ApiKey, ApiClientError, { id: string; name: string }>({
    mutationFn: ({ id, name }) => keysApi.rename(id, name),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['keys'] });
    },
  });
}

export function useRevokeKey() {
  const qc = useQueryClient();
  return useMutation<{ ok: true }, ApiClientError, string>({
    mutationFn: (id) => keysApi.revoke(id),
    onMutate: async (id) => {
      // Оптимистичное обновление — список без отозванного ключа сразу.
      await qc.cancelQueries({ queryKey: ['keys'] });
      const prev = qc.getQueryData<ApiKey[]>(['keys']);
      qc.setQueryData<ApiKey[]>(['keys'], (old) =>
        old?.map((k) => (k.id === id ? { ...k, revoked_at: new Date().toISOString() } : k)) ?? [],
      );
      return { prev };
    },
    onError: (_err, _id, ctx) => {
      const snapshot = ctx as { prev?: ApiKey[] } | undefined;
      if (snapshot?.prev) qc.setQueryData(['keys'], snapshot.prev);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ['keys'] });
    },
  });
}

// ============================================================
// MCP tokens (Sprint MCP S1)
//
// Mirrors API-key hooks 1-to-1 — отдельный queryKey ['mcp-tokens'] чтобы
// инвалидация одной таблицы не дёргала другую.
// ============================================================

export function useMcpTokens(): UseQueryResult<McpToken[]> {
  return useQuery({
    queryKey: ['mcp-tokens'],
    queryFn: () => mcpKeysApi.list(),
    staleTime: 60_000,
  });
}

export function useCreateMcpToken() {
  const qc = useQueryClient();
  return useMutation<
    McpTokenCreated,
    ApiClientError,
    { name: string; scope: McpTokenScope; expires_in_days?: 30 | 90 | 180 | 365 }
  >({
    mutationFn: (payload) => mcpKeysApi.create(payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['mcp-tokens'] });
    },
  });
}

export function useRenameMcpToken() {
  const qc = useQueryClient();
  return useMutation<McpToken, ApiClientError, { id: string; name: string }>({
    mutationFn: ({ id, name }) => mcpKeysApi.rename(id, name),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['mcp-tokens'] });
    },
  });
}

export function useRevokeMcpToken() {
  const qc = useQueryClient();
  return useMutation<{ ok: true }, ApiClientError, string>({
    mutationFn: (id) => mcpKeysApi.revoke(id),
    onMutate: async (id) => {
      await qc.cancelQueries({ queryKey: ['mcp-tokens'] });
      const prev = qc.getQueryData<McpToken[]>(['mcp-tokens']);
      qc.setQueryData<McpToken[]>(['mcp-tokens'], (old) =>
        old?.map((t) =>
          t.id === id
            ? { ...t, status: 'revoked' as const, revoked_at: new Date().toISOString() }
            : t,
        ) ?? [],
      );
      return { prev };
    },
    onError: (_err, _id, ctx) => {
      const snapshot = ctx as { prev?: McpToken[] } | undefined;
      if (snapshot?.prev) qc.setQueryData(['mcp-tokens'], snapshot.prev);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ['mcp-tokens'] });
    },
  });
}

// ============================================================
// Billing
// ============================================================

export function useTransactions(
  params: TransactionsQuery = {},
): UseQueryResult<TransactionsPage> {
  return useQuery({
    queryKey: ['billing', 'transactions', params],
    queryFn: () => billingApi.transactions(params),
    staleTime: 30_000,
    // Sprint 8: keep previous data при пагинации/фильтрах — иначе UI flicker'ит
    // на каждый submit (особенно с debounced search). placeholderData = (prev) => prev
    // — стандартный паттерн TanStack v5 для smooth-pagination.
    placeholderData: (prev) => prev,
  });
}

export function useTopup() {
  return useMutation<
    { payment_id: string; confirmation_url: string },
    ApiClientError,
    {
      amount_rub: number;
      return_url: string;
      payment_method?: 'bank_card' | 'sbp' | 'tinkoff_bank' | 'sberbank';
    }
  >({
    mutationFn: (payload) => billingApi.topup(payload),
  });
}

/**
 * Card-linking flow (CEO 2026-05-15). Создаёт verification-платёж 1 ₽ через
 * ЮKassa и отдаёт `confirmation_url` — фронт делает редирект, после возврата
 * backend сохраняет payment_method_id + начисляет +100 ₽ welcome credit.
 */
export function useLinkCard() {
  return useMutation<
    { payment_id: string; confirmation_url: string },
    ApiClientError,
    { return_url: string }
  >({
    mutationFn: (payload) => billingApi.linkCard(payload),
  });
}

export function useUnlinkCard() {
  const qc = useQueryClient();
  return useMutation<{ ok: true }, ApiClientError, void>({
    mutationFn: () => billingApi.unlinkCard(),
    onSuccess: () => {
      // account — источник истины по autorefill_pm_id; balance может измениться
      // если backend откатывает welcome-bonus при unlink (политика на бэке).
      void qc.invalidateQueries({ queryKey: ['account'] });
      void qc.invalidateQueries({ queryKey: ['billing', 'balance'] });
    },
  });
}

// ============================================================
// Team
// ============================================================

export function useSeats(): UseQueryResult<Seat[]> {
  return useQuery({
    queryKey: ['account', 'seats'],
    queryFn: () => accountApi.seats(),
    staleTime: 60_000,
  });
}

export function usePendingInvites(): UseQueryResult<PendingInvite[]> {
  return useQuery({
    queryKey: ['account', 'invites'],
    queryFn: () => accountApi.invites(),
    staleTime: 60_000,
  });
}

export function useInviteSeat() {
  const qc = useQueryClient();
  return useMutation<PendingInvite, ApiClientError, { email: string; role: SeatRole }>({
    mutationFn: (payload) => accountApi.inviteSeat(payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['account', 'invites'] });
    },
  });
}

export function useRemoveSeat() {
  const qc = useQueryClient();
  return useMutation<{ ok: true }, ApiClientError, string>({
    mutationFn: (user_id) => accountApi.removeSeat(user_id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['account', 'seats'] });
    },
  });
}

// ============================================================
// Usage
// ============================================================

export function useUsage(params: {
  from: string;
  to: string;
  group_by: UsageGroupBy;
}): UseQueryResult<UsageResponse> {
  return useQuery({
    queryKey: ['usage', params],
    queryFn: () => usageApi.query(params),
    staleTime: 60_000,
  });
}

// ============================================================
// Settings
// ============================================================

export function useUpdateSettings() {
  const qc = useQueryClient();
  return useMutation({
    // notifications — JSON-blob (см. types.Account.notifications). Раньше тут стоял
    // `boolean`, что несовместимо с серверным контрактом; никто не использовал
    // такой вызов, но TS теперь ловит несостыковку.
    //
    // Sprint 4: добавлен `pii_masking_enabled` (Поток M). Backend сделает его опциональным
    // на серверной стороне до полной выкатки PII-engine'а — frontend шлёт только если
    // пользователь дёрнул toggle.
    mutationFn: (payload: {
      prompt_logging_enabled?: boolean;
      pii_masking_enabled?: boolean;
      notifications?: Record<string, unknown>;
    }) => accountApi.updateSettings(payload),
    onSuccess: (account) => {
      qc.setQueryData(['account'], account);
    },
  });
}

// ============================================================
// Telegram link (Sprint 4 / Поток M)
// ============================================================

/**
 * Запрашиваем one-time токен для привязки Telegram через bot deep-link.
 *
 * UX-нота: используется как mutation (не query), потому что:
 *   - Токен живёт всего 5 мин — кеш бесполезен.
 *   - Каждый клик «Подключить» должен генерировать новый token (старый протух или потрачен).
 */
export function useLinkTelegram() {
  return useMutation<TelegramLinkResponse, ApiClientError, void>({
    mutationFn: () => accountApi.linkTelegram(),
  });
}

export function useUnlinkTelegram() {
  const qc = useQueryClient();
  return useMutation<{ ok: true }, ApiClientError, void>({
    mutationFn: () => accountApi.unlinkTelegram(),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['account'] });
    },
  });
}

// ============================================================
// Sprint 6: Tariff change
// ============================================================

export function useChangeTariff() {
  const qc = useQueryClient();
  return useMutation<TariffChangeResponse, ApiClientError, TariffSlug>({
    mutationFn: (tariff) => accountApi.changeTariff(tariff),
    onSuccess: () => {
      // Тариф мог поменять баланс (списание подписки) и доступ к фичам — invalidate всё, что
      // пишет про текущего юзера, чтобы UI не лгал ни секунды.
      void qc.invalidateQueries({ queryKey: ['account'] });
      void qc.invalidateQueries({ queryKey: ['billing', 'balance'] });
      void qc.invalidateQueries({ queryKey: ['billing', 'transactions'] });
    },
  });
}

// ============================================================
// Sprint 6: 2FA mutations + sessions queries
// ============================================================

export function useTwoFactorSetup() {
  return useMutation<TwoFactorSetupResponse, ApiClientError, void>({
    mutationFn: () => authApi.twoFactorSetup(),
  });
}

export function useTwoFactorVerify() {
  const qc = useQueryClient();
  return useMutation<{ ok: true }, ApiClientError, string>({
    mutationFn: (code) => authApi.twoFactorVerify(code),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['account'] });
    },
  });
}

export function useTwoFactorDisable() {
  const qc = useQueryClient();
  return useMutation<{ ok: true }, ApiClientError, string>({
    mutationFn: (code) => authApi.twoFactorDisable(code),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['account'] });
    },
  });
}

/** Список активных сессий — для Settings → Security. */
export function useSessions() {
  return useQuery<AuthSession[]>({
    queryKey: ['auth', 'sessions'],
    queryFn: () => authApi.listSessions(),
    staleTime: 30_000,
  });
}

export function useRevokeSession() {
  const qc = useQueryClient();
  return useMutation<{ ok: true }, ApiClientError, string>({
    mutationFn: (id) => authApi.revokeSession(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['auth', 'sessions'] });
    },
  });
}

export function useRevokeAllSessions() {
  const qc = useQueryClient();
  return useMutation<{ ok: true }, ApiClientError, void>({
    mutationFn: () => authApi.revokeAllSessions(),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['auth', 'sessions'] });
    },
  });
}

// ============================================================
// Sprint 6: смена пароля v2 (Settings)
// ============================================================

export function useChangePasswordV2() {
  return useMutation<
    { ok: true },
    ApiClientError,
    { current_password: string; new_password: string }
  >({
    mutationFn: (payload) =>
      authApi.passwordChangeV2(payload.current_password, payload.new_password),
  });
}

// ============================================================
// Sprint 6: data-export
// ============================================================

export function useDataExportLatest() {
  return useQuery<DataExportRequest | null>({
    queryKey: ['account', 'data-export', 'latest'],
    queryFn: () => accountApi.latestDataExport(),
    staleTime: 60_000,
  });
}

export function useRequestDataExport() {
  const qc = useQueryClient();
  return useMutation<DataExportRequest, ApiClientError, void>({
    mutationFn: () => accountApi.requestDataExport(),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['account', 'data-export'] });
    },
  });
}

// ============================================================
// Sprint 6: закрытие аккаунта
// ============================================================

export function useCloseAccount() {
  const qc = useQueryClient();
  return useMutation<AccountClosureStatus, ApiClientError, string | undefined>({
    mutationFn: (reason) => accountApi.closeAccount(reason),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['account'] });
    },
  });
}

export function useCancelClosure() {
  const qc = useQueryClient();
  return useMutation<AccountClosureStatus, ApiClientError, void>({
    mutationFn: () => accountApi.cancelClosure(),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['account'] });
    },
  });
}

// ============================================================
// Sprint 6: email-verification resend
// ============================================================

export function useResendEmailVerification() {
  return useMutation<{ ok: true }, ApiClientError, string>({
    mutationFn: (email) => authApi.emailVerifyResend(email),
  });
}

// ============================================================
// Sprint 7: data export production (polling + история)
//
// Списки и polling для:
//   - DataExportSection (Privacy)
//   - админка (опционально, V2)
//
// Спека: 02_Product/v1.5 — Sprint 7 brief.
// ============================================================

/**
 * Список экспортов аккаунта — для «История экспортов» в Privacy.
 *
 * UX-нота: список pull-only — НЕ polling'уется здесь, потому что обновления
 * статусов конкретных запросов идут через `useDataExportStatus(id)`, который
 * invalidate'ит queryKey ['account','data-export','list'] на каждый переход
 * pending→processing→ready. Это даёт визуальное обновление списка без round-trip
 * каждые 5s.
 */
export function useDataExportList() {
  return useQuery<DataExportRequest[]>({
    queryKey: ['account', 'data-export', 'list'],
    queryFn: () => accountApi.listDataExports(),
    staleTime: 30_000,
  });
}

/**
 * Polling статуса конкретного export-job'а с exponential backoff.
 *
 * **Цель backoff'а:** ~720 req/час (5s × 720 = 3600s) — много даже для нашей
 * скромной нагрузки. С backoff'ом: первые 30s по 5s = 6 req, потом по 30s →
 * ~120 req/час максимум. Это безопасно даже если несколько вкладок открыты.
 *
 * **Stop-condition:** перестаём poll'ить при terminal-статусах (ready/failed/expired).
 *
 * @param id — UUID экспорта; null если ещё не создан (хук просто не делает запрос).
 */
export function useDataExportStatus(id: string | null): UseQueryResult<DataExportRequest> {
  const qc = useQueryClient();
  return useQuery<DataExportRequest>({
    queryKey: ['account', 'data-export', 'status', id],
    queryFn: () => accountApi.dataExportStatus(id as string),
    enabled: Boolean(id),
    // Динамический interval: смотрим на возраст «query state» внутри callback'а.
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return 5_000;
      // Terminal statuses — стоп.
      if (data.status === 'ready' || data.status === 'failed' || data.status === 'expired') {
        // Один раз invalidate список (чтобы История экспортов отрисовала final-status).
        void qc.invalidateQueries({ queryKey: ['account', 'data-export', 'list'] });
        void qc.invalidateQueries({ queryKey: ['account', 'data-export', 'latest'] });
        return false;
      }
      // Exponential backoff: 5s → 10s → 20s → cap 30s. Cap нужен, чтобы tab открытый
      // на ночь не делал 1 запрос/час: 30s — компромисс между UX и нагрузкой.
      const startedAtMs = new Date(data.requested_at).getTime();
      const ageS = Math.max(0, (Date.now() - startedAtMs) / 1000);
      if (ageS < 30) return 5_000;
      if (ageS < 90) return 10_000;
      if (ageS < 180) return 20_000;
      return 30_000;
    },
    // refetchInterval сам решит — но если query попал в background'е после
    // terminal-status, refetchOnWindowFocus оставляем на стандарт (false для backoff'а).
    refetchOnWindowFocus: false,
  });
}

// ============================================================
// Sprint 7: routing preferences (smart-router policy per-account)
//
// Спека: 02_Product/v1.5/22_routing_preferences_spec.md.
// ============================================================

export function useRoutingPreferences(): UseQueryResult<RoutingPreferences> {
  return useQuery<RoutingPreferences>({
    queryKey: ['account', 'routing-preferences'],
    queryFn: () => accountApi.routingPreferences(),
    // Long staleTime — preferences меняются редко, не дёргаем при каждом
    // tab-focus'е. После Save mutation сама invalidate'ит queryKey.
    staleTime: 5 * 60_000,
  });
}

export function useUpdateRoutingPreferences() {
  const qc = useQueryClient();
  return useMutation<RoutingPreferences, ApiClientError, RoutingPreferencesUpdate>({
    mutationFn: (payload) => accountApi.updateRoutingPreferences(payload),
    onSuccess: (data) => {
      // Backend возвращает full snapshot — кладём прямо в кэш, чтобы UI обновился
      // без лишнего round-trip'а.
      qc.setQueryData(['account', 'routing-preferences'], data);
    },
  });
}

// ============================================================
// Sprint 8: Auto-refill v2
//
// Спека: BRIEF Sprint 8 §1. UI: AutoRefillCard в Settings → Billing.
// ============================================================

export function useAutorefill(): UseQueryResult<AutorefillState> {
  return useQuery<AutorefillState>({
    queryKey: ['account', 'autorefill'],
    queryFn: () => accountApi.getAutorefill(),
    staleTime: 60_000,
  });
}

export function useUpdateAutorefill() {
  const qc = useQueryClient();
  return useMutation<AutorefillState, ApiClientError, AutorefillUpdate>({
    mutationFn: (payload) => accountApi.updateAutorefill(payload),
    onSuccess: (data) => {
      // Backend возвращает full snapshot — кладём прямо в кэш.
      qc.setQueryData(['account', 'autorefill'], data);
    },
  });
}

export function useDisableAutorefill() {
  const qc = useQueryClient();
  return useMutation<AutorefillState, ApiClientError, void>({
    mutationFn: () => accountApi.disableAutorefill(),
    onSuccess: (data) => {
      qc.setQueryData(['account', 'autorefill'], data);
    },
  });
}

// ============================================================
// Sprint 8: Activity feed
// ============================================================

export function useActivity(limit = 10): UseQueryResult<ActivityEvent[]> {
  return useQuery<ActivityEvent[]>({
    queryKey: ['account', 'activity', limit],
    queryFn: () => accountApi.activity({ limit }),
    // Activity refetch при возврате на вкладку — пользователь хочет видеть
    // свежие события если только что что-то произошло в другой вкладке.
    refetchOnWindowFocus: true,
    staleTime: 30_000,
  });
}

// ============================================================
// Sprint 8: Bulk operations для API keys
//
// Спека: BRIEF Sprint 8 §5. После bulk-операций invalidate'им ['keys'] —
// MSW и backend возвращают актуальное состояние через next list-fetch.
// ============================================================

export function useBulkRevokeKeys() {
  const qc = useQueryClient();
  return useMutation<{ revoked: string[] }, ApiClientError, string[]>({
    mutationFn: (key_ids) => keysApi.bulkRevoke(key_ids),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['keys'] });
    },
  });
}

// ============================================================
// BrikkoLens — observability traces (Phase 5 #4, 2026-05-09)
// ============================================================

export function useTraces(params: TracesQueryParams = {}): UseQueryResult<TracesListResponse> {
  return useQuery({
    queryKey: ['traces', params],
    queryFn: () => tracesApi.list(params),
    staleTime: 15_000,
    placeholderData: (prev) => prev,
  });
}

export function useTraceDetail(request_id: string | null): UseQueryResult<TraceDetail> {
  return useQuery({
    queryKey: ['trace', request_id],
    queryFn: () => tracesApi.detail(request_id as string),
    enabled: !!request_id,
    staleTime: 60_000,
  });
}

// ============================================================
// BrikkoLens — analytics summary (Sprint 2, 2026-05-09)
// ============================================================

export function useAnalyticsSummary(
  params: AnalyticsSummaryParams = {},
): UseQueryResult<AnalyticsSummaryResponse> {
  return useQuery({
    queryKey: ['analytics-summary', params],
    queryFn: () => analyticsApi.summary(params),
    staleTime: 30_000,
    placeholderData: (prev) => prev,
  });
}

// ============================================================
// Admin status — CEO single-pane dashboard (Sprint 13.7, 2026-05-09)
// ============================================================

export function useAdminStatus(): UseQueryResult<AdminStatusResponse> {
  return useQuery({
    queryKey: ['admin-status'],
    queryFn: () => adminApi.status(),
    refetchInterval: 30_000, // обновление каждые 30 сек — CEO смотрит и видит свежие
    staleTime: 15_000,
  });
}

/** Lightweight `is_admin` toggle для UI nav (показать/скрыть «Админка»). */
export function useIsAdmin(): UseQueryResult<AdminCheckResponse> {
  return useQuery({
    queryKey: ['admin-check'],
    queryFn: () => adminApi.check(),
    staleTime: 5 * 60_000, // 5 минут — флаг редко меняется
    retry: false,
  });
}


export function useBulkRotateKeys() {
  const qc = useQueryClient();
  return useMutation<
    {
      rotated: Array<{
        id: string;
        full_key: string;
        prefix: string;
        scope: ApiKeyScope;
      }>;
    },
    ApiClientError,
    string[]
  >({
    mutationFn: (key_ids) => keysApi.bulkRotate(key_ids),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['keys'] });
    },
  });
}
