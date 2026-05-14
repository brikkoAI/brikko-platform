'use client';

/**
 * TrafficCanvas — live particle system, имитация трафика через Brikko router.
 *
 * Sprint 13 (2026-05-02): вынесен из preview/cream/page.tsx чтобы переиспользовать
 * на marketing-страницах после rebrand'а в Cream Studio v6 эстетику. Логика
 * один-в-один с reference-имплементацией; цвета считываются из CSS-vars
 * (--accent-1, --fg-muted, --fg-primary, --bg-base) — переключаются вместе
 * с темой через [data-theme="dark"] на <html>.
 *
 * Single 2D canvas, ~40-60 particles max, DPR-aware, debounced resize.
 * 4 фазы: REQUEST → HUB PULSE → DISPATCH → RESPONSE.
 *
 * `prefers-reduced-motion: reduce` → canvas скрыт через CSS.
 *
 * Прозрачный — bg-base проступает через предка (body/main).
 */

import { useEffect, useRef } from 'react';

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

interface Props {
  /** Theme key — на изменение пере-перечитывает CSS-vars. */
  themeKey: string;
}

export function TrafficCanvas({ themeKey }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const colorsRef = useRef<ThemeColors>({
    accent1: '#1c1917',
    fgMuted: '#57534e',
    fgPrimary: '#1c1917',
    bgBase: '#ffffff',
  });

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const id = requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        colorsRef.current = readThemeColors();
      });
    });
    return () => cancelAnimationFrame(id);
  }, [themeKey]);

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

      if (life < PHASE_HUB_PULSE_END) {
        if (p.phase === 'request') {
          hubPulseStart = now;
          p.phase = 'dispatch';
        }
        return true;
      }

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

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      style={{
        position: 'fixed',
        inset: 0,
        width: '100vw',
        height: '100vh',
        zIndex: 0,
        pointerEvents: 'none',
        transform: 'translateZ(0)',
        willChange: 'transform, opacity',
        transition: 'opacity 200ms cubic-bezier(0.32, 0.72, 0, 1)',
      }}
    />
  );
}
