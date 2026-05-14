'use client';

import { KeyRound, Plus, RefreshCcw, Server, Trash2, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { Badge } from '@/components/ui/badge';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { toast } from '@/components/ui/toast';
import { CreateKeyDialog } from '@/components/dashboard/CreateKeyDialog';
import { CreateMcpTokenDialog } from '@/components/dashboard/CreateMcpTokenDialog';
import { RevokeKeyDialog } from '@/components/dashboard/RevokeKeyDialog';
import {
  useApiKeys,
  useBulkRevokeKeys,
  useBulkRotateKeys,
  useMcpTokens,
  useRevokeMcpToken,
} from '@/lib/auth';
import { formatDate, formatRelative } from '@/lib/utils';
import type { ApiKey, McpToken, McpTokenScope } from '@/lib/types';

/**
 * Sprint MCP S1 — табы API keys / MCP tokens.
 *
 * Делаем встроенный controlled-tabs (без Radix) — две panels мало для отдельной
 * зависимости. Кнопки tab — нативные <button role="tab"> с aria-selected; контент
 * под ними условный.
 *
 * Принцип:
 *   - API keys (defaultTab) — все существующие фичи: bulk, rotate, revoke dialog.
 *   - MCP tokens — S1 MVP: list + create + revoke. Без bulk (см. backend S1 KISS).
 */

type TabKey = 'api' | 'mcp';

export default function KeysPage() {
  const [tab, setTab] = useState<TabKey>('api');
  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <header className="flex flex-col gap-3">
        <div>
          <h1 className="text-3xl font-semibold text-gray-900">Ключи и токены</h1>
          <p className="mt-2 text-body text-gray-500">
            API-ключи — для backend-интеграций и SDK. MCP-токены — для Claude Desktop,
            Cursor и других AI-инструментов.
          </p>
        </div>
        <div
          role="tablist"
          aria-label="Тип учётных данных"
          className="flex items-center gap-1 rounded-md border border-gray-200 bg-gray-50 p-1"
        >
          <TabButton active={tab === 'api'} onClick={() => setTab('api')} testId="tab-api-keys">
            <KeyRound className="h-4 w-4" strokeWidth={1.75} />
            API-ключи
          </TabButton>
          <TabButton active={tab === 'mcp'} onClick={() => setTab('mcp')} testId="tab-mcp-tokens">
            <Server className="h-4 w-4" strokeWidth={1.75} />
            MCP-токены
          </TabButton>
        </div>
      </header>

      {tab === 'api' ? <ApiKeysPanel /> : <McpTokensPanel />}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
  testId,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
  testId: string;
}) {
  return (
    <button
      role="tab"
      aria-selected={active}
      type="button"
      onClick={onClick}
      data-testid={testId}
      className={
        'flex items-center gap-1.5 rounded px-3 py-1.5 text-body-sm font-medium transition focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600 ' +
        (active
          ? 'bg-white text-gray-900 shadow-sm'
          : 'text-gray-600 hover:text-gray-900')
      }
    >
      {children}
    </button>
  );
}

// ============================================================
// API keys panel — bulk/rotate/revoke (existing functionality)
// ============================================================

function ApiKeysPanel() {
  const keys = useApiKeys();
  const [createOpen, setCreateOpen] = useState(false);
  const [revokeTarget, setRevokeTarget] = useState<ApiKey | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkAction, setBulkAction] = useState<'revoke' | 'rotate' | null>(null);

  const bulkRevoke = useBulkRevokeKeys();
  const bulkRotate = useBulkRotateKeys();

  const activeKeys = useMemo(() => (keys.data ?? []).filter((k) => !k.revoked_at), [keys.data]);
  const revokedKeys = useMemo(() => (keys.data ?? []).filter((k) => !!k.revoked_at), [keys.data]);

  // При revoke ключа из единичного диалога / bulk — выкидываем его id из selection.
  useEffect(() => {
    setSelectedIds((cur) => {
      const validIds = new Set(activeKeys.map((k) => k.id));
      let changed = false;
      const next = new Set<string>();
      for (const id of cur) {
        if (validIds.has(id)) {
          next.add(id);
        } else {
          changed = true;
        }
      }
      return changed ? next : cur;
    });
  }, [activeKeys]);

  function toggleSelected(id: string): void {
    setSelectedIds((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const allActiveSelected =
    activeKeys.length > 0 && activeKeys.every((k) => selectedIds.has(k.id));

  function toggleSelectAll(): void {
    if (allActiveSelected) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(activeKeys.map((k) => k.id)));
    }
  }

  async function executeBulkRevoke(): Promise<void> {
    const ids = Array.from(selectedIds);
    try {
      const res = await bulkRevoke.mutateAsync(ids);
      toast.success(`Отозвано ${res.revoked.length} ${ids.length === 1 ? 'ключ' : 'ключей'}.`);
      setSelectedIds(new Set());
      setBulkAction(null);
    } catch {
      toast.error('Не удалось отозвать. Попробуй ещё раз.');
    }
  }

  async function executeBulkRotate(): Promise<void> {
    const ids = Array.from(selectedIds);
    try {
      const res = await bulkRotate.mutateAsync(ids);
      toast.success(
        `Ротировано ${res.rotated.length} ${ids.length === 1 ? 'ключ' : 'ключей'}. Старые секреты уже не работают — обновите .env.`,
      );
      setSelectedIds(new Set());
      setBulkAction(null);
    } catch {
      toast.error('Не удалось ротировать. Попробуй ещё раз.');
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <p className="text-body-sm text-gray-500">
          Создавай ключи под каждый проект — будет проще понимать, откуда идёт нагрузка.
        </p>
        <Button
          leftIcon={<Plus className="h-4 w-4" strokeWidth={2} />}
          onClick={() => setCreateOpen(true)}
          data-testid="create-key-cta"
        >
          Создать ключ
        </Button>
      </div>

      {keys.isLoading ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </div>
      ) : activeKeys.length === 0 && revokedKeys.length === 0 ? (
        <EmptyState
          icon={<KeyRound className="h-12 w-12" strokeWidth={1.5} />}
          title="Здесь будут API-ключи"
          description="Создать ключ под каждый проект или окружение. После создания покажем curl-сниппет — копировать и запускать."
          action={
            <div className="flex flex-wrap items-center justify-center gap-2">
              <Button onClick={() => setCreateOpen(true)} leftIcon={<Plus className="h-4 w-4" />}>
                Создать первый ключ
              </Button>
              <Button asChild variant="ghost" size="sm">
                <a href="/docs/quickstart">Открыть Quickstart</a>
              </Button>
            </div>
          }
        />
      ) : (
        <Table data-testid="keys-table">
          <TableHeader>
            <TableRow>
              <TableHead className="w-10">
                <input
                  type="checkbox"
                  className="h-4 w-4 rounded border-gray-300 text-brand-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600"
                  checked={allActiveSelected}
                  ref={(el) => {
                    if (el) {
                      el.indeterminate =
                        selectedIds.size > 0 && !allActiveSelected;
                    }
                  }}
                  onChange={toggleSelectAll}
                  disabled={activeKeys.length === 0}
                  aria-label={allActiveSelected ? 'Снять выделение' : 'Выбрать все активные ключи'}
                  data-testid="select-all-keys"
                />
              </TableHead>
              <TableHead>Имя</TableHead>
              <TableHead>Префикс</TableHead>
              <TableHead>Права</TableHead>
              <TableHead>Создан</TableHead>
              <TableHead>Использован</TableHead>
              <TableHead>Статус</TableHead>
              <TableHead className="text-right">Действия</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {[...activeKeys, ...revokedKeys].map((k) => (
              <TableRow key={k.id}>
                <TableCell>
                  {k.revoked_at ? (
                    <span className="text-body-sm text-gray-300" aria-hidden="true">—</span>
                  ) : (
                    <input
                      type="checkbox"
                      className="h-4 w-4 rounded border-gray-300 text-brand-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600"
                      checked={selectedIds.has(k.id)}
                      onChange={() => toggleSelected(k.id)}
                      aria-label={`Выбрать ключ ${k.name}`}
                      data-testid={`select-key-${k.id}`}
                    />
                  )}
                </TableCell>
                <TableCell className="font-medium text-gray-900">{k.name}</TableCell>
                <TableCell>
                  <code className="font-mono text-body-sm text-gray-700">{k.prefix}…</code>
                </TableCell>
                <TableCell className="text-body-sm">
                  {k.scope === 'full' ? 'Полный' : 'Только чтение'}
                </TableCell>
                <TableCell className="text-body-sm text-gray-500">
                  {formatDate(k.created_at)}
                </TableCell>
                <TableCell className="text-body-sm text-gray-500">
                  {formatRelative(k.last_used_at)}
                </TableCell>
                <TableCell>
                  {k.revoked_at ? (
                    <Badge variant="error">Отозван</Badge>
                  ) : (
                    <Badge variant="success">Активен</Badge>
                  )}
                </TableCell>
                <TableCell className="text-right">
                  {k.revoked_at ? (
                    <span className="text-body-sm text-gray-400">—</span>
                  ) : (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setRevokeTarget(k)}
                      leftIcon={<Trash2 className="h-4 w-4" />}
                      aria-label={`Отозвать ключ ${k.name}`}
                      data-testid={`revoke-key-${k.id}`}
                    >
                      Отозвать
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <CreateKeyDialog open={createOpen} onOpenChange={setCreateOpen} />
      {revokeTarget ? (
        <RevokeKeyDialog
          apiKey={revokeTarget}
          open={Boolean(revokeTarget)}
          onOpenChange={(o) => !o && setRevokeTarget(null)}
        />
      ) : null}

      {selectedIds.size > 0 ? (
        <BulkActionBar
          count={selectedIds.size}
          onCancel={() => setSelectedIds(new Set())}
          onRevoke={() => setBulkAction('revoke')}
          onRotate={() => setBulkAction('rotate')}
        />
      ) : null}

      <BulkConfirmDialog
        action={bulkAction}
        count={selectedIds.size}
        loading={bulkRevoke.isPending || bulkRotate.isPending}
        onCancel={() => setBulkAction(null)}
        onConfirm={() => {
          if (bulkAction === 'revoke') void executeBulkRevoke();
          else if (bulkAction === 'rotate') void executeBulkRotate();
        }}
      />
    </div>
  );
}

// ============================================================
// MCP tokens panel — Sprint MCP S1
// ============================================================

const MCP_SCOPE_LABELS: Record<McpTokenScope, string> = {
  // S3 (CEO 2026-05-12): расширенный enum с 4 read-only scope + ``all``.
  all: 'Все 7 tools',
  read_account: 'Чтение аккаунта',
  read_usage: 'Чтение usage',
  recommend_model: 'Рекомендация модели',
  list_models: 'Каталог моделей',
  read_traces: 'Последние traces',
  list_cookbook: 'Cookbook-рецепты',
  list_integrations: 'Интеграции',
};

function McpTokensPanel() {
  const tokens = useMcpTokens();
  const revokeToken = useRevokeMcpToken();
  const [createOpen, setCreateOpen] = useState(false);
  const [revokeTarget, setRevokeTarget] = useState<McpToken | null>(null);

  const items = tokens.data ?? [];
  const active = items.filter((t) => !t.revoked_at);
  const revoked = items.filter((t) => !!t.revoked_at);

  async function confirmRevoke(): Promise<void> {
    if (!revokeTarget) return;
    try {
      await revokeToken.mutateAsync(revokeTarget.id);
      toast.success('Токен отозван.');
      setRevokeTarget(null);
    } catch {
      toast.error('Не удалось отозвать токен. Попробуй ещё раз.');
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <p className="text-body-sm text-gray-500">
          MCP-токены работают с Claude Desktop, Cursor, Continue, Zed. Подключаются по
          адресу <code className="font-mono text-body-sm">https://api.brikko.ru/mcp</code>.
        </p>
        <Button
          leftIcon={<Plus className="h-4 w-4" strokeWidth={2} />}
          onClick={() => setCreateOpen(true)}
          data-testid="create-mcp-token-cta"
        >
          Создать токен
        </Button>
      </div>

      {tokens.isLoading ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          icon={<Server className="h-12 w-12" strokeWidth={1.5} />}
          title="Здесь будут MCP-токены"
          description="Создай токен, чтобы подключить Brikko к Claude Desktop или Cursor. После создания покажем строку конфига."
          action={
            <Button onClick={() => setCreateOpen(true)} leftIcon={<Plus className="h-4 w-4" />}>
              Создать первый токен
            </Button>
          }
        />
      ) : (
        <Table data-testid="mcp-tokens-table">
          <TableHeader>
            <TableRow>
              <TableHead>Имя</TableHead>
              <TableHead>Префикс</TableHead>
              <TableHead>Доступ</TableHead>
              <TableHead>Создан</TableHead>
              <TableHead>Использован</TableHead>
              <TableHead>Истекает</TableHead>
              <TableHead>Статус</TableHead>
              <TableHead className="text-right">Действия</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {[...active, ...revoked].map((t) => (
              <TableRow key={t.id}>
                <TableCell className="font-medium text-gray-900">{t.name}</TableCell>
                <TableCell>
                  <code className="font-mono text-body-sm text-gray-700">{t.prefix}…</code>
                </TableCell>
                <TableCell className="text-body-sm">
                  {MCP_SCOPE_LABELS[t.scope]}
                </TableCell>
                <TableCell className="text-body-sm text-gray-500">
                  {formatDate(t.created_at)}
                </TableCell>
                <TableCell className="text-body-sm text-gray-500">
                  {formatRelative(t.last_used_at)}
                </TableCell>
                <TableCell className="text-body-sm text-gray-500">
                  {t.expires_at ? formatDate(t.expires_at) : 'Без срока'}
                </TableCell>
                <TableCell>
                  {t.revoked_at ? (
                    <Badge variant="error">Отозван</Badge>
                  ) : (
                    <Badge variant="success">Активен</Badge>
                  )}
                </TableCell>
                <TableCell className="text-right">
                  {t.revoked_at ? (
                    <span className="text-body-sm text-gray-400">—</span>
                  ) : (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setRevokeTarget(t)}
                      leftIcon={<Trash2 className="h-4 w-4" />}
                      aria-label={`Отозвать токен ${t.name}`}
                      data-testid={`revoke-mcp-token-${t.id}`}
                    >
                      Отозвать
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <CreateMcpTokenDialog open={createOpen} onOpenChange={setCreateOpen} />

      {revokeTarget ? (
        <Dialog
          open
          onOpenChange={(o) => {
            if (!o) setRevokeTarget(null);
          }}
        >
          <DialogContent data-testid="revoke-mcp-token-dialog">
            <DialogHeader>
              <DialogTitle>Отозвать токен «{revokeTarget.name}»?</DialogTitle>
              <DialogDescription>
                MCP-клиенты, использующие этот токен (Claude Desktop, Cursor и др.), сразу
                перестанут работать. Это действие нельзя отменить.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button
                type="button"
                variant="secondary"
                onClick={() => setRevokeTarget(null)}
              >
                Отмена
              </Button>
              <Button
                type="button"
                variant="destructive"
                loading={revokeToken.isPending}
                onClick={() => void confirmRevoke()}
                data-testid="revoke-mcp-token-confirm"
              >
                Отозвать
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      ) : null}
    </div>
  );
}

// ============================================================
// Bulk action bar / confirm dialog — для API keys (унаследовано из Sprint 8)
// ============================================================

function BulkActionBar({
  count,
  onCancel,
  onRevoke,
  onRotate,
}: {
  count: number;
  onCancel: () => void;
  onRevoke: () => void;
  onRotate: () => void;
}) {
  return (
    <div
      className="sticky bottom-4 z-10 mx-auto flex w-full max-w-xl items-center justify-between gap-3 rounded-lg border border-gray-200 bg-white p-3 shadow-lg"
      role="region"
      aria-label="Действия с выбранными ключами"
      data-testid="bulk-action-bar"
    >
      <p className="text-body-sm text-gray-700">
        Выбрано <strong>{count}</strong> {count === 1 ? 'ключ' : 'ключей'}
      </p>
      <div className="flex items-center gap-2">
        <Button
          variant="secondary"
          size="sm"
          leftIcon={<RefreshCcw className="h-4 w-4" />}
          onClick={onRotate}
          data-testid="bulk-rotate"
        >
          Ротировать
        </Button>
        <Button
          variant="destructive"
          size="sm"
          leftIcon={<Trash2 className="h-4 w-4" />}
          onClick={onRevoke}
          data-testid="bulk-revoke"
        >
          Отозвать
        </Button>
        <Button
          variant="ghost"
          size="sm"
          leftIcon={<X className="h-4 w-4" />}
          onClick={onCancel}
          aria-label="Отменить выбор"
          data-testid="bulk-cancel"
        >
          Отмена
        </Button>
      </div>
    </div>
  );
}

function BulkConfirmDialog({
  action,
  count,
  loading,
  onCancel,
  onConfirm,
}: {
  action: 'revoke' | 'rotate' | null;
  count: number;
  loading: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  if (action === null) return null;
  const isRevoke = action === 'revoke';
  const title = isRevoke
    ? `Отозвать ${count} ${count === 1 ? 'ключ' : 'ключей'}?`
    : `Ротировать ${count} ${count === 1 ? 'ключ' : 'ключей'}?`;
  const description = isRevoke
    ? 'Запросы с отозванных ключей сразу получат 401. Это действие нельзя отменить.'
    : 'Старые секреты перестанут работать. Новые секреты придут в email — обнови .env во всех проектах.';
  return (
    <Dialog open onOpenChange={(o) => !o && onCancel()}>
      <DialogContent data-testid="bulk-confirm-dialog">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button type="button" variant="secondary" onClick={onCancel}>
            Отмена
          </Button>
          <Button
            type="button"
            variant={isRevoke ? 'destructive' : 'primary'}
            loading={loading}
            onClick={onConfirm}
            data-testid="bulk-confirm-submit"
          >
            {isRevoke ? 'Отозвать' : 'Ротировать'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
