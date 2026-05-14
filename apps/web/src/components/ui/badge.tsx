import { cva, type VariantProps } from 'class-variance-authority';
import { type HTMLAttributes } from 'react';
import { cn } from '@/lib/utils';

const badgeVariants = cva(
  'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium',
  {
    variants: {
      variant: {
        neutral: 'bg-gray-100 text-gray-700',
        success: 'bg-success-50 text-success-600 border border-success-200',
        warning: 'bg-warning-50 text-warning-600 border border-warning-200',
        error: 'bg-error-50 text-error-600 border border-error-200',
        info: 'bg-info-50 text-info-600',
        brand: 'bg-brand-50 text-brand-700',
      },
    },
    defaultVariants: { variant: 'neutral' },
  },
);

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
