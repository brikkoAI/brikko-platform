/**
 * Apple touch icon — для iOS/iPadOS «Добавить на экран Домой».
 * 180×180 — официальный размер от Apple HIG.
 *
 * Дизайн совпадает с favicon (icon.tsx), но крупнее и с большими полями —
 * чтобы при автоматическом скруглении iOS буква «B» не обрезалась.
 */
import { ImageResponse } from 'next/og';

export const runtime = 'edge';
export const size = { width: 180, height: 180 };
export const contentType = 'image/png';

export default function AppleIcon() {
  return new ImageResponse(
    (
      <div
        style={{
          fontSize: 110,
          background: '#1C1917',
          width: '100%',
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: '#F5F5F4',
          fontWeight: 700,
          fontFamily: 'system-ui, sans-serif',
          letterSpacing: '-0.04em',
        }}
      >
        B
      </div>
    ),
    { ...size },
  );
}
