/**
 * <TariffCard> — Sprint 6: визуал и состояния карточки тарифа.
 *
 * Что покрываем:
 *  - Активный тариф юзера → бейдж «Активный», CTA disabled и label «Текущий тариф».
 *  - Pro Privacy emphasis → 152-ФЗ бейдж видим.
 *  - Business+ contactSales → CTA «Связаться с продажами».
 *  - onSelect зовётся с правильным slug'ом.
 *  - Legacy mapping `pro` → `pro_features` — Pro Features подсвечен «активным».
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TariffCard, TARIFF_CATALOG } from '@/components/dashboard/TariffCard';

const PRO_PRIVACY = TARIFF_CATALOG.find((t) => t.slug === 'pro_privacy')!;
const PAYG = TARIFF_CATALOG.find((t) => t.slug === 'payg')!;
const BUSINESS = TARIFF_CATALOG.find((t) => t.slug === 'business')!;
const PRO_FEATURES = TARIFF_CATALOG.find((t) => t.slug === 'pro_features')!;

describe('<TariffCard>', () => {
  it('PAYG юзер на карточке PAYG — Active badge + disabled CTA', () => {
    const onSelect = vi.fn();
    render(<TariffCard data={PAYG} currentTariff="payg" onSelect={onSelect} />);
    expect(screen.getByText('Активный')).toBeInTheDocument();
    const cta = screen.getByTestId('tariff-cta-payg');
    expect(cta).toBeDisabled();
    expect(cta.textContent).toMatch(/Текущий тариф/);
  });

  it('Pro Privacy — 152-ФЗ бейдж видим', () => {
    const onSelect = vi.fn();
    render(<TariffCard data={PRO_PRIVACY} currentTariff="payg" onSelect={onSelect} />);
    expect(screen.getByText('152-ФЗ')).toBeInTheDocument();
  });

  it('Business — CTA «Связаться с продажами», не «Перейти»', () => {
    const onSelect = vi.fn();
    render(<TariffCard data={BUSINESS} currentTariff="payg" onSelect={onSelect} />);
    expect(screen.getByTestId('tariff-cta-business').textContent).toMatch(
      /Связаться с продажами/,
    );
  });

  it('Click на CTA вызывает onSelect с правильным slug', async () => {
    const onSelect = vi.fn();
    render(<TariffCard data={PRO_PRIVACY} currentTariff="payg" onSelect={onSelect} />);
    await userEvent.click(screen.getByTestId('tariff-cta-pro_privacy'));
    expect(onSelect).toHaveBeenCalledWith('pro_privacy');
  });

  it('Legacy `pro` тариф → Pro Features подсвечен как активный', () => {
    // Backend возвращает Account.tariff = 'pro' для pro_features. Карточка должна
    // воспринять это как «текущий», иначе CTA активен и юзер случайно «доплатит».
    const onSelect = vi.fn();
    render(<TariffCard data={PRO_FEATURES} currentTariff={'pro' as 'pro_features'} onSelect={onSelect} />);
    expect(screen.getByText('Активный')).toBeInTheDocument();
    expect(screen.getByTestId('tariff-cta-pro_features')).toBeDisabled();
  });
});
