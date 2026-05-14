/**
 * Favicon — генерируется Next.js при build, кэшируется браузером.
 * Размер 32×32 — стандарт для табов (Chrome/FF/Safari ресайзят сами при необходимости).
 *
 * Дизайн (Cream Studio v6): буква «B» cream (#F5F5F4) на espresso-квадрате
 * (#1C1917) и radius 7px. Минимальный, читается на любом фоне табов
 * (тёмная/светлая тема).
 *
 * Когда захочется кастомный SVG/PNG от дизайнера — заменить этот файл на
 * `icon.svg` или `icon.png`. Next.js по соглашению подхватит автоматически
 * (см. https://nextjs.org/docs/app/api-reference/file-conventions/metadata/app-icons).
 */
import { ImageResponse } from 'next/og';

export const runtime = 'edge';
export const size = { width: 32, height: 32 };
export const contentType = 'image/png';

export default function Icon() {
  return new ImageResponse(
    (
      <div
        style={{
          fontSize: 22,
          background: '#1C1917',
          width: '100%',
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: '#F5F5F4',
          fontWeight: 700,
          fontFamily: 'system-ui, sans-serif',
          borderRadius: 7,
          letterSpacing: '-0.02em',
        }}
      >
        B
      </div>
    ),
    { ...size },
  );
}
