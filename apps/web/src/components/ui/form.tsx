import { forwardRef, type HTMLAttributes, type ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface FieldProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
}

/** Контейнер для пары label + input. Сам ничего не делает, только spacing + структура. */
export const Field = forwardRef<HTMLDivElement, FieldProps>(
  ({ className, children, ...props }, ref) => (
    <div ref={ref} className={cn('flex flex-col gap-1.5', className)} {...props}>
      {children}
    </div>
  ),
);
Field.displayName = 'Field';

interface FieldErrorProps extends HTMLAttributes<HTMLParagraphElement> {
  id: string;
  children?: ReactNode;
}

/**
 * Текст ошибки. Всегда рендерим, но скрываем visually когда нет — стабильный layout
 * + screen-reader всегда находит элемент по aria-describedby.
 */
export const FieldError = forwardRef<HTMLParagraphElement, FieldErrorProps>(
  ({ className, id, children, ...props }, ref) => (
    <p
      ref={ref}
      id={id}
      role={children ? 'alert' : undefined}
      className={cn(
        'min-h-[1.25rem] text-body-sm text-error-600',
        !children && 'sr-only',
        className,
      )}
      {...props}
    >
      {children}
    </p>
  ),
);
FieldError.displayName = 'FieldError';

interface FieldHelperProps extends HTMLAttributes<HTMLParagraphElement> {
  id: string;
  children: ReactNode;
}

export const FieldHelper = forwardRef<HTMLParagraphElement, FieldHelperProps>(
  ({ className, id, children, ...props }, ref) => (
    <p ref={ref} id={id} className={cn('text-body-sm text-gray-500', className)} {...props}>
      {children}
    </p>
  ),
);
FieldHelper.displayName = 'FieldHelper';
