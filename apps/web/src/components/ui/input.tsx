import { forwardRef, type InputHTMLAttributes } from 'react';
import { cn } from '@/lib/utils';

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  invalid?: boolean;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ className, invalid, type = 'text', ...props }, ref) => {
    return (
      <input
        ref={ref}
        type={type}
        aria-invalid={invalid || undefined}
        className={cn(
          'h-10 w-full rounded-md border bg-white px-3 text-body text-gray-900',
          'placeholder:text-gray-400',
          'transition-colors duration-150 ease-out',
          'focus-visible:outline-none',
          'disabled:cursor-not-allowed disabled:bg-gray-100 disabled:text-gray-400',
          invalid
            ? 'border-error-600 focus-visible:ring-2 focus-visible:ring-error-600/15'
            : 'border-gray-300 hover:border-gray-400 focus-visible:border-brand-600 focus-visible:ring-2 focus-visible:ring-brand-600/20',
          className,
        )}
        {...props}
      />
    );
  },
);
Input.displayName = 'Input';
