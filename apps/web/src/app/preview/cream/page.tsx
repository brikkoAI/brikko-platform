'use client';

/**
 * /preview/cream — Cream Studio v3 (strict grayscale + theme toggle).
 *
 * v5: drop terracotta/sage. White-gray (light) + gray-white (dark).
 * Linear / Vercel / Anthropic-dark уровень минимализма.
 *
 * Canvas particle system:
 *  - Particles спавнятся в spawn-zone слева ("clients") → летят к hub в центре
 *    (Brikko router) → перенаправляются в одну из 6 model-нод справа →
 *    ответ возвращается через hub в исходную клиент-точку.
 *  - 4 фазы: REQUEST (accent-1) → HUB PULSE → DISPATCH (fg-muted) → RESPONSE (fg-primary).
 *  - Цвета считываются из CSS-vars через JS-bridge при init и theme change.
 *  - Static positions: spawn-zone, hub, 6 model-nodes — пересчитываются на resize.
 *  - prefers-reduced-motion → canvas скрыт.
 *
 * Theme toggle: localStorage('brikko-theme') | system (prefers-color-scheme).
 * Применяется через document.documentElement[data-theme].
 *
 * Top-bar: brand слева, hamburger top-center, theme-toggle + login справа.
 * Hero: Editorial Split (текст слева, live code-window справа).
 * CTA-trail: Catmull-Rom spline через 8-point trailing buffer, accent-1 colored.
 */

import { useEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { Source_Serif_4, Manrope, JetBrains_Mono } from 'next/font/google';
import styles from './page.module.css';
import { LiveStatusWidget } from './_components/LiveStatusWidget';
import { RouterDemo } from './_components/RouterDemo';
import { CursorDot } from './_components/CursorDot';
import { BootSequence } from './_components/BootSequence';
import { ReadingProgress } from './_components/ReadingProgress';
import { PageTransitionOrchestrator } from './_components/PageTransitionLink';

const serif = Source_Serif_4({
  subsets: ['latin', 'cyrillic-ext'],
  weight: ['300', '400', '500'],
  style: ['normal', 'italic'],
  variable: '--font-cream-serif',
  display: 'swap',
});

const sans = Manrope({
  subsets: ['latin', 'cyrillic-ext'],
  weight: ['400', '500', '600'],
  variable: '--font-cream-sans',
  display: 'swap',
});

const mono = JetBrains_Mono({
  subsets: ['latin', 'cyrillic-ext'],
  weight: ['400', '500'],
  variable: '--font-cream-mono',
  display: 'swap',
});

type Theme = 'light' | 'dark';

const HERO_LINES: { words: { text: string; accent?: boolean; italic?: boolean }[] }[] = [
  {
    words: [
      { text: 'Один' },
      { text: 'API.' },
    ],
  },
  {
    words: [
      { text: '38', italic: true },
      { text: 'LLM.' },
    ],
  },
  {
    words: [
      { text: 'Рублёвая', accent: true, italic: true },
      { text: 'касса.', accent: true },
    ],
  },
];

const MENU_LINKS = [
  { href: '#proof', label: 'Цифры' },
  { href: '#models', label: 'Модели' },
  { href: '#why', label: 'Почему Brikko' },
  { href: '#cookbook', label: 'Рецепты' },
  { href: '/pricing', label: 'Тарифы' },
  { href: '/docs', label: 'Документация' },
];

/**
 * Models showcase — 6 hand-picked из 38 visible на проде. Цены — RUB/1M по
 * открытому прайс-листу Brikko (см. models-fallback.ts). Если backend
 * изменит прайсы — этот массив рассинхронизируется с /models, но для
 * preview-лендинга 6 hand-picked примеров достаточно. Полный каталог —
 * на /models.
 */
type ShowcaseModel = {
  id: string;
  provider: string;
  inputRub: number;
  outputRub: number;
};

const SHOWCASE_MODELS: ShowcaseModel[] = [
  { id: 'GPT-5.5', provider: 'OpenAI', inputRub: 460, outputRub: 2760 },
  { id: 'Claude Opus 4.7', provider: 'Anthropic', inputRub: 1380, outputRub: 6900 },
  { id: 'Gemini 3.1 Pro', provider: 'Google', inputRub: 184, outputRub: 1104 },
  { id: 'DeepSeek V4 Flash', provider: 'DeepSeek', inputRub: 13, outputRub: 26 },
  { id: 'YandexGPT 5.1 Pro', provider: 'Яндекс', inputRub: 604, outputRub: 604 },
  { id: 'GigaChat 2 Max', provider: 'Сбер', inputRub: 1283, outputRub: 1283 },
];

/**
 * Soon-карточки — easter-egg в каталоге, показывают что Brikko расширяется
 * за пределы text-LLM. Audio (Whisper, TTS), image (DALL-E), realtime voice.
 * Цены — placeholder; точные значения зафиксируем перед релизом каждой модели.
 */
type SoonModel = {
  id: string;
  provider: string;
  modality: string;
  price: string;
  note: string;
  badge: string;
};

const SOON_MODELS: SoonModel[] = [
  {
    id: 'Whisper STT',
    provider: 'OpenAI',
    modality: 'audio→text',
    price: '~55 коп / минута',
    note: 'Транскрипция аудио. Уже в beta.',
    badge: 'Скоро · Q2 2026',
  },
  {
    id: 'OpenAI TTS-1',
    provider: 'OpenAI',
    modality: 'text→audio',
    price: '1200 коп / 1M chars',
    note: 'Синтез речи. Estimated Q3 2026.',
    badge: 'Скоро · Q3 2026',
  },
  {
    id: 'DALL-E 3',
    provider: 'OpenAI',
    modality: 'text→image',
    price: '~3.2 ₽ за картинку',
    note: 'Генерация изображений. На повестке.',
    badge: 'Скоро · Q3 2026',
  },
  {
    id: 'Realtime API',
    provider: 'OpenAI',
    modality: 'voice agent',
    price: 'tba',
    note: 'WebSocket-streaming для voice-агентов.',
    badge: 'Скоро · Q4 2026',
  },
];

type Differentiator = {
  iconKey: 'doc' | 'router' | 'rouble' | 'key';
  eyebrow: string;
  title: string;
  body: string;
  span: 'wide' | 'narrow';
};

const DIFFERENTIATORS: Differentiator[] = [
  {
    iconKey: 'doc',
    eyebrow: 'Документы',
    title: 'Чек, акт, договор автоматически',
    body: 'Юр-документы по тарифу самозанятого / ИП — приходят в кабинет сразу после оплаты. Никаких писем в бухгалтерию и переписки с менеджером.',
    span: 'wide',
  },
  {
    iconKey: 'router',
    eyebrow: 'Smart Router',
    title: 'auto:cheap сам выбирает',
    body: 'Модель оптимальная по цене / качеству для каждого запроса. Failover на резерв за <2 секунды если основной провайдер упал.',
    span: 'narrow',
  },
  {
    iconKey: 'rouble',
    eyebrow: 'Касса',
    title: 'Рублёвая оплата',
    body: 'ЮKassa, СБП, карты МИР. Без FX-комиссий, без VPN, без иностранных карт. Закрывающие документы — по 152-ФЗ.',
    span: 'narrow',
  },
  {
    iconKey: 'key',
    eyebrow: 'Один ключ',
    title: 'Один Bearer-токен ко всему',
    body: 'Один Bearer-токен ко всем 38 моделям. Меняете провайдера — ничего не меняется в коде. OpenAI-совместимый формат: миграция за минуту.',
    span: 'wide',
  },
];

type Recipe = {
  title: string;
  tags: string[];
  cost: string;
  description: string;
  href: string;
};

const RECIPES: Recipe[] = [
  {
    title: 'Юристы — анализ договора с PII-маскингом',
    tags: ['legal', 'pii', 'claude'],
    cost: '~980 ₽ / 1000 запросов',
    description: 'Прогнал договор через Claude Sonnet 4.6 с автозаменой ФИО / телефонов. Strict JSON Schema на выходе.',
    href: '/cookbook/contract-parsing',
  },
  {
    title: 'CRM — классификация лидов hot / warm / cold',
    tags: ['crm', 'classification', 'deepseek'],
    cost: '~12 ₽ / 1000 обращений',
    description: 'DeepSeek V4-Flash + структурированный output: company_size, budget, fit_score. Готовый промпт.',
    href: '/cookbook/lead-qualification',
  },
  {
    title: 'Support — классификация тикетов и роутинг',
    tags: ['support', 'routing', 'gemini'],
    cost: '~220 ₽ / 1000 тикетов',
    description: 'Из текста обращения → priority, department, suggested_response. Gemini 3 Flash, 2 секунды на тикет.',
    href: '/cookbook/support-ticket-classifier',
  },
];

export default function CreamPreviewPage() {
  const lenisRef = useRef<{ destroy: () => void } | null>(null);
  const heroH1Ref = useRef<HTMLHeadingElement | null>(null);
  const heroBgL1Ref = useRef<HTMLDivElement | null>(null);
  const heroBgL2Ref = useRef<HTMLDivElement | null>(null);

  const [menuOpen, setMenuOpen] = useState(false);
  const [theme, setTheme] = useState<Theme>('light');
  const [themeReady, setThemeReady] = useState(false);

  // === Theme: hydrate from localStorage / system on mount ===
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const stored = window.localStorage.getItem('brikko-theme') as Theme | null;
    let initial: Theme;
    if (stored === 'light' || stored === 'dark') {
      initial = stored;
    } else {
      initial = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }
    setTheme(initial);
    setThemeReady(true);

    // Sync with system theme только если user не сохранял preference.
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const onSystemChange = (e: MediaQueryListEvent) => {
      const userStored = window.localStorage.getItem('brikko-theme');
      if (userStored !== 'light' && userStored !== 'dark') {
        setTheme(e.matches ? 'dark' : 'light');
      }
    };
    mq.addEventListener('change', onSystemChange);
    return () => mq.removeEventListener('change', onSystemChange);
  }, []);

  // === Apply theme to documentElement + persist ===
  useEffect(() => {
    if (!themeReady) return;
    document.documentElement.setAttribute('data-theme', theme);
    window.localStorage.setItem('brikko-theme', theme);
  }, [theme, themeReady]);

  // === Hamburger overlay: ESC + body scroll lock ===
  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMenuOpen(false);
    };
    window.addEventListener('keydown', onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [menuOpen]);

  // === Lenis + GSAP page-level orchestration ===
  useEffect(() => {
    let mounted = true;
    let cleanup: (() => void) | null = null;

    const reduced =
      typeof window !== 'undefined' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const isMobile =
      typeof window !== 'undefined' && window.matchMedia('(max-width: 767px)').matches;

    (async () => {
      const [{ default: Lenis }, gsapMod, scrollTriggerMod] = await Promise.all([
        import('@studio-freight/lenis'),
        import('gsap'),
        import('gsap/ScrollTrigger'),
      ]);
      if (!mounted) return;

      const gsap = gsapMod.default;
      const ScrollTrigger = scrollTriggerMod.ScrollTrigger;
      gsap.registerPlugin(ScrollTrigger);

      if (heroH1Ref.current) {
        const wordSpans = heroH1Ref.current.querySelectorAll('span[data-word]');
        if (reduced) {
          gsap.set(wordSpans, { opacity: 1, y: 0, filter: 'blur(0px)' });
        } else {
          gsap.to(wordSpans, {
            opacity: 1,
            y: 0,
            filter: 'blur(0px)',
            duration: 0.95,
            ease: 'cubic-bezier(0.16, 1, 0.3, 1)',
            stagger: isMobile ? 0.04 : 0.08,
            delay: 0.15,
          });
        }
      }

      if (reduced) {
        return;
      }

      const lenis = new Lenis({
        duration: 1.2,
        easing: (t: number) => Math.min(1, 1.001 - Math.pow(2, -10 * t)),
        smoothWheel: true,
        touchMultiplier: 1,
      });
      lenisRef.current = lenis;
      // Expose for PageTransitionOrchestrator (anchor smooth-scroll).
      (window as unknown as { __brikkoLenis: typeof lenis }).__brikkoLenis = lenis;

      let rafId = 0;
      const raf = (time: number) => {
        lenis.raf(time);
        rafId = requestAnimationFrame(raf);
      };
      rafId = requestAnimationFrame(raf);

      lenis.on('scroll', ScrollTrigger.update);
      ScrollTrigger.scrollerProxy(document.body, {
        scrollTop(value?: number) {
          if (arguments.length && typeof value === 'number') {
            lenis.scrollTo(value, { immediate: true });
          }
          return window.scrollY;
        },
        getBoundingClientRect() {
          return {
            top: 0,
            left: 0,
            width: window.innerWidth,
            height: window.innerHeight,
          };
        },
      });

      if (!isMobile && heroBgL1Ref.current && heroBgL2Ref.current) {
        gsap.to(heroBgL1Ref.current, {
          y: () => window.innerHeight * 0.4,
          ease: 'none',
          scrollTrigger: {
            trigger: '#hero',
            start: 'top top',
            end: 'bottom top',
            scrub: 0.5,
          },
        });
        gsap.to(heroBgL2Ref.current, {
          y: () => window.innerHeight * 0.7,
          ease: 'none',
          scrollTrigger: {
            trigger: '#hero',
            start: 'top top',
            end: 'bottom top',
            scrub: 0.5,
          },
        });
      }

      ScrollTrigger.refresh();

      cleanup = () => {
        cancelAnimationFrame(rafId);
        ScrollTrigger.getAll().forEach((t) => t.kill());
        lenis.destroy();
        lenisRef.current = null;
        try {
          delete (window as unknown as { __brikkoLenis?: unknown }).__brikkoLenis;
        } catch {
          /* defineProperty edge — ignore */
        }
      };
    })();

    return () => {
      mounted = false;
      cleanup?.();
    };
  }, []);

  const fontVars = {
    fontFamily: 'var(--font-cream-sans)',
  } as CSSProperties;

  const toggleTheme = () => setTheme((t) => (t === 'light' ? 'dark' : 'light'));

  return (
    <main
      className={`${styles.cream} ${serif.variable} ${sans.variable} ${mono.variable}`}
      style={fontVars}
      lang="ru"
    >
      <BootSequence />
      <ReadingProgress />
      <CursorDot />
      <PageTransitionOrchestrator />

      <TrafficCanvas theme={theme} />

      <div className={styles.noise} aria-hidden="true" />

      <header className={styles.topBar} aria-label="Brikko preview navigation">
        <a className={styles.brand} href="/preview/cream">
          Brikko
        </a>

        <button
          type="button"
          className={`${styles.hamburger} ${menuOpen ? styles.hamburgerOpen : ''}`}
          data-hamburger
          aria-label={menuOpen ? 'Закрыть меню' : 'Открыть меню'}
          aria-expanded={menuOpen}
          aria-controls="cream-menu-overlay"
          onClick={() => setMenuOpen((v) => !v)}
        >
          <span className={styles.hamburgerLine} aria-hidden="true" />
          <span className={styles.hamburgerLine} aria-hidden="true" />
          <span className={styles.hamburgerLine} aria-hidden="true" />
        </button>

        <div className={styles.topRight}>
          <button
            type="button"
            aria-label={`Переключить на ${theme === 'light' ? 'тёмную' : 'светлую'} тему`}
            onClick={toggleTheme}
            className={styles.themeToggle}
            data-theme-toggle
          >
            {theme === 'light' ? <MoonIcon /> : <SunIcon />}
          </button>
          <a className={styles.topLogin} href="/signup">
            Войти
          </a>
        </div>
      </header>

      <div
        id="cream-menu-overlay"
        className={`${styles.menuOverlay} ${menuOpen ? styles.menuOverlayOpen : ''}`}
        aria-hidden={!menuOpen}
        onClick={(e) => {
          if (e.target === e.currentTarget) setMenuOpen(false);
        }}
      >
        <nav className={styles.menuNav} aria-label="Brikko sections">
          <ul className={styles.menuList}>
            {MENU_LINKS.map((link, i) => (
              <li
                key={link.href}
                className={styles.menuItem}
                style={{ transitionDelay: menuOpen ? `${100 + i * 50}ms` : '0ms' }}
              >
                <a
                  className={styles.menuLink}
                  href={link.href}
                  onClick={() => setMenuOpen(false)}
                  tabIndex={menuOpen ? 0 : -1}
                >
                  <span className={styles.menuIndex}>0{i + 1}</span>
                  <span className={styles.menuLabel}>{link.label}</span>
                </a>
              </li>
            ))}
          </ul>
        </nav>
      </div>

      {/* ============================================================
       * HERO — Editorial Split
       * ============================================================ */}
      <section id="hero" className={styles.hero}>
        <div ref={heroBgL1Ref} className={styles.heroBgLayer1} aria-hidden="true" />
        <div ref={heroBgL2Ref} className={styles.heroBgLayer2} aria-hidden="true" />

        <div className={styles.heroSplit}>
          <div className={styles.heroLeft}>
            <span className={styles.eyebrow}>
              <span className={styles.eyebrowDot} aria-hidden="true" />
              01 — Brikko Gateway
            </span>

            <h1 ref={heroH1Ref} className={styles.h1}>
              {HERO_LINES.map((line, li) => (
                <span key={li} className={styles.h1Line}>
                  {line.words.map((w, wi) => (
                    <span
                      key={wi}
                      data-word
                      className={w.italic ? styles.accentWord : undefined}
                      style={{
                        fontStyle: w.italic ? 'italic' : 'normal',
                      }}
                    >
                      {w.text}
                      {wi < line.words.length - 1 ? ' ' : ''}
                    </span>
                  ))}
                </span>
              ))}
            </h1>

            <p className={styles.subhead}>
              Подключи 38 топовых LLM от шести провайдеров через один
              OpenAI-совместимый ключ. Чек, акт и договор приходят в кабинет
              автоматически. 200 ₽ на старте — хватит на тысячу запросов.
            </p>

            <LiveStatusWidget />

            <div className={styles.ctaRow}>
              <CoralTrailCTA href="/signup">Получить ключ за 5 минут</CoralTrailCTA>
              <a className={styles.ctaSecondary} href="/playground">
                Открыть Playground
              </a>
            </div>
          </div>

          <div className={styles.heroRight}>
            <HeroCodeWindow />
          </div>
        </div>
      </section>

      {/* ============================================================
       * QUICK PROOF — 3 numbers
       * ============================================================ */}
      <section id="proof" className={styles.proof}>
        <div className={styles.proofRow}>
          <ProofCounter
            target={38}
            unit="моделей"
            label="OpenAI, Anthropic, Google, DeepSeek, Yandex, Sber — все в одном API."
          />
          <ProofCounter
            target={5}
            unit="минут"
            label="От регистрации до первого 200 OK с curl или OpenAI SDK."
          />
          <ProofCounter
            target={200}
            unit="₽"
            label="Welcome-бонус новому аккаунту. Хватает на ~1 000 запросов к GPT-5 mini."
          />
        </div>
      </section>

      <SectionDivider />

      {/* ============================================================
       * MODELS SHOWCASE — 6 hand-picked из 38
       * ============================================================ */}
      <section id="models" className={styles.modelsSection} data-reveal-section>
        <header className={styles.sectionHeader}>
          <span className={styles.eyebrow}>
            <span className={styles.eyebrowDot} aria-hidden="true" />
            02 — Каталог
          </span>
          <h2 className={styles.h2}>
            <span data-reveal-text>Тридцать восемь моделей. </span>
            <span data-reveal-text className={styles.h2Italic}>
              Один
            </span>{' '}
            <span data-reveal-text>Bearer.</span>
          </h2>
          <p className={styles.sectionLede}>
            OpenAI, Anthropic, Google, DeepSeek, Яндекс, Сбер — шесть провайдеров,
            один OpenAI-совместимый ключ. Прайс в рублях за миллион токенов,
            открытый каталог по каждой модели.
          </p>
        </header>

        <div className={styles.modelsGrid}>
          {SHOWCASE_MODELS.map((m, i) => (
            <ModelCard key={m.id} model={m} index={i} />
          ))}
        </div>

        <div className={styles.soonDivider} aria-hidden="true">
          <span className={styles.soonDividerLine} />
          <span className={styles.soonDividerCaption}>Скоро в каталоге</span>
          <span className={styles.soonDividerLine} />
        </div>

        <div className={styles.modelsGrid}>
          {SOON_MODELS.map((m, i) => (
            <SoonModelCard key={m.id} model={m} index={i} />
          ))}
        </div>

        <div className={styles.sectionFooter}>
          <a className={styles.sectionLink} href="/models">
            Открыть полный каталог
            <ArrowIcon />
          </a>
        </div>
      </section>

      <SectionDivider />

      {/* ============================================================
       * WHY BRIKKO — 4 differentiators в asymmetric bento
       * ============================================================ */}
      <section id="why" className={styles.whySection} data-reveal-section>
        <header className={styles.sectionHeader}>
          <span className={styles.eyebrow}>
            <span className={styles.eyebrowDot} aria-hidden="true" />
            03 — Почему
          </span>
          <h2 className={styles.h2}>
            <span data-reveal-text>Что вы получаете кроме </span>
            <span data-reveal-text className={styles.h2Italic}>
              API.
            </span>
          </h2>
        </header>

        <div className={styles.bento}>
          {DIFFERENTIATORS.map((d, i) => (
            <DifferentiatorCard key={d.title} d={d} index={i} />
          ))}
        </div>
      </section>

      <SectionDivider />

      <RouterDemo />

      <SectionDivider />

      {/* ============================================================
       * COOKBOOK TEASER — 3 готовых рецепта
       * ============================================================ */}
      <section id="cookbook" className={styles.cookbookSection} data-reveal-section>
        <header className={styles.sectionHeader}>
          <span className={styles.eyebrow}>
            <span className={styles.eyebrowDot} aria-hidden="true" />
            04 — Рецепты
          </span>
          <h2 className={styles.h2}>
            <span data-reveal-text>Готовые блоки. </span>
            <span data-reveal-text className={styles.h2Italic}>
              Считаем
            </span>{' '}
            <span data-reveal-text>экономику.</span>
          </h2>
          <p className={styles.sectionLede}>
            curl + system prompt + JSON Schema — копируйте в свой код. Цена в рублях пересчитана
            на тысячу запросов.
          </p>
        </header>

        <div className={styles.cookbookGrid}>
          {RECIPES.map((r, i) => (
            <RecipeCard key={r.title} recipe={r} index={i} />
          ))}
        </div>

        <div className={styles.sectionFooter}>
          <a className={styles.sectionLink} href="/cookbook">
            Все рецепты
            <ArrowIcon />
          </a>
        </div>
      </section>
    </main>
  );
}

/* ============================================================
 * Section divider — hairline scaleX 0→1 при entry в viewport
 * ============================================================ */
function SectionDivider() {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduced) {
      el.style.transform = 'scaleX(1)';
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            el.style.transform = 'scaleX(1)';
            io.disconnect();
          }
        });
      },
      { threshold: 0.1 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  return <div ref={ref} className={styles.sectionDivider} aria-hidden="true" />;
}

/* ============================================================
 * Reveal helpers — scroll-tied entry для секций и карточек.
 * Чистый Intersection Observer + CSS transitions, без framer-motion.
 * ============================================================ */
function useReveal<T extends HTMLElement>(delay = 0) {
  const ref = useRef<T | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduced) {
      el.dataset.revealed = 'true';
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            const el = entry.target as HTMLElement;
            window.setTimeout(() => {
              el.dataset.revealed = 'true';
            }, delay);
            io.disconnect();
          }
        });
      },
      { threshold: 0.15, rootMargin: '0px 0px -10% 0px' },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [delay]);

  return ref;
}

/* ============================================================
 * ModelCard — double-bezel, provider/id/price
 * ============================================================ */
function ModelCard({ model, index }: { model: ShowcaseModel; index: number }) {
  const ref = useReveal<HTMLDivElement>(index * 100);
  return (
    <div ref={ref} className={styles.cardOuter} data-card-reveal>
      <div className={styles.cardInner}>
        <div className={styles.modelTop}>{model.provider}</div>
        <div className={styles.modelId}>{model.id}</div>
        <div className={styles.modelPrice}>
          {model.inputRub} / {model.outputRub} ₽ за 1M
        </div>
      </div>
    </div>
  );
}

/* ============================================================
 * SoonModelCard — Coming Soon easter-egg. Bg-tier-2, opacity 0.85,
 * cursor:not-allowed, no translate on hover. Badge pill в правом-верхнем углу.
 * ============================================================ */
function SoonModelCard({ model, index }: { model: SoonModel; index: number }) {
  const ref = useReveal<HTMLDivElement>(index * 100);
  return (
    <div
      ref={ref}
      className={`${styles.cardOuter} ${styles.soonCardOuter}`}
      data-card-reveal
      aria-disabled="true"
    >
      <div className={`${styles.cardInner} ${styles.soonCardInner}`}>
        <span className={styles.soonBadge}>{model.badge}</span>
        <div className={styles.modelTop}>
          {model.provider} · {model.modality}
        </div>
        <div className={styles.modelId}>{model.id}</div>
        <div className={styles.modelPrice}>{model.price}</div>
        <div className={styles.soonNote}>{model.note}</div>
      </div>
    </div>
  );
}

/* ============================================================
 * DifferentiatorCard — bento card, 24px Phosphor-style glyph
 * ============================================================ */
function DifferentiatorCard({ d, index }: { d: Differentiator; index: number }) {
  const ref = useReveal<HTMLDivElement>(index * 100);
  return (
    <div
      ref={ref}
      className={`${styles.cardOuter} ${styles.bentoCard} ${
        d.span === 'wide' ? styles.bentoWide : styles.bentoNarrow
      }`}
      data-card-reveal
    >
      <div className={`${styles.cardInner} ${styles.bentoCardInner}`}>
        <div className={styles.bentoGlyph} aria-hidden="true">
          <DifferentiatorIcon iconKey={d.iconKey} />
        </div>
        <div className={styles.bentoEyebrow}>{d.eyebrow}</div>
        <h3 className={styles.bentoTitle}>{d.title}</h3>
        <p className={styles.bentoBody}>{d.body}</p>
      </div>
    </div>
  );
}

function DifferentiatorIcon({ iconKey }: { iconKey: Differentiator['iconKey'] }) {
  const common = {
    width: 24,
    height: 24,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.25,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
  };
  if (iconKey === 'doc') {
    return (
      <svg {...common}>
        <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
        <path d="M14 3v5h5" />
        <path d="M9 13h6" />
        <path d="M9 17h4" />
      </svg>
    );
  }
  if (iconKey === 'router') {
    return (
      <svg {...common}>
        <path d="M4 7h6l3 3" />
        <path d="M4 17h6l3-3" />
        <path d="M16 4l4 3-4 3" />
        <path d="M16 14l4 3-4 3" />
      </svg>
    );
  }
  if (iconKey === 'rouble') {
    return (
      <svg {...common}>
        <circle cx="12" cy="12" r="9" />
        <path d="M9 7h4.2a2.8 2.8 0 0 1 0 5.6H9" />
        <path d="M9 12.6V18" />
        <path d="M7.5 14.5h4.5" />
      </svg>
    );
  }
  // key
  return (
    <svg {...common}>
      <circle cx="8" cy="14" r="3.5" />
      <path d="M11 13l8.5-8.5" />
      <path d="M16 8l3 3" />
      <path d="M14 10l2 2" />
    </svg>
  );
}

/* ============================================================
 * RecipeCard — compact double-bezel + tag pills
 * ============================================================ */
function RecipeCard({ recipe, index }: { recipe: Recipe; index: number }) {
  const ref = useReveal<HTMLAnchorElement>(index * 100);
  return (
    <a ref={ref} href={recipe.href} className={`${styles.cardOuter} ${styles.recipeCard}`} data-card-reveal>
      <div className={`${styles.cardInner} ${styles.recipeInner}`}>
        <div className={styles.recipeTags}>
          {recipe.tags.map((t) => (
            <span key={t} className={styles.recipeTag}>
              {t}
            </span>
          ))}
        </div>
        <h3 className={styles.recipeTitle}>{recipe.title}</h3>
        <p className={styles.recipeDescription}>{recipe.description}</p>
        <div className={styles.recipeBottom}>
          <span className={styles.recipeCost}>{recipe.cost}</span>
          <span className={styles.recipeArrow}>
            Открыть рецепт <ArrowIcon />
          </span>
        </div>
      </div>
    </a>
  );
}

/* ============================================================
 * Helpers
 * ============================================================ */

function ArrowIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M5 12h14M13 6l6 6-6 6" />
    </svg>
  );
}

function SunIcon() {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.25"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="4" />
      <path d="M12 3v1.5" />
      <path d="M12 19.5V21" />
      <path d="M3 12h1.5" />
      <path d="M19.5 12H21" />
      <path d="M5.636 5.636l1.06 1.06" />
      <path d="M17.303 17.303l1.06 1.06" />
      <path d="M5.636 18.364l1.06-1.06" />
      <path d="M17.303 6.697l1.06-1.06" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.25"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z" />
    </svg>
  );
}

/* ============================================================
 * TrafficCanvas — live particle system, имитация трафика через Brikko router.
 * Single 2D canvas, ~40-60 particles max, DPR-aware, debounced resize.
 * 4 фазы: REQUEST → HUB PULSE → DISPATCH → RESPONSE.
 * Цвета — из CSS-vars через JS-bridge, обновляются на theme change.
 * ============================================================ */

type Vec = { x: number; y: number };

type Phase = 'request' | 'dispatch' | 'response' | 'arrived';

type Particle = {
  birth: number;
  spawn: Vec;
  control1: Vec;
  control2: Vec;
  control3: Vec;
  control4: Vec;
  modelIdx: number;
  glyph: string;
  phase: Phase;
};

type ThemeColors = {
  accent1: string;
  fgMuted: string;
  fgPrimary: string;
  bgBase: string;
};

const MODEL_LABELS = [
  'GPT-5.4 mini',
  'Claude 4.6',
  'Gemini 3 Flash',
  'DeepSeek V4-Pro',
  'YandexGPT 5.1',
  'GigaChat 2 Pro',
];

const HUB_LABEL = 'brikko · router';

const PHASE_REQUEST_END = 0.25;
const PHASE_HUB_PULSE_END = 0.30;
const PHASE_DISPATCH_END = 0.55;

const TOTAL_LIFETIME_MS = 4400;

const HEX_GLYPHS = ['4f', 'a2', 'b9', '7c', '0e', '3d', 'c1', '8b'];

function bezierAt(t: number, p0: Vec, p1: Vec, p2: Vec): Vec {
  const u = 1 - t;
  return {
    x: u * u * p0.x + 2 * u * t * p1.x + t * t * p2.x,
    y: u * u * p0.y + 2 * u * t * p1.y + t * t * p2.y,
  };
}

function easeOutQuad(t: number): number {
  return 1 - (1 - t) * (1 - t);
}

function debounce<F extends (...args: never[]) => void>(fn: F, ms: number): F & { cancel: () => void } {
  let timer: ReturnType<typeof setTimeout> | null = null;
  const wrapped = ((...args: Parameters<F>) => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  }) as F & { cancel: () => void };
  wrapped.cancel = () => {
    if (timer) clearTimeout(timer);
  };
  return wrapped;
}

function readThemeColors(): ThemeColors {
  const cs = getComputedStyle(document.documentElement);
  // CSS-vars живут на .cream-классе через CSS module, но dark inverts через
  // [data-theme=dark] на html — поэтому читаем computedStyle с body, где
  // переменные уже разрешены через каскад.
  const fromBody = getComputedStyle(document.body);
  const get = (name: string) =>
    (fromBody.getPropertyValue(name) || cs.getPropertyValue(name) || '').trim();
  return {
    accent1: get('--accent-1') || '#1c1917',
    fgMuted: get('--fg-muted') || '#57534e',
    fgPrimary: get('--fg-primary') || '#1c1917',
    bgBase: get('--bg-base') || '#ffffff',
  };
}

function TrafficCanvas({ theme }: { theme: Theme }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  // Live ref так что цвета обновляются на theme change без re-init canvas'а.
  const colorsRef = useRef<ThemeColors>({
    accent1: '#1c1917',
    fgMuted: '#57534e',
    fgPrimary: '#1c1917',
    bgBase: '#ffffff',
  });

  // Обновляем colors при theme change. Next frame подхватит новые значения.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    // Пара RAF чтобы CSS-vars точно успели примениться через каскад.
    const id = requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        colorsRef.current = readThemeColors();
      });
    });
    return () => cancelAnimationFrame(id);
  }, [theme]);

  // Canvas opacity fade на скролл от hero. Particles мешают читаемости текста
  // в нижних секциях — таем с 1.0 до 0.15 за первые 80% первого экрана,
  // чтобы фон не казался мёртвым, но и не конкурировал с контентом.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const canvas = canvasRef.current;
    if (!canvas) return;

    const update = () => {
      const fadeEnd = window.innerHeight * 0.8;
      const t = Math.min(1, Math.max(0, window.scrollY / fadeEnd));
      const opacity = 1 - t * 0.85;
      canvas.style.opacity = String(opacity);
    };

    update();
    window.addEventListener('scroll', update, { passive: true });
    window.addEventListener('resize', update, { passive: true });
    return () => {
      window.removeEventListener('scroll', update);
      window.removeEventListener('resize', update);
    };
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    // Init colors (theme может ещё не примениться — fallback в readThemeColors).
    colorsRef.current = readThemeColors();

    const dims = { w: 0, h: 0, dpr: 1, isMobile: false };
    const positions: {
      spawnZone: { x0: number; x1: number; y0: number; y1: number };
      hub: Vec;
      models: Vec[];
      activeModelCount: number;
    } = {
      spawnZone: { x0: 0, x1: 0, y0: 0, y1: 0 },
      hub: { x: 0, y: 0 },
      models: [],
      activeModelCount: 6,
    };
    const particles: Particle[] = [];
    let hubPulseStart = -Infinity;
    const modelPulseStart: number[] = new Array(6).fill(-Infinity);

    const setupCanvas = () => {
      const dpr = Math.max(1, Math.min(window.devicePixelRatio || 1, 2));
      const w = window.innerWidth;
      const h = window.innerHeight;
      const isMobile = w < 768;

      canvas.width = Math.floor(w * dpr);
      canvas.height = Math.floor(h * dpr);
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      dims.w = w;
      dims.h = h;
      dims.dpr = dpr;
      dims.isMobile = isMobile;

      if (isMobile) {
        positions.spawnZone = {
          x0: w * 0.05,
          x1: w * 0.30,
          y0: h * 0.15,
          y1: h * 0.85,
        };
      } else {
        positions.spawnZone = {
          x0: w * 0.05,
          x1: w * 0.20,
          y0: h * 0.15,
          y1: h * 0.85,
        };
      }

      positions.hub = {
        x: w * 0.50,
        y: isMobile ? h * 0.40 : h * 0.50,
      };

      positions.activeModelCount = isMobile ? 4 : 6;
      const modelYs = [0.15, 0.30, 0.45, 0.60, 0.75, 0.90];
      positions.models = modelYs.map((yFrac) => ({
        x: w * 0.85,
        y: h * yFrac,
      }));
    };

    setupCanvas();

    const spawnParticle = (now: number) => {
      if (particles.length >= 60) return;

      const sz = positions.spawnZone;
      const spawn: Vec = {
        x: sz.x0 + Math.random() * (sz.x1 - sz.x0),
        y: sz.y0 + Math.random() * (sz.y1 - sz.y0),
      };
      const hub = positions.hub;
      const modelIdx = Math.floor(Math.random() * positions.activeModelCount);
      const model = positions.models[modelIdx]!;

      const perp = (a: Vec, b: Vec, offsetMin: number, offsetMax: number): Vec => {
        const mx = (a.x + b.x) / 2;
        const my = (a.y + b.y) / 2;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const len = Math.max(1, Math.hypot(dx, dy));
        const nx = -dy / len;
        const ny = dx / len;
        const offset = offsetMin + Math.random() * (offsetMax - offsetMin);
        const sign = Math.random() > 0.5 ? 1 : -1;
        return { x: mx + nx * offset * sign, y: my + ny * offset * sign };
      };

      const c1 = perp(spawn, hub, 30, 80);
      const c2 = perp(hub, model, 30, 80);
      const c3 = perp(model, hub, 30, 80);
      const c4 = perp(hub, spawn, 30, 80);

      particles.push({
        birth: now,
        spawn,
        control1: c1,
        control2: c2,
        control3: c3,
        control4: c4,
        modelIdx,
        glyph: HEX_GLYPHS[Math.floor(Math.random() * HEX_GLYPHS.length)]!,
        phase: 'request',
      });
    };

    let spawnTimer: ReturnType<typeof setTimeout> | null = null;
    const scheduleSpawn = () => {
      const baseDelay = dims.isMobile ? 250 : 125;
      const jitter = Math.random() * 150;
      spawnTimer = setTimeout(() => {
        spawnParticle(performance.now());
        scheduleSpawn();
      }, baseDelay + jitter);
    };
    scheduleSpawn();

    const drawHub = (now: number) => {
      const colors = colorsRef.current;
      const { hub } = positions;
      let scale = 1;
      const pulseAge = now - hubPulseStart;
      if (pulseAge >= 0 && pulseAge <= 200) {
        const t = pulseAge / 200;
        const tri = t < 0.5 ? t * 2 : (1 - t) * 2;
        scale = 1 + tri * 0.15;
      }
      const radius = 32 * scale;

      ctx.save();
      ctx.beginPath();
      ctx.arc(hub.x, hub.y, radius, 0, Math.PI * 2);
      ctx.fillStyle = colors.bgBase;
      ctx.fill();
      ctx.lineWidth = 1.2;
      ctx.strokeStyle = colors.accent1;
      ctx.stroke();

      ctx.font = '8px ui-monospace, "Geist Mono", "JetBrains Mono", monospace';
      ctx.fillStyle = colors.fgMuted;
      ctx.globalAlpha = pulseAge >= 0 && pulseAge <= 100 ? 0.85 : 0.55;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(HUB_LABEL, hub.x, hub.y);
      ctx.restore();
    };

    const drawModelNodes = (now: number) => {
      const colors = colorsRef.current;
      ctx.save();
      ctx.font = '11px ui-monospace, "Geist Mono", "JetBrains Mono", monospace';
      ctx.textBaseline = 'middle';
      for (let i = 0; i < positions.activeModelCount; i++) {
        const node = positions.models[i]!;
        const pulseAge = now - modelPulseStart[i]!;
        let scale = 1;
        let glow = 0;
        if (pulseAge >= 0 && pulseAge <= 200) {
          const t = pulseAge / 200;
          const tri = t < 0.5 ? t * 2 : (1 - t) * 2;
          scale = 1 + tri * 0.25;
          glow = tri * 8;
        }

        if (glow > 0) {
          ctx.shadowColor = colors.fgMuted;
          ctx.shadowBlur = glow;
        } else {
          ctx.shadowBlur = 0;
        }
        ctx.beginPath();
        ctx.arc(node.x, node.y, 4 * scale, 0, Math.PI * 2);
        ctx.fillStyle = colors.fgMuted;
        ctx.globalAlpha = 0.55;
        ctx.fill();
        ctx.shadowBlur = 0;

        ctx.globalAlpha = 0.45;
        ctx.fillStyle = colors.fgMuted;
        ctx.textAlign = 'left';
        ctx.fillText(MODEL_LABELS[i]!, node.x + 12, node.y);
      }
      ctx.restore();
    };

    const drawTrail = (
      points: Vec[],
      color: string,
      headAlpha: number,
      headSize: number,
    ) => {
      ctx.save();
      for (let i = 0; i < points.length; i++) {
        const p = points[i]!;
        const fade = Math.pow(0.85, i);
        ctx.globalAlpha = headAlpha * fade;
        ctx.beginPath();
        ctx.arc(p.x, p.y, headSize * (1 - i * 0.08), 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.fill();
      }
      ctx.restore();
    };

    const updateAndDrawParticle = (p: Particle, now: number) => {
      const colors = colorsRef.current;
      const age = now - p.birth;
      const life = age / TOTAL_LIFETIME_MS;
      if (life >= 1) return false;

      const hub = positions.hub;
      const model = positions.models[p.modelIdx]!;

      // Phase 1 — REQUEST (0 → 0.25): spawn → hub. Color: accent-1.
      if (life < PHASE_REQUEST_END) {
        const t = easeOutQuad(life / PHASE_REQUEST_END);
        const head = bezierAt(t, p.spawn, p.control1, hub);
        const trail: Vec[] = [head];
        for (let i = 1; i <= 6; i++) {
          const tt = Math.max(0, t - i * 0.025);
          trail.push(bezierAt(tt, p.spawn, p.control1, hub));
        }
        if (!dims.isMobile && Math.floor(now / 50) % 2 === 0) {
          for (let i = 1; i < trail.length; i++) {
            const tp = trail[i]!;
            tp.x += (Math.random() - 0.5) * 2;
            tp.y += (Math.random() - 0.5) * 2;
          }
        }
        drawTrail(trail, colors.accent1, 0.55, 2);

        if (!dims.isMobile && t > 0.05 && t < 0.5) {
          ctx.save();
          ctx.font = '9px ui-monospace, "Geist Mono", monospace';
          ctx.fillStyle = colors.accent1;
          ctx.globalAlpha = 0.4;
          ctx.fillText(p.glyph, head.x + 8, head.y - 6);
          ctx.restore();
        }
        return true;
      }

      // Phase 2 — HUB PULSE (0.25 → 0.30).
      if (life < PHASE_HUB_PULSE_END) {
        if (p.phase === 'request') {
          hubPulseStart = now;
          p.phase = 'dispatch';
        }
        return true;
      }

      // Phase 3 — DISPATCH (0.30 → 0.55): hub → model. Color: fg-muted.
      if (life < PHASE_DISPATCH_END) {
        const phaseLife = (life - PHASE_HUB_PULSE_END) / (PHASE_DISPATCH_END - PHASE_HUB_PULSE_END);
        const t = phaseLife;
        const head = bezierAt(t, hub, p.control2, model);
        const trail: Vec[] = [head];
        for (let i = 1; i <= 5; i++) {
          const tt = Math.max(0, t - i * 0.04);
          trail.push(bezierAt(tt, hub, p.control2, model));
        }
        drawTrail(trail, colors.fgMuted, 0.5, 2);
        return true;
      }

      // Phase 4 — RESPONSE (0.55 → 1.0): model → hub → spawn. Color: fg-primary.
      if (p.phase === 'dispatch') {
        modelPulseStart[p.modelIdx] = now;
        p.phase = 'response';
      }

      const phaseLife = (life - PHASE_DISPATCH_END) / (1 - PHASE_DISPATCH_END);
      let head: Vec;
      let controlA: Vec;
      let controlB: Vec;
      let segP0: Vec;
      let segP2: Vec;
      let segT: number;
      if (phaseLife < 0.5) {
        segT = phaseLife * 2;
        segP0 = model;
        controlA = p.control3;
        segP2 = hub;
        controlB = p.control3;
      } else {
        segT = (phaseLife - 0.5) * 2;
        segP0 = hub;
        controlA = p.control4;
        segP2 = p.spawn;
        controlB = p.control4;
      }
      head = bezierAt(segT, segP0, controlA, segP2);
      void controlB;
      const trail: Vec[] = [head];
      for (let i = 1; i <= 4; i++) {
        const tt = Math.max(0, segT - i * 0.05);
        trail.push(bezierAt(tt, segP0, controlA, segP2));
      }
      drawTrail(trail, colors.fgPrimary, 0.4, 1.8);

      const remaining = TOTAL_LIFETIME_MS * (1 - life);
      if (remaining < 200 && phaseLife > 0.95) {
        const fade = remaining / 200;
        ctx.save();
        ctx.font = '12px ui-monospace, "Geist Mono", monospace';
        ctx.fillStyle = colors.accent1;
        ctx.globalAlpha = 0.6 * fade;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText('✓', p.spawn.x, p.spawn.y);
        ctx.restore();
      }

      return true;
    };

    let rafId = 0;
    const draw = (now: number) => {
      ctx.clearRect(0, 0, dims.w, dims.h);
      drawHub(now);
      drawModelNodes(now);
      for (let i = particles.length - 1; i >= 0; i--) {
        const alive = updateAndDrawParticle(particles[i]!, now);
        if (!alive) particles.splice(i, 1);
      }
      rafId = requestAnimationFrame(draw);
    };
    rafId = requestAnimationFrame(draw);

    const debouncedSetup = debounce(setupCanvas, 200);
    const onResize = () => debouncedSetup();
    window.addEventListener('resize', onResize);

    return () => {
      cancelAnimationFrame(rafId);
      if (spawnTimer) clearTimeout(spawnTimer);
      debouncedSetup.cancel();
      window.removeEventListener('resize', onResize);
    };
  }, []);

  return <canvas ref={canvasRef} className={styles.trafficCanvas} aria-hidden="true" />;
}

/* ============================================================
 * HeroCodeWindow — typewriter loop в double-bezel shell.
 * Печатает curl-команду, затем JSON-ответ, пауза, clear, повтор.
 * Цвета — через CSS-vars (--code-bg / --code-fg инвертируются по теме).
 * ============================================================ */

const TYPE_REQUEST_LINES: string[] = [
  'curl https://api.brikko.ru/v1/messages \\',
  '  -H "Authorization: Bearer brikko_..." \\',
  '  -H "Content-Type: application/json" \\',
  '  -d \'{',
  '    "model": "auto:cheap",',
  '    "messages": [',
  '      {"role": "user", "content": "Привет!"}',
  '    ]',
  '  }\'',
];
const TYPE_RESPONSE_LINES: string[] = [
  '{',
  '  "role": "assistant",',
  '  "content": "Привет, мир!"',
  '}',
];

function HeroCodeWindow() {
  const reqRef = useRef<HTMLPreElement | null>(null);
  const resRef = useRef<HTMLPreElement | null>(null);

  useEffect(() => {
    const reqEl = reqRef.current;
    const resEl = resRef.current;
    if (!reqEl || !resEl) return;

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const charDelay = reduced ? 80 : 25;
    const reqText = TYPE_REQUEST_LINES.join('\n');
    const resText = TYPE_RESPONSE_LINES.join('\n');

    let cancelled = false;
    let timeoutId: ReturnType<typeof setTimeout> | null = null;

    const wait = (ms: number) =>
      new Promise<void>((resolve) => {
        timeoutId = setTimeout(resolve, ms);
      });

    const typeInto = async (el: HTMLPreElement, text: string) => {
      el.textContent = '';
      for (let i = 0; i < text.length; i++) {
        if (cancelled) return;
        el.textContent = text.slice(0, i + 1);
        await wait(charDelay);
      }
    };

    const loop = async () => {
      while (!cancelled) {
        reqEl.textContent = '';
        resEl.textContent = '';
        await typeInto(reqEl, reqText);
        if (cancelled) return;
        await wait(700);
        await typeInto(resEl, resText);
        if (cancelled) return;
        await wait(1500);
      }
    };

    loop();

    return () => {
      cancelled = true;
      if (timeoutId) clearTimeout(timeoutId);
    };
  }, []);

  return (
    <div className={styles.codeShellOuter} aria-hidden="true">
      <div className={styles.codeShellInner}>
        <div className={styles.codeBar}>
          <span className={styles.codeBarDot} />
          <span className={styles.codeBarDot} />
          <span className={styles.codeBarDot} />
          <span className={styles.codeBarLabel}>brikko.sh</span>
        </div>
        <div className={styles.codeStream}>
          <pre ref={reqRef} className={`${styles.codeLine} ${styles.codeReq}`} />
          <pre ref={resRef} className={`${styles.codeLine} ${styles.codeRes}`} />
          <span className={styles.codeCaret} />
        </div>
      </div>
    </div>
  );
}

/* ============================================================
 * CTA Trail — rope-эффект через Catmull-Rom spline.
 * Цвет stroke — accent-1 через CSS-var (через runtime style).
 * ============================================================ */

const TRAIL_RADIUS = 120;
const TRAIL_OVERLAY_PAD = 160;
const FADE_MS = 250;
const PARTICLE_PERIOD = 600;
const TRAIL_SAMPLES = 8;
const RELAX_DECAY = 0.92;

type Pt = { x: number; y: number };

function catmullRomToBezier(pts: Pt[]): string {
  if (pts.length < 2) return '';
  const first = pts[0]!;
  if (pts.length === 2) {
    const last = pts[1]!;
    return `M ${first.x.toFixed(2)} ${first.y.toFixed(2)} L ${last.x.toFixed(2)} ${last.y.toFixed(2)}`;
  }
  const d: string[] = [`M ${first.x.toFixed(2)} ${first.y.toFixed(2)}`];
  for (let i = 0; i < pts.length - 1; i++) {
    const p1 = pts[i]!;
    const p2 = pts[i + 1]!;
    const p0 = pts[i - 1] ?? p1;
    const p3 = pts[i + 2] ?? p2;
    const c1x = p1.x + (p2.x - p0.x) / 6;
    const c1y = p1.y + (p2.y - p0.y) / 6;
    const c2x = p2.x - (p3.x - p1.x) / 6;
    const c2y = p2.y - (p3.y - p1.y) / 6;
    d.push(
      `C ${c1x.toFixed(2)} ${c1y.toFixed(2)} ${c2x.toFixed(2)} ${c2y.toFixed(2)} ${p2.x.toFixed(2)} ${p2.y.toFixed(2)}`,
    );
  }
  return d.join(' ');
}

function CoralTrailCTA({
  href,
  children,
}: {
  href: string;
  children: React.ReactNode;
}) {
  const anchorRef = useRef<HTMLAnchorElement | null>(null);
  const arrowRef = useRef<HTMLSpanElement | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const pathRef = useRef<SVGPathElement | null>(null);
  const dotRef = useRef<SVGCircleElement | null>(null);

  useEffect(() => {
    const anchor = anchorRef.current;
    const arrow = arrowRef.current;
    const svg = svgRef.current;
    const path = pathRef.current;
    const dot = dotRef.current;
    if (!anchor || !arrow || !svg || !path || !dot) return;

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const fineHover = window.matchMedia('(hover: hover) and (pointer: fine)').matches;
    if (reduced || !fineHover) return;

    const buffer: Pt[] = [];
    let opacity = 0;
    let opacityTarget = 0;
    let lastFrame = performance.now();
    let lastEvent: { clientX: number; clientY: number } | null = null;
    let raf = 0;
    let lastMoveAt = 0;

    const tick = (now: number) => {
      const dt = now - lastFrame;
      lastFrame = now;

      const svgRect = svg.getBoundingClientRect();
      const arrowRect = arrow.getBoundingClientRect();
      const ax = arrowRect.left + arrowRect.width / 2 - svgRect.left;
      const ay = arrowRect.top + arrowRect.height / 2 - svgRect.top;

      if (lastEvent) {
        const dx = lastEvent.clientX - (arrowRect.left + arrowRect.width / 2);
        const dy = lastEvent.clientY - (arrowRect.top + arrowRect.height / 2);
        const dist = Math.hypot(dx, dy);
        opacityTarget = dist <= TRAIL_RADIUS ? 0.55 : 0;
      } else {
        opacityTarget = 0;
      }

      const stillMoving = lastEvent && now - lastMoveAt < 60;

      if (lastEvent && stillMoving) {
        const lx = lastEvent.clientX - svgRect.left;
        const ly = lastEvent.clientY - svgRect.top;
        const head = buffer[0];
        if (!head || Math.hypot(lx - head.x, ly - head.y) > 2) {
          buffer.unshift({ x: lx, y: ly });
          while (buffer.length > TRAIL_SAMPLES) buffer.pop();
        }
      } else if (buffer.length > 0) {
        const head = buffer[0]!;
        for (let i = 1; i < buffer.length; i++) {
          const cur = buffer[i]!;
          buffer[i] = {
            x: cur.x * RELAX_DECAY + head.x * (1 - RELAX_DECAY),
            y: cur.y * RELAX_DECAY + head.y * (1 - RELAX_DECAY),
          };
        }
      }

      const step = dt / FADE_MS;
      if (opacity < opacityTarget) {
        opacity = Math.min(opacityTarget, opacity + step * 0.55);
      } else if (opacity > opacityTarget) {
        opacity = Math.max(opacityTarget, opacity - step * 0.55);
      }

      const pts: Pt[] = [{ x: ax, y: ay }];
      for (let i = buffer.length - 1; i >= 0; i--) pts.push(buffer[i]!);

      if (pts.length >= 2 && opacity > 0.01) {
        path.setAttribute('d', catmullRomToBezier(pts));
        path.setAttribute('opacity', opacity.toFixed(3));

        const total = path.getTotalLength();
        if (total > 0) {
          const t = (now % PARTICLE_PERIOD) / PARTICLE_PERIOD;
          const pt = path.getPointAtLength(total * t);
          dot.setAttribute('cx', pt.x.toFixed(2));
          dot.setAttribute('cy', pt.y.toFixed(2));
          dot.setAttribute('opacity', (opacity * 1.4).toFixed(3));
        } else {
          dot.setAttribute('opacity', '0');
        }
      } else {
        path.setAttribute('opacity', '0');
        dot.setAttribute('opacity', '0');
      }

      raf = requestAnimationFrame(tick);
    };

    const onMove = (e: PointerEvent) => {
      lastEvent = { clientX: e.clientX, clientY: e.clientY };
      lastMoveAt = performance.now();
    };

    window.addEventListener('pointermove', onMove);
    raf = requestAnimationFrame(tick);

    return () => {
      window.removeEventListener('pointermove', onMove);
      cancelAnimationFrame(raf);
      path.setAttribute('opacity', '0');
      dot.setAttribute('opacity', '0');
    };
  }, []);

  return (
    <a ref={anchorRef} href={href} className={styles.ctaPrimary}>
      <span>{children}</span>
      <span className={styles.ctaIconWrap} aria-hidden="true">
        <span ref={arrowRef} className={styles.ctaArrow}>
          <ArrowIcon />
        </span>
      </span>
      <svg
        ref={svgRef}
        className={styles.trailOverlay}
        aria-hidden="true"
        style={{
          left: `-${TRAIL_OVERLAY_PAD}px`,
          right: `-${TRAIL_OVERLAY_PAD}px`,
          top: `-${TRAIL_OVERLAY_PAD}px`,
          bottom: `-${TRAIL_OVERLAY_PAD}px`,
        }}
      >
        <path
          ref={pathRef}
          className={styles.trailPath}
          d="M 0 0"
          fill="none"
          stroke="var(--accent-1)"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          opacity="0"
        />
        <circle
          ref={dotRef}
          className={styles.trailDot}
          r="4"
          fill="var(--accent-1)"
          opacity="0"
        />
      </svg>
    </a>
  );
}

/* ============================================================
 * Counter that resolves on viewport-enter
 * ============================================================ */

function ProofCounter({
  target,
  unit,
  label,
}: {
  target: number;
  unit: string;
  label: string;
}) {
  const [value, setValue] = useState(0);
  const ref = useRef<HTMLDivElement | null>(null);
  const playedRef = useRef(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduced) {
      setValue(target);
      return;
    }

    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting && !playedRef.current) {
            playedRef.current = true;
            const start = performance.now();
            const dur = 900;
            const tick = (now: number) => {
              const t = Math.min(1, (now - start) / dur);
              const eased = 1 - Math.pow(1 - t, 3);
              setValue(Math.round(target * eased));
              if (t < 1) requestAnimationFrame(tick);
              else setValue(target);
            };
            requestAnimationFrame(tick);
          }
        });
      },
      { threshold: 0.5 },
    );

    io.observe(el);
    return () => io.disconnect();
  }, [target]);

  return (
    <div className={styles.proofItem} ref={ref}>
      <div className={styles.proofDivider} />
      <div className={styles.proofNumber}>
        {value}
        <span className={styles.proofUnit}>{unit}</span>
      </div>
      <div className={styles.proofLabel}>{label}</div>
    </div>
  );
}
