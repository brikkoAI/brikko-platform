import type { Config } from 'tailwindcss';
import colors from 'tailwindcss/colors';

const config: Config = {
  content: ['./src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Cream Studio v6 semantic tokens (Sprint 13). CSS-vars определены в
        // src/app/globals.css. Используются в (marketing)/* — переключаются
        // светлая/тёмная тема через [data-theme="dark"] на <html>.
        bg: {
          base: 'var(--bg-base)',
          elevated: 'var(--bg-elevated)',
          'tier-2': 'var(--bg-tier-2)',
        },
        fg: {
          primary: 'var(--fg-primary)',
          muted: 'var(--fg-muted)',
          faint: 'var(--fg-faint)',
        },
        accent: {
          1: 'var(--accent-1)',
          '1-hover': 'var(--accent-1-hover)',
          2: 'var(--accent-2)',
        },
        // Контрастный цвет текста на brand-600 кнопках. Инвертируется через
        // CSS-переменную в [data-theme="dark"] — никакого dark: variant.
        'on-accent': 'var(--on-accent)',
        hairline: 'var(--hairline)',
        'hairline-hi': 'var(--hairline-hi)',
        // Brand: indigo (09_design_system §1.2)
        // Расширено 2026-05-01: brand-200/300/400/500/800 использовались в
        // 9 файлах (Hero, ModelsFilters, ModelCard, pricing, smart-routing,
        // cookbook), но Tailwind молча дропал их без CSS-переменных. Теперь
        // полная палитра 50-900 (значения из стандартного indigo Tailwind).
        brand: {
          50: 'rgb(var(--brand-50) / <alpha-value>)',
          100: 'rgb(var(--brand-100) / <alpha-value>)',
          200: 'rgb(var(--brand-200) / <alpha-value>)',
          300: 'rgb(var(--brand-300) / <alpha-value>)',
          400: 'rgb(var(--brand-400) / <alpha-value>)',
          500: 'rgb(var(--brand-500) / <alpha-value>)',
          600: 'rgb(var(--brand-600) / <alpha-value>)',
          700: 'rgb(var(--brand-700) / <alpha-value>)',
          800: 'rgb(var(--brand-800) / <alpha-value>)',
          900: 'rgb(var(--brand-900) / <alpha-value>)',
        },
        success: {
          50: 'rgb(var(--success-50) / <alpha-value>)',
          200: 'rgb(var(--success-200) / <alpha-value>)',
          600: 'rgb(var(--success-600) / <alpha-value>)',
        },
        warning: {
          50: 'rgb(var(--warning-50) / <alpha-value>)',
          200: 'rgb(var(--warning-200) / <alpha-value>)',
          600: 'rgb(var(--warning-600) / <alpha-value>)',
        },
        // Banner-*: invert-able через CSS-переменные на data-theme="dark".
        // light → cream-tinted bg + espresso text; dark → deep semantic bg
        // + light text. Без этого светлые fg-50 теряются в dark-shell.
        'banner-warning': {
          bg: 'rgb(var(--banner-warning-bg) / <alpha-value>)',
          border: 'rgb(var(--banner-warning-border) / <alpha-value>)',
          fg: 'rgb(var(--banner-warning-fg) / <alpha-value>)',
          'fg-muted': 'rgb(var(--banner-warning-fg-muted) / <alpha-value>)',
        },
        'banner-info': {
          bg: 'rgb(var(--banner-info-bg) / <alpha-value>)',
          border: 'rgb(var(--banner-info-border) / <alpha-value>)',
          fg: 'rgb(var(--banner-info-fg) / <alpha-value>)',
          'fg-muted': 'rgb(var(--banner-info-fg-muted) / <alpha-value>)',
        },
        'banner-success': {
          bg: 'rgb(var(--banner-success-bg) / <alpha-value>)',
          border: 'rgb(var(--banner-success-border) / <alpha-value>)',
          fg: 'rgb(var(--banner-success-fg) / <alpha-value>)',
          'fg-muted': 'rgb(var(--banner-success-fg-muted) / <alpha-value>)',
        },
        'banner-error': {
          bg: 'rgb(var(--banner-error-bg) / <alpha-value>)',
          border: 'rgb(var(--banner-error-border) / <alpha-value>)',
          fg: 'rgb(var(--banner-error-fg) / <alpha-value>)',
          'fg-muted': 'rgb(var(--banner-error-fg-muted) / <alpha-value>)',
        },
        error: {
          50: 'rgb(var(--error-50) / <alpha-value>)',
          200: 'rgb(var(--error-200) / <alpha-value>)',
          600: 'rgb(var(--error-600) / <alpha-value>)',
        },
        info: {
          50: 'rgb(var(--info-50) / <alpha-value>)',
          600: 'rgb(var(--info-600) / <alpha-value>)',
        },
        // Neutrals: slate, not gray (09_design_system §1.4)
        gray: colors.slate,
      },
      fontFamily: {
        sans: ['var(--font-sans)', 'system-ui', 'sans-serif'],
        mono: ['var(--font-mono)', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      fontSize: {
        body: ['0.9375rem', { lineHeight: '1.55' }],
        'body-large': ['1.0625rem', { lineHeight: '1.55' }],
        'body-sm': ['0.8125rem', { lineHeight: '1.5' }],
        code: ['0.8125rem', { lineHeight: '1.5' }],
      },
      boxShadow: {
        sm: '0 1px 2px 0 rgb(15 23 42 / 0.04)',
        md: '0 4px 12px -2px rgb(15 23 42 / 0.08), 0 2px 4px -2px rgb(15 23 42 / 0.04)',
        lg: '0 4px 12px -2px rgb(15 23 42 / 0.08), 0 2px 4px -2px rgb(15 23 42 / 0.04)',
        xl: '0 4px 12px -2px rgb(15 23 42 / 0.08), 0 2px 4px -2px rgb(15 23 42 / 0.04)',
        '2xl': 'none',
      },
    },
  },
  plugins: [],
};

export default config;
