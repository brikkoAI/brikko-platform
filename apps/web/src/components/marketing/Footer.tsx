import Link from 'next/link';
import type { Route } from 'next';
import { BRAND } from '@/lib/brand';

/**
 * Marketing footer — Cream Studio v6 (Sprint 13, 2026-05-02).
 *
 * Strict grayscale, hairline borders, serif brand-mark, semantic muted-colors.
 * Все классы префикснуты `brikko-*` (см. apps/web/src/app/brikko-marketing.css).
 */
export function Footer() {
  return (
    <footer className="brikko-footer" aria-label="Brikko footer">
      <div className="brikko-footer-grid">
        <div>
          <span className="brikko-footer-brand">{BRAND.name}</span>
          <p className="brikko-footer-tagline">
            38 LLM через один OpenAI-совместимый ключ. Оплата в рублях,
            чек самозанятого после каждого пополнения.
          </p>
        </div>

        <FooterColumn title="Продукт">
          <FooterLink href="/pricing">Тарифы</FooterLink>
          <FooterLink href="/models">Каталог моделей</FooterLink>
          <FooterLink href="/integrations">Интеграции</FooterLink>
          <FooterLink href="/mcp">MCP для Claude/Cursor</FooterLink>
          <FooterLink href="/playground">Playground</FooterLink>
          <FooterLink href="/docs">Документация</FooterLink>
          <FooterLink href="/docs/cookbook">Cookbook</FooterLink>
          <FooterLink href="/docs/smart-routing">Smart Router</FooterLink>
          <FooterLink href="/faq">FAQ</FooterLink>
          <FooterLink href="/status">Статус сервиса</FooterLink>
        </FooterColumn>

        <FooterColumn title="Юридическое">
          <FooterLink href="/legal/info">Реквизиты</FooterLink>
          <FooterLink href="/legal/oferta">Оферта</FooterLink>
          <FooterLink href="/legal/privacy">Конфиденциальность</FooterLink>
          <FooterLink href="/legal/cookie">Cookie</FooterLink>
          <FooterLink href="/legal/152-fz">152-ФЗ и AI</FooterLink>
        </FooterColumn>

        <FooterColumn title="Контакты">
          <FooterLink href={`mailto:${BRAND.supportEmail}`}>
            {BRAND.supportEmail}
          </FooterLink>
          {BRAND.legalEntity && BRAND.inn ? (
            <li className="text-fg-faint" style={{ fontSize: 13, lineHeight: 1.55, color: 'var(--fg-faint)' }}>
              {BRAND.legalEntity}
              <br />
              ИНН: {BRAND.inn}
            </li>
          ) : null}
        </FooterColumn>
      </div>

      {/*
        Блок «Способы оплаты». Подключены ЮKassa-методы для шопа 1345959
        (одобрено 2026-05-08): банковские карты (Мир/Visa/MC), СБП,
        T-Pay, SberPay. Сейчас текст-only, без логотипов — НСПК требует
        добавлять лого СБП ТОЛЬКО если рядом стоят логотипы других
        методов; пока стоят только названия — формально совместимо с
        брендбуком.
        TODO (когда будем обновлять визуал): заменить текст на SVG-логотипы
        по брендбуку НСПК (стр. 37-38), скачать с nspk.ru/logo, плюс
        логотипы T-Pay (Т-Банк) и SberPay (Сбербанк).
      */}
      <div className="brikko-footer-payments" aria-label="Способы оплаты">
        <span className="brikko-footer-payments-label">Способы оплаты:</span>
        <span className="brikko-footer-payments-list">
          <span>Карты Мир / Visa / Mastercard</span>
          <span aria-hidden="true">·</span>
          <span>СБП</span>
          <span aria-hidden="true">·</span>
          <span>T-Pay</span>
          <span aria-hidden="true">·</span>
          <span>SberPay</span>
        </span>
        <span className="brikko-footer-payments-via">через ЮKassa</span>
      </div>

      <div className="brikko-footer-bottom">
        <span>© {new Date().getFullYear()} {BRAND.name}</span>
        <span className="brikko-footer-domain">{BRAND.domain}</span>
      </div>
    </footer>
  );
}

function FooterColumn({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="brikko-footer-col-title">{title}</h3>
      <ul className="brikko-footer-list">{children}</ul>
    </div>
  );
}

function FooterLink({ href, children }: { href: string; children: React.ReactNode }) {
  const isExternal = /^(mailto:|tel:|https?:)/.test(href);
  if (isExternal) {
    return (
      <li>
        <a href={href} className="brikko-footer-link">
          {children}
        </a>
      </li>
    );
  }
  return (
    <li>
      <Link href={href as Route} className="brikko-footer-link">
        {children}
      </Link>
    </li>
  );
}
