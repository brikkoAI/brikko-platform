import { AlertTriangle, CheckCircle2, Info, OctagonX, type LucideIcon } from 'lucide-react';
import { cva, type VariantProps } from 'class-variance-authority';
import type { HTMLAttributes, ReactNode } from 'react';
import { cn } from '@/lib/utils';

// All variants route through banner-{variant}-* CSS-переменные так, чтобы
// :root[data-theme='dark'] инвертировал и фон, и текст. До 2026-05-08 только
// `warning` пользовался этим паттерном — `info`/`success`/`error` сидели на
// сырых `bg-*-50 text-*-600`, которые в dark-shell сливались с фоном
// плашки (например, Privacy-upsell на /app/settings).
const bannerVariants = cva(
  'flex items-start gap-3 rounded-lg border p-4 text-body',
  {
    variants: {
      variant: {
        info: 'bg-banner-info-bg border-banner-info-border !text-banner-info-fg',
        success:
          'bg-banner-success-bg border-banner-success-border !text-banner-success-fg',
        warning:
          'bg-banner-warning-bg border-banner-warning-border !text-banner-warning-fg',
        error:
          'bg-banner-error-bg border-banner-error-border !text-banner-error-fg',
      },
    },
    defaultVariants: { variant: 'info' },
  },
);

const ICONS: Record<NonNullable<VariantProps<typeof bannerVariants>['variant']>, LucideIcon> = {
  info: Info,
  success: CheckCircle2,
  warning: AlertTriangle,
  error: OctagonX,
};

export interface BannerProps
  extends HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof bannerVariants> {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
}

export function Banner({ className, variant = 'info', title, description, action, ...props }: BannerProps) {
  const Icon = ICONS[variant ?? 'info'];
  return (
    <div role="status" className={cn(bannerVariants({ variant }), className)} {...props}>
      <Icon className="mt-0.5 h-5 w-5 shrink-0" strokeWidth={1.5} aria-hidden="true" />
      <div className="flex flex-1 flex-col gap-1">
        <p className="font-medium">{title}</p>
        {description ? (
          <div
            className={cn(
              'text-body-sm',
              variant === 'warning' && '!text-banner-warning-fg-muted',
              variant === 'info' && '!text-banner-info-fg-muted',
              variant === 'success' && '!text-banner-success-fg-muted',
              variant === 'error' && '!text-banner-error-fg-muted',
            )}
          >
            {description}
          </div>
        ) : null}
      </div>
      {action ? <div className="ml-4 shrink-0">{action}</div> : null}
    </div>
  );
}
