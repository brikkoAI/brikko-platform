/**
 * PiiCompliance секция (Sprint 4 / V2 USP).
 *
 * Что покрываем:
 *   - Заголовок section'а связан с aria-labelledby (a11y).
 *   - Список из 4 шагов flow рендерится (заведомо столько шагов в спецификации USP).
 *   - 3 trust-points (открытая модель / нет хранения / on-prem).
 *   - В тексте есть упоминание 152-ФЗ или ПДн (smoke на копирайт).
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PiiCompliance } from '@/components/marketing/PiiCompliance';

describe('<PiiCompliance> 152-ФЗ landing section', () => {
  it('a11y: section имеет aria-labelledby связку', () => {
    const { container } = render(<PiiCompliance />);
    const section = container.querySelector('section');
    expect(section).toBeTruthy();
    const labelledBy = section!.getAttribute('aria-labelledby');
    expect(labelledBy).toBe('pii-compliance-heading');
    expect(screen.getByRole('heading', { level: 2 }).id).toBe('pii-compliance-heading');
  });

  it('flow рендерится как ordered list из 4 шагов', () => {
    render(<PiiCompliance />);
    const ol = screen.getByRole('list', { name: /Поток обработки промпта с PII-маскингом/i });
    const items = ol.querySelectorAll('li');
    expect(items).toHaveLength(4);
  });

  it('содержит 3 trust-point articles (открытая модель / не храним / on-prem)', () => {
    render(<PiiCompliance />);
    const articles = screen.getAllByRole('article');
    expect(articles).toHaveLength(3);
  });

  it('копирайт упоминает ПДн или 152-ФЗ', () => {
    render(<PiiCompliance />);
    // Smoke: один из ключевых юр-терминов должен встретиться в тексте.
    const text = document.body.textContent ?? '';
    expect(text).toMatch(/(ПДн|152-ФЗ|персональные данные)/i);
  });
});
