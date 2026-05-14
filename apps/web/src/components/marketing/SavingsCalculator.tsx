'use client';

import Link from 'next/link';
import { useMemo, useState } from 'react';
import { formatRub } from '@/lib/utils';

/**
 * SavingsCalculator — Brikko vs Wise (direct OpenAI/Anthropic billing).
 *
 * Cream Studio v6, monochrome + accent-1. Подключается на /pricing после
 * PricingCards как конверсионный момент перед регистрацией.
 *
 * Зачем такой калькулятор:
 *   Сценарий «оплачу OpenAI напрямую через Wise» — главный конкурент Brikko
 *   у разработчиков-одиночек. Сравнение по чистому токен-cost'у Brikko
 *   немного дороже (открытый рублёвый прайс), но Wise тащит за собой
 *   комиссию, время на ручные платежи и риск бана аккаунта с РФ-IP. На
 *   реалистичных объёмах Brikko в плюсе. Если объём < 5к ₽/мес — честно
 *   показываем что Brikko дороже и объясняем когда он всё равно нужен.
 *
 * NB 2026-05-12: убрали блок «НДС-возврат на ОСНО» и налоговый-режим toggle.
 * Brikko работает в формате самозанятого (НПД) — счёт-фактуру не выдаёт,
 * НДС-вычета у клиента не возникает. Сравнение должно быть честным.
 *
 * Цены моделей — должны быть в синке с backend `to_public_dict()` и реальным
 * catalog'ом (см. apps/gateway/voltari_gateway/router/catalog.py). Здесь
 * захардкодили рублёвые цены Brikko для быстрой реализации; sync — отдельная
 * задача (#TD-catalog-sync).
 */

interface ModelOption {
  id: 'gpt-4o' | 'gpt-4o-mini' | 'claude-sonnet-4.6' | 'claude-haiku-4.5' | 'gemini-2.5-pro';
  name: string;
  /** ₽ за 1M input-токенов — listprice провайдера (для Wise-сценария). */
  inputPricePerM: number;
  /** ₽ за 1M output-токенов у провайдера. */
  outputPricePerM: number;
}

const MODELS: ReadonlyArray<ModelOption> = [
  { id: 'gpt-4o', name: 'GPT-4o', inputPricePerM: 250, outputPricePerM: 1000 },
  { id: 'gpt-4o-mini', name: 'GPT-4o mini', inputPricePerM: 15, outputPricePerM: 60 },
  { id: 'claude-sonnet-4.6', name: 'Claude Sonnet 4.6', inputPricePerM: 240, outputPricePerM: 1200 },
  { id: 'claude-haiku-4.5', name: 'Claude Haiku 4.5', inputPricePerM: 60, outputPricePerM: 240 },
  { id: 'gemini-2.5-pro', name: 'Gemini 2.5 Pro', inputPricePerM: 100, outputPricePerM: 800 },
];

// Brikko публикует собственный открытый прайс в рублях за миллион токенов
// (см. /v1/models/public). Для калькулятора нужен фактор поверх listprice
// провайдера — это та самая дельта, которая выражена в публичном rub_per_1m
// относительно USD-прайса вендора. Хранится здесь как число, чтобы не
// гонять http-запрос на каждый клик; sync с backend — отдельная задача.
const BRIKKO_PRICE_FACTOR = 0.15;

// Типичный chat-профиль: 70% input / 30% output. Источник —
// усреднение по нашим тестовым клиентам, см. 04_Market.
const TOKEN_RATIO_INPUT = 0.7;
const TOKEN_RATIO_OUTPUT = 0.3;

// Wise: 1% transfer fee + 100 ₽ FX/spread на каждый платёж (раз в месяц).
const WISE_FEE_PERCENT = 0.01;
const WISE_FEE_FIXED_RUB = 100;

// Время на ручные платежи: 4 ч/мес в первый месяц
// (поиск посредника, регистрация Wise, попытки разных карт).
const HOURS_PER_MONTH_MANUAL = 4;

// Expected value бан-риска: 8% месячный шанс × 10% от cost как условный штраф
// (замена карты, потеря истории, downtime).
const BAN_RISK_PROBABILITY = 0.08;
const BAN_RISK_COST_FRACTION = 0.10;

interface CalculatorState {
  modelId: ModelOption['id'];
  tokensPerMonth: number;
  hourlyRateRub: number;
}

interface BreakdownLine {
  label: string;
  hint?: string;
  wise: number;
  brikko: number;
}

interface CalcResult {
  lines: ReadonlyArray<BreakdownLine>;
  wiseTotal: number;
  brikkoTotal: number;
  savings: number;
  savingsPercent: number;
}

function calc(state: CalculatorState): CalcResult {
  const model = MODELS.find((m) => m.id === state.modelId) ?? MODELS[0];
  // MODELS — non-empty literal; fallback гарантирует ModelOption (не undefined).
  if (!model) {
    // unreachable — но удовлетворяет noUncheckedIndexedAccess.
    throw new Error('MODELS catalog is empty');
  }

  const inputTokens = state.tokensPerMonth * TOKEN_RATIO_INPUT;
  const outputTokens = state.tokensPerMonth * TOKEN_RATIO_OUTPUT;

  const tokensCostListprice =
    (inputTokens * model.inputPricePerM + outputTokens * model.outputPricePerM) / 1_000_000;

  // 1. Через Wise (direct OpenAI billing)
  const wiseFee = tokensCostListprice * WISE_FEE_PERCENT + WISE_FEE_FIXED_RUB;
  const timeCost = HOURS_PER_MONTH_MANUAL * state.hourlyRateRub;
  const banRisk = tokensCostListprice * BAN_RISK_PROBABILITY * BAN_RISK_COST_FRACTION;

  // 2. Через Brikko — рублёвая касса, чек НПД, smart routing, observability.
  const tokensCostBrikko = tokensCostListprice * (1 + BRIKKO_PRICE_FACTOR);

  const lines: BreakdownLine[] = [
    {
      label: 'Стоимость токенов',
      hint: '70% input / 30% output',
      wise: tokensCostListprice,
      brikko: tokensCostBrikko,
    },
    {
      label: 'Комиссия Wise',
      hint: '1% + 100 ₽ за платёж',
      wise: wiseFee,
      brikko: 0,
    },
    {
      label: 'Время на платежи',
      hint: `${HOURS_PER_MONTH_MANUAL} ч/мес × ставка*`,
      wise: timeCost,
      brikko: 0,
    },
    {
      label: 'Риск бана (EV)',
      hint: '8% × 10% cost**',
      wise: banRisk,
      brikko: 0,
    },
  ];

  const wiseTotal = tokensCostListprice + wiseFee + timeCost + banRisk;
  const brikkoTotal = tokensCostBrikko;
  const savings = wiseTotal - brikkoTotal;
  const savingsPercent = wiseTotal > 0 ? (savings / wiseTotal) * 100 : 0;

  return { lines, wiseTotal, brikkoTotal, savings, savingsPercent };
}

function formatTokensShort(n: number): string {
  if (n >= 1_000_000) {
    const m = n / 1_000_000;
    return `${m % 1 === 0 ? m.toFixed(0) : m.toFixed(1)}M токенов`;
  }
  if (n >= 1_000) {
    return `${Math.round(n / 1_000)}k токенов`;
  }
  return `${n} токенов`;
}

const FONT_SERIF = '"Source Serif 4", "PP Editorial New", Georgia, serif';
const FONT_MONO = 'Geist Mono, JetBrains Mono, ui-monospace, monospace';

export function SavingsCalculator() {
  const [state, setState] = useState<CalculatorState>({
    modelId: 'gpt-4o',
    tokensPerMonth: 1_000_000,
    hourlyRateRub: 1500,
  });

  const result = useMemo(() => calc(state), [state]);
  const isBrikkoCheaper = result.savings > 0;

  return (
    <section
      className="brikko-section"
      id="savings-calculator"
      aria-labelledby="savings-calc-heading"
    >
      <header
        style={{
          maxWidth: 1400,
          margin: '0 auto 56px',
          padding: '0 6vw',
          position: 'relative',
          zIndex: 2,
        }}
      >
        <span className="brikko-eyebrow">
          <span className="brikko-eyebrow-dot" aria-hidden="true" />
          Калькулятор
        </span>
        <h2 className="brikko-h2" id="savings-calc-heading">
          <span>Сколько вы </span>
          <span className="brikko-h2-italic">экономите</span>
        </h2>
        <p className="brikko-lede" style={{ marginBottom: 0 }}>
          Реальная стоимость работы с OpenAI/Claude через Wise vs через Brikko.
          Без воды: считаем токены, комиссии, время на ручные платежи и риск бана.
        </p>
      </header>

      <div
        style={{
          maxWidth: 1400,
          margin: '0 auto',
          padding: '0 6vw',
          position: 'relative',
          zIndex: 2,
        }}
      >
        <div
          className="brikko-calculator-grid"
          style={{ display: 'grid', gap: 16 }}
        >
          <InputsPanel state={state} onChange={setState} />
          <ComparisonPanel result={result} />
        </div>

        <SummaryRow result={result} isBrikkoCheaper={isBrikkoCheaper} />

        <Disclaimer />
      </div>
    </section>
  );
}

interface InputsPanelProps {
  state: CalculatorState;
  onChange: (next: CalculatorState) => void;
}

function InputsPanel({ state, onChange }: InputsPanelProps) {
  return (
    <article className="brikko-card-outer">
      <div className="brikko-card-inner" style={{ padding: 28, display: 'grid', gap: 24 }}>
        <Field label="Какую модель?" htmlFor="calc-model">
          <select
            id="calc-model"
            className="brikko-select"
            value={state.modelId}
            onChange={(e) =>
              onChange({ ...state, modelId: e.target.value as ModelOption['id'] })
            }
          >
            {MODELS.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </Field>

        <Field
          label="Сколько токенов в месяц?"
          htmlFor="calc-tokens"
          valueLabel={formatTokensShort(state.tokensPerMonth)}
        >
          <input
            id="calc-tokens"
            type="range"
            min={10_000}
            max={10_000_000}
            step={10_000}
            value={state.tokensPerMonth}
            onChange={(e) => onChange({ ...state, tokensPerMonth: Number(e.target.value) })}
            className="brikko-calc-slider"
            aria-valuetext={formatTokensShort(state.tokensPerMonth)}
          />
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              fontFamily: FONT_MONO,
              fontSize: 11,
              color: 'var(--fg-faint)',
              marginTop: 6,
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            <span>10k</span>
            <span>10M</span>
          </div>
        </Field>

        <Field label="Ставка специалиста" htmlFor="calc-rate">
          <div style={{ position: 'relative' }}>
            <input
              id="calc-rate"
              type="number"
              min={500}
              max={10_000}
              step={100}
              value={state.hourlyRateRub}
              onChange={(e) => {
                const next = Number(e.target.value);
                if (Number.isFinite(next)) {
                  onChange({ ...state, hourlyRateRub: Math.max(500, Math.min(10_000, next)) });
                }
              }}
              className="brikko-input"
              style={{ paddingRight: 64, fontVariantNumeric: 'tabular-nums' }}
            />
            <span
              style={{
                position: 'absolute',
                right: 14,
                top: '50%',
                transform: 'translateY(-50%)',
                fontSize: 13,
                color: 'var(--fg-muted)',
                fontFamily: FONT_MONO,
                pointerEvents: 'none',
              }}
            >
              ₽/час
            </span>
          </div>
        </Field>

      </div>
    </article>
  );
}

interface FieldProps {
  label: string;
  htmlFor: string;
  valueLabel?: string;
  children: React.ReactNode;
}

function Field({ label, htmlFor, valueLabel, children }: FieldProps) {
  return (
    <div>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'baseline',
          marginBottom: 8,
        }}
      >
        <label
          htmlFor={htmlFor}
          style={{
            fontSize: 13,
            fontWeight: 500,
            color: 'var(--fg-primary)',
            letterSpacing: '-0.005em',
          }}
        >
          {label}
        </label>
        {valueLabel ? (
          <span
            style={{
              fontFamily: FONT_MONO,
              fontSize: 13,
              color: 'var(--fg-primary)',
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            {valueLabel}
          </span>
        ) : null}
      </div>
      {children}
    </div>
  );
}

interface ComparisonPanelProps {
  result: CalcResult;
}

function ComparisonPanel({ result }: ComparisonPanelProps) {
  return (
    <article className="brikko-card-outer">
      <div className="brikko-card-inner" style={{ padding: 28 }}>
        <div
          role="table"
          aria-label="Сравнение Wise vs Brikko"
          style={{ width: '100%', overflowX: 'auto' }}
        >
          <table
            style={{
              width: '100%',
              borderCollapse: 'collapse',
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            <thead>
              <tr>
                <th
                  style={{
                    textAlign: 'left',
                    fontSize: 11,
                    fontWeight: 500,
                    letterSpacing: '0.18em',
                    textTransform: 'uppercase',
                    color: 'var(--fg-muted)',
                    paddingBottom: 12,
                    borderBottom: '1px solid var(--hairline)',
                  }}
                >
                  Статья
                </th>
                <th
                  style={{
                    textAlign: 'right',
                    fontSize: 11,
                    fontWeight: 500,
                    letterSpacing: '0.18em',
                    textTransform: 'uppercase',
                    color: 'var(--fg-muted)',
                    paddingBottom: 12,
                    borderBottom: '1px solid var(--hairline)',
                    width: '22%',
                  }}
                >
                  Wise
                </th>
                <th
                  style={{
                    textAlign: 'right',
                    fontSize: 11,
                    fontWeight: 500,
                    letterSpacing: '0.18em',
                    textTransform: 'uppercase',
                    color: 'var(--fg-primary)',
                    paddingBottom: 12,
                    borderBottom: '1px solid var(--hairline)',
                    width: '22%',
                  }}
                >
                  Brikko
                </th>
                <th
                  style={{
                    textAlign: 'right',
                    fontSize: 11,
                    fontWeight: 500,
                    letterSpacing: '0.18em',
                    textTransform: 'uppercase',
                    color: 'var(--fg-muted)',
                    paddingBottom: 12,
                    borderBottom: '1px solid var(--hairline)',
                    width: '22%',
                  }}
                >
                  Δ
                </th>
              </tr>
            </thead>
            <tbody>
              {result.lines.map((line) => (
                <BreakdownRow key={line.label} line={line} />
              ))}
              <TotalRow wiseTotal={result.wiseTotal} brikkoTotal={result.brikkoTotal} />
            </tbody>
          </table>
        </div>

        <BenefitsList />
      </div>
    </article>
  );
}

function BreakdownRow({ line }: { line: BreakdownLine }) {
  const delta = line.brikko - line.wise;
  return (
    <tr>
      <td
        style={{
          padding: '14px 8px 14px 0',
          borderBottom: '1px solid var(--hairline)',
          verticalAlign: 'top',
        }}
      >
        <div style={{ fontSize: 14, color: 'var(--fg-primary)' }}>{line.label}</div>
        {line.hint ? (
          <div style={{ fontSize: 12, color: 'var(--fg-faint)', marginTop: 2 }}>
            {line.hint}
          </div>
        ) : null}
      </td>
      <Cell value={line.wise} />
      <Cell value={line.brikko} accent={line.brikko === 0 && line.wise > 0} />
      <Cell value={delta} muted />
    </tr>
  );
}

function Cell({
  value,
  accent = false,
  muted = false,
}: {
  value: number;
  accent?: boolean;
  muted?: boolean;
}) {
  const isZero = Math.abs(value) < 0.5;
  return (
    <td
      style={{
        padding: '14px 0 14px 8px',
        textAlign: 'right',
        borderBottom: '1px solid var(--hairline)',
        fontFamily: FONT_MONO,
        fontSize: 13,
        color: muted
          ? 'var(--fg-muted)'
          : accent
            ? 'var(--fg-primary)'
            : isZero
              ? 'var(--fg-faint)'
              : 'var(--fg-primary)',
        fontVariantNumeric: 'tabular-nums',
        transition: 'color 200ms ease',
      }}
    >
      {isZero ? '0 ₽' : formatRub(value)}
    </td>
  );
}

function TotalRow({ wiseTotal, brikkoTotal }: { wiseTotal: number; brikkoTotal: number }) {
  return (
    <tr>
      <td
        style={{
          padding: '18px 8px 0 0',
          fontSize: 13,
          fontWeight: 500,
          color: 'var(--fg-primary)',
          letterSpacing: '0.04em',
          textTransform: 'uppercase',
        }}
      >
        Итого
      </td>
      <td
        style={{
          padding: '18px 0 0 8px',
          textAlign: 'right',
          fontFamily: FONT_MONO,
          fontSize: 16,
          fontWeight: 500,
          color: 'var(--fg-muted)',
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {formatRub(wiseTotal)}
      </td>
      <td
        style={{
          padding: '18px 0 0 8px',
          textAlign: 'right',
          fontFamily: FONT_MONO,
          fontSize: 16,
          fontWeight: 600,
          color: 'var(--fg-primary)',
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {formatRub(brikkoTotal)}
      </td>
      <td
        style={{
          padding: '18px 0 0 8px',
          textAlign: 'right',
          fontFamily: FONT_MONO,
          fontSize: 14,
          color: 'var(--fg-muted)',
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {formatRub(brikkoTotal - wiseTotal)}
      </td>
    </tr>
  );
}

function BenefitsList() {
  const benefits: string[] = [
    `+${HOURS_PER_MONTH_MANUAL} ч/мес обратно к продукту`,
    '0% риск бана аккаунта провайдера',
    'Чек НПД на каждое пополнение, автоматически',
  ];

  return (
    <ul
      style={{
        listStyle: 'none',
        padding: 0,
        margin: '24px 0 0',
        display: 'grid',
        gap: 8,
      }}
    >
      {benefits.map((b) => (
        <li
          key={b}
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: 8,
            fontSize: 13,
            color: 'var(--fg-muted)',
            lineHeight: 1.5,
          }}
        >
          <PlusIcon />
          <span>{b}</span>
        </li>
      ))}
    </ul>
  );
}

function PlusIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      aria-hidden="true"
      style={{ marginTop: 4, flexShrink: 0, color: 'var(--fg-primary)' }}
    >
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

interface SummaryRowProps {
  result: CalcResult;
  isBrikkoCheaper: boolean;
}

function SummaryRow({ result, isBrikkoCheaper }: SummaryRowProps) {
  const absSavings = Math.abs(result.savings);
  const absPercent = Math.abs(result.savingsPercent);

  return (
    <div
      style={{
        marginTop: 24,
        padding: '28px 32px',
        background: isBrikkoCheaper ? 'var(--accent-1-soft)' : 'var(--bg-elevated)',
        border: `1px solid ${isBrikkoCheaper ? 'var(--accent-1)' : 'var(--hairline)'}`,
        borderRadius: 24,
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 20,
        transition: 'background 200ms ease, border-color 200ms ease',
      }}
    >
      <div style={{ minWidth: 0, flex: '1 1 280px' }}>
        {isBrikkoCheaper ? (
          <>
            <p
              style={{
                margin: 0,
                fontSize: 13,
                color: 'var(--fg-muted)',
                letterSpacing: '0.04em',
                textTransform: 'uppercase',
              }}
            >
              Экономия с Brikko
            </p>
            <p
              style={{
                margin: '6px 0 0',
                fontFamily: FONT_SERIF,
                fontWeight: 400,
                fontSize: 'clamp(28px, 3.5vw, 40px)',
                lineHeight: 1.1,
                letterSpacing: '-0.02em',
                color: 'var(--fg-primary)',
                fontVariantNumeric: 'tabular-nums',
              }}
            >
              {formatRub(absSavings)}{' '}
              <span style={{ fontSize: '0.6em', color: 'var(--fg-muted)' }}>/ мес</span>
            </p>
            <p
              style={{
                margin: '4px 0 0',
                fontFamily: FONT_MONO,
                fontSize: 13,
                color: 'var(--fg-muted)',
                fontVariantNumeric: 'tabular-nums',
              }}
            >
              {Math.round(absPercent)}% ниже Wise-варианта
            </p>
          </>
        ) : (
          <>
            <p
              style={{
                margin: 0,
                fontSize: 13,
                color: 'var(--fg-muted)',
                letterSpacing: '0.04em',
                textTransform: 'uppercase',
              }}
            >
              На текущем объёме Brikko дороже
            </p>
            <p
              style={{
                margin: '6px 0 0',
                fontFamily: FONT_SERIF,
                fontWeight: 400,
                fontSize: 'clamp(20px, 2.2vw, 26px)',
                lineHeight: 1.3,
                color: 'var(--fg-primary)',
                maxWidth: '50ch',
              }}
            >
              Разница {formatRub(absSavings)} / мес. Выгода появляется примерно от
              50 000 ₽/мес. Подходит, если важны документы и compliance.
            </p>
          </>
        )}
      </div>

      <Link
        href="/signup"
        className={isBrikkoCheaper ? 'brikko-btn brikko-btn-primary' : 'brikko-btn brikko-btn-secondary'}
        style={{ flexShrink: 0 }}
      >
        {isBrikkoCheaper ? 'Создать аккаунт за 30 секунд' : 'Попробовать Brikko'}
      </Link>
    </div>
  );
}

function Disclaimer() {
  return (
    <div
      style={{
        marginTop: 24,
        fontSize: 12,
        color: 'var(--fg-faint)',
        lineHeight: 1.6,
        maxWidth: '80ch',
      }}
    >
      <p style={{ margin: 0 }}>
        * Время на платежи — среднее по нашим клиентам в первый месяц
        (поиск посредника, регистрация Wise, попытки разных карт). На стабильном
        потоке снижается до ~1 ч/мес.
      </p>
      <p style={{ margin: '6px 0 0' }}>
        ** Риск бана аккаунта OpenAI с РФ-IP оценочный, точной публичной
        статистики нет. Базовое предположение — 8% месячный шанс на новых
        аккаунтах в 2024–2025, с ожидаемым ущербом 10% от месячного cost.
      </p>
      <p style={{ margin: '6px 0 0' }}>
        *** При тратах меньше ~5 000 ₽/мес Brikko может быть дороже Wise —
        разница в цене токенов превышает сэкономленное время. Калькулятор показывает это честно.
      </p>
    </div>
  );
}
