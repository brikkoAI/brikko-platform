'use client';

import { useState } from 'react';
import { ShieldCheck, Send, Copy, Check, MessageCircle } from 'lucide-react';
import { HelperTooltip } from '@/components/ui/helper-tooltip';
import { Card, CardDescription, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Badge } from '@/components/ui/badge';
import { Banner } from '@/components/ui/banner';
import { toast } from '@/components/ui/toast';
import {
  useAccount,
  useUpdateSettings,
  useLogout,
  useLinkTelegram,
  useUnlinkTelegram,
} from '@/lib/auth';
import { DataExportSection } from '@/components/dashboard/DataExportSection';
import { formatDate } from '@/lib/utils';
import { isPiiTier, type Tariff, type TelegramNotificationSettings } from '@/lib/types';
import type { TelegramLinkResponse } from '@/lib/api';

const TARIFF_LABEL: Record<Tariff, string> = {
  payg: 'Pay-as-you-go',
  pro: 'Pro Features',
  pro_privacy: 'Pro Privacy',
  team: 'Team',
  business: 'Business',
  business_privacy: 'Business Privacy',
  business_plus: 'Business+',
};

/**
 * Извлекаем notifications.telegram (см. Account.notifications JSON-blob).
 * Backend хранит свободно, frontend читает с graceful default'ами.
 */
function readTelegramSettings(notifications: Record<string, unknown>): TelegramNotificationSettings {
  const raw = notifications['telegram'];
  if (!raw || typeof raw !== 'object') return {};
  const r = raw as Record<string, unknown>;
  return {
    balance_low: typeof r.balance_low === 'boolean' ? r.balance_low : undefined,
    failover_triggered:
      typeof r.failover_triggered === 'boolean' ? r.failover_triggered : undefined,
    key_created: typeof r.key_created === 'boolean' ? r.key_created : undefined,
  };
}

export default function SettingsPage() {
  const account = useAccount();
  const updateSettings = useUpdateSettings();
  const logout = useLogout();

  if (account.isLoading) {
    return (
      <div className="mx-auto max-w-3xl">
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (!account.data) return null;
  const a = account.data;
  const piiAvailable = isPiiTier(a.tariff);
  const piiEnabled = Boolean(a.pii_masking_enabled);

  return (
    <div className="flex flex-col gap-6">
      {/*
        Header страницы лежит в settings/layout.tsx — там же tabs и closure-banner.
        На этом маршруте Tab «Профиль» активен; контент ниже совмещает Profile + Privacy +
        Telegram + Session, потому что разделять на 3 sub-routes ради «правильных tabs»
        в Sprint 6 не стоит — это сломало бы 123 теста.
       */}
      <Card>
        <CardDescription>Аккаунт</CardDescription>
        <dl className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2">
          <div>
            <dt className="text-body-sm text-gray-500">Email</dt>
            <dd className="text-body text-gray-900">{a.email}</dd>
          </div>
          <div>
            <dt className="text-body-sm text-gray-500">Имя организации</dt>
            <dd className="text-body text-gray-900">{a.name ?? '—'}</dd>
          </div>
          <div>
            <dt className="text-body-sm text-gray-500">Тариф</dt>
            <dd className="text-body text-gray-900">
              <Badge variant="brand">{TARIFF_LABEL[a.tariff] ?? a.tariff}</Badge>
            </dd>
          </div>
          <div>
            <dt className="text-body-sm text-gray-500">Зарегистрирован</dt>
            <dd className="text-body text-gray-900">{formatDate(a.created_at)}</dd>
          </div>
        </dl>
      </Card>

      {/*
        Privacy section (Sprint 4 / Поток M coordination).
        Размещаем выше Telegram — это маркетинговый USP V2 и юридически наиболее значимая
        настройка (152-ФЗ). Пользователь должен увидеть её первой при заходе в Settings.
      */}
      <Card id="privacy">
        <CardTitle>Защита персональных данных</CardTitle>
        <CardDescription className="mt-1">
          Контролирует, что Brikko делает с ПДн в твоих промптах перед отправкой провайдерам
          (OpenAI, Anthropic и т.д.).
        </CardDescription>

        <PrivacyPiiToggle
          available={piiAvailable}
          enabled={piiEnabled}
          loading={updateSettings.isPending}
          onChange={async (next) => {
            try {
              await updateSettings.mutateAsync({ pii_masking_enabled: next });
              toast.success(
                next
                  ? 'PII-маскинг включён. Запросы будут проходить через фильтр.'
                  : 'PII-маскинг отключён.',
              );
            } catch {
              toast.error('Не удалось сохранить. Попробуй ещё раз.');
            }
          }}
        />

        <hr className="my-6 border-gray-200" />

        <CardDescription>Логирование промптов</CardDescription>
        <label className="mt-3 flex items-start gap-3">
          <input
            type="checkbox"
            className="mt-1 h-4 w-4 rounded border-gray-300 text-brand-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600"
            checked={a.prompt_logging_enabled}
            onChange={async (e) => {
              try {
                await updateSettings.mutateAsync({
                  prompt_logging_enabled: e.target.checked,
                });
                toast.success(
                  e.target.checked
                    ? 'Логирование промптов включено.'
                    : 'Логирование промптов отключено.',
                );
              } catch {
                toast.error('Не удалось сохранить настройку.');
              }
            }}
          />
          <span className="flex flex-col gap-1">
            <span className="text-body font-medium text-gray-900">
              Логировать запросы и ответы
            </span>
            <span className="text-body-sm text-gray-500">
              Помогает отлаживать problems. Метаданные (токены, стоимость, модель) логируются
              всегда — без них нельзя посчитать счёт.
            </span>
          </span>
        </label>
      </Card>

      <DataExportSection />

      <div id="telegram">
        <TelegramSection
          link={a.telegram_link}
          notifications={readTelegramSettings(a.notifications)}
          onAlertChange={async (key, next) => {
            const current = readTelegramSettings(a.notifications);
            const updated: TelegramNotificationSettings = { ...current, [key]: next };
            try {
              await updateSettings.mutateAsync({
                notifications: { ...a.notifications, telegram: updated },
              });
            } catch {
              toast.error('Не удалось сохранить настройку.');
            }
          }}
        />
      </div>

      <Card>
        <CardTitle>Сессия</CardTitle>
        <CardDescription className="mt-1">
          Выход не отзывает API-ключи — они продолжат работать. Чтобы остановить запросы — отзови
          ключи на странице{' '}
          <a href="/app/keys" className="text-brand-600 hover:underline">
            ключей
          </a>
          .
        </CardDescription>
        <Button
          variant="secondary"
          className="mt-4"
          loading={logout.isPending}
          onClick={async () => {
            try {
              await logout.mutateAsync();
              window.location.assign('/login');
            } catch {
              toast.error('Не удалось выйти. Попробуй ещё раз.');
            }
          }}
          data-testid="logout-button"
        >
          Выйти
        </Button>
      </Card>
    </div>
  );
}

/**
 * PII-маскинг toggle с tier-gate.
 *
 * UX-обоснование:
 *   - На non-Privacy тарифах рендерим disabled-toggle + Banner с upgrade CTA внутри той же карточки,
 *     а не открываем drawer/modal — пользователь должен видеть, что фича существует, но требует
 *     апгрейда. Спрятать = дать ProxyAPI лишний шанс.
 *   - На Privacy-тарифах — обычный checkbox + описание, без лишних подтверждений.
 *     Включить-выключить можно одним кликом, потому что это не destructive action.
 *   - 152-ФЗ-формулировка с указанием штрафа в банере — единственное место, где fines
 *     приводятся прямо: пугать в каждом углу bad UX, но в Privacy-секции это уместно.
 */
function PrivacyPiiToggle({
  available,
  enabled,
  loading,
  onChange,
}: {
  available: boolean;
  enabled: boolean;
  loading: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <div className="mt-4 flex flex-col gap-4">
      <label
        className={`flex items-start gap-3 ${available ? '' : 'opacity-60'}`}
        data-testid="pii-masking-toggle-label"
      >
        <input
          type="checkbox"
          className="mt-1 h-4 w-4 rounded border-gray-300 text-brand-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600 disabled:cursor-not-allowed"
          checked={available && enabled}
          disabled={!available || loading}
          onChange={(e) => onChange(e.target.checked)}
          aria-describedby="pii-masking-help"
          data-testid="pii-masking-toggle"
        />
        <span className="flex flex-col gap-1">
          <span className="flex items-center gap-2 text-body font-medium text-gray-900">
            <ShieldCheck className="h-4 w-4 text-brand-700" strokeWidth={1.75} aria-hidden="true" />
            Маскировать ПДн в запросах к моделям
            <HelperTooltip content="Маскирует ФИО, email, телефон, паспорт, ИНН, СНИЛС, банковские карты до отправки в OpenAI/Anthropic. В ответе модели — восстанавливает обратно." />
          </span>
          <span id="pii-masking-help" className="text-body-sm text-gray-600">
            Brikko автоматически заменит ФИО, телефоны, email, номера паспортов и ИНН на
            placeholder&apos;ы перед отправкой в OpenAI/Anthropic. Соответствует 152-ФЗ для юрлиц,
            обрабатывающих персональные данные граждан РФ.
          </span>
        </span>
      </label>

      {!available ? (
        <Banner
          variant="info"
          title="Доступно на тарифах Privacy"
          description={
            <>
              PII-маскинг включён на <strong>Pro Privacy</strong> (3 990 ₽/мес) и{' '}
              <strong>Business Privacy</strong> (49 990 ₽/мес). По 152-ФЗ передача ПДн
              граждан РФ в зарубежные LLM требует локализации и согласия субъекта; PII-маскинг
              решает это автоматически.
            </>
          }
          action={
            <Button asChild size="sm">
              <a href="/app/billing">Перейти на Privacy-тариф</a>
            </Button>
          }
        />
      ) : null}
    </div>
  );
}

/**
 * Telegram-бот секция (Sprint 4 / Поток M).
 *
 * UX-обоснование:
 *   - Подключение раскрывается inline (не через modal), потому что пользователю важно держать
 *     контекст: в Settings уже есть несколько одинаково ранжированных секций (privacy / TG /
 *     session). Modal сломал бы поток. Inline — пользователь видит токен и сразу скан/копи.
 *   - Один большой CTA-button «Подключить» вместо двух (QR + текст), потому что 95% пользователей
 *     зайдут с десктопа — выбор «QR vs текст» рендерим уже в раскрытом блоке, а не двумя кнопками.
 *   - Список алертов — простые checkbox'ы; категорий 3, и они независимые. RadioGroup был бы
 *     overkill (и грязный с точки зрения семантики — это не xor-выбор).
 */
function TelegramSection({
  link,
  notifications,
  onAlertChange,
}: {
  link: { linked: boolean; chat_id: string | null } | undefined;
  notifications: TelegramNotificationSettings;
  onAlertChange: (
    key: keyof TelegramNotificationSettings,
    next: boolean,
  ) => Promise<void>;
}) {
  const [linkInfo, setLinkInfo] = useState<TelegramLinkResponse | null>(null);
  const linkTelegram = useLinkTelegram();
  const unlinkTelegram = useUnlinkTelegram();
  const isLinked = Boolean(link?.linked);

  return (
    <Card>
      <CardTitle className="flex items-center gap-2">
        <Send className="h-4 w-4 text-brand-700" strokeWidth={1.75} aria-hidden="true" />
        Telegram-уведомления
        <HelperTooltip content="Привязка аккаунта Brikko к @BrikkoAI_bot. После привязки — алерты о балансе, платежах и подозрительной активности приходят в Telegram." />
      </CardTitle>
      <CardDescription className="mt-1">
        Получай алерты о низком балансе, failover&apos;ах между провайдерами и создании новых
        ключей в Telegram. Бот: @BrikkoAI_bot. Команды: /balance, /usage, /keys, /alerts on/off, /help.
      </CardDescription>

      {!isLinked ? (
        <div className="mt-4 flex flex-col gap-4">
          {!linkInfo ? (
            // §1.6 Telegram empty state — встроено внутрь Card. Иконка MessageCircle
            // и текст «Получать алерты о балансе...» дословно из 11_dashboard_states_and_flows.md.
            <div className="flex items-start gap-3 rounded-md border border-gray-200 bg-gray-50 p-4">
              <MessageCircle className="mt-0.5 h-6 w-6 shrink-0 text-gray-400" strokeWidth={1.5} aria-hidden="true" />
              <div className="flex flex-1 flex-col gap-2">
                <h3 className="text-body font-medium text-gray-900">Подключить Telegram-бота</h3>
                <p className="text-body-sm text-gray-600">
                  Получать алерты о балансе, платежах и подозрительной активности в @BrikkoAI_bot. Настраивается за 30 секунд.
                </p>
                <div className="mt-1 flex flex-wrap items-center gap-2">
                  <Button
                    size="sm"
                    loading={linkTelegram.isPending}
                    onClick={async () => {
                      try {
                        const data = await linkTelegram.mutateAsync();
                        setLinkInfo(data);
                      } catch {
                        toast.error('Сервис временно недоступен. Попробуй позже.');
                      }
                    }}
                    data-testid="telegram-link-button"
                  >
                    Подключить
                  </Button>
                  <a
                    href="/docs/telegram-bot"
                    className="text-body-sm text-brand-600 hover:underline"
                  >
                    Список команд бота
                  </a>
                </div>
              </div>
            </div>
          ) : (
            <TelegramLinkInstructions
              info={linkInfo}
              onCancel={() => setLinkInfo(null)}
            />
          )}
        </div>
      ) : (
        <div className="mt-4 flex flex-col gap-4">
          <div className="flex items-center justify-between rounded-md border border-banner-success-border bg-banner-success-bg px-3 py-2">
            <div className="flex items-center gap-2 text-body-sm !text-banner-success-fg">
              <Check className="h-4 w-4" aria-hidden="true" />
              <span>
                Подключён. Chat ID: <span className="font-mono">{link?.chat_id ?? '—'}</span>
              </span>
            </div>
            <Button
              variant="secondary"
              size="sm"
              loading={unlinkTelegram.isPending}
              onClick={async () => {
                try {
                  await unlinkTelegram.mutateAsync();
                  toast.success('Telegram отключён.');
                } catch {
                  toast.error('Не удалось отключить.');
                }
              }}
            >
              Отключить
            </Button>
          </div>

          <fieldset className="flex flex-col gap-3">
            <legend className="text-body-sm font-medium text-gray-700">
              Что присылать в Telegram
            </legend>
            <AlertCheckbox
              label="Баланс ниже 200 ₽"
              hint="Чтобы не словить 402 в проде."
              checked={notifications.balance_low ?? true}
              onChange={(v) => onAlertChange('balance_low', v)}
            />
            <AlertCheckbox
              label="Failover между провайдерами"
              hint="Когда smart-router переключился с OpenAI на резервного."
              checked={notifications.failover_triggered ?? false}
              onChange={(v) => onAlertChange('failover_triggered', v)}
            />
            <AlertCheckbox
              label="Создание нового API-ключа"
              hint="Защита от компрометации — узнаешь о любом ключе сразу."
              checked={notifications.key_created ?? true}
              onChange={(v) => onAlertChange('key_created', v)}
            />
          </fieldset>
        </div>
      )}
    </Card>
  );
}

function TelegramLinkInstructions({
  info,
  onCancel,
}: {
  info: TelegramLinkResponse;
  onCancel: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const minsLeft = Math.max(1, Math.ceil(info.ttl_seconds / 60));

  return (
    <div className="rounded-md border border-gray-200 bg-gray-50 p-4">
      <p className="text-body-sm text-gray-700">
        Открой бота <strong>@{info.bot_username}</strong> и нажми <strong>Start</strong>. Ссылка
        действует {minsLeft} мин.
      </p>

      <div className="mt-3 flex items-stretch gap-2">
        <code
          className="flex-1 break-all rounded-md border border-gray-200 bg-white px-3 py-2 text-xs font-mono text-gray-800"
          data-testid="telegram-deep-link"
        >
          {info.deep_link}
        </code>
        <Button
          variant="secondary"
          size="sm"
          leftIcon={copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(info.deep_link);
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            } catch {
              toast.error('Не получилось скопировать. Выдели вручную.');
            }
          }}
        >
          {copied ? 'Скопировано' : 'Копировать'}
        </Button>
      </div>

      <div className="mt-4 flex items-center gap-3">
        <Button asChild size="sm">
          <a href={info.deep_link} target="_blank" rel="noopener noreferrer">
            Открыть в Telegram
          </a>
        </Button>
        <Button variant="ghost" size="sm" onClick={onCancel}>
          Отмена
        </Button>
      </div>
    </div>
  );
}

function AlertCheckbox({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint: string;
  checked: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <label className="flex items-start gap-3">
      <input
        type="checkbox"
        className="mt-1 h-4 w-4 rounded border-gray-300 text-brand-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="flex flex-col gap-0.5">
        <span className="text-body text-gray-900">{label}</span>
        <span className="text-body-sm text-gray-500">{hint}</span>
      </span>
    </label>
  );
}
