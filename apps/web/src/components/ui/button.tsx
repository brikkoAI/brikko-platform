import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import { Loader2 } from 'lucide-react';
import {
  Children,
  cloneElement,
  Fragment,
  forwardRef,
  isValidElement,
  type ButtonHTMLAttributes,
  type ReactElement,
  type ReactNode,
} from 'react';
import { cn } from '@/lib/utils';

const buttonVariants = cva(
  [
    'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md font-medium',
    'transition-colors duration-150 ease-out',
    'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600',
    'disabled:pointer-events-none disabled:opacity-50',
  ].join(' '),
  {
    variants: {
      variant: {
        // Cream Studio v6: brand-600 = espresso в light / cream в dark.
        // text-on-accent инвертируется через CSS-переменную --on-accent
        // (#fff в light / #1c1917 в dark). `!` важен — иначе tailwind-merge
        // конфликтует c кастомным `text-body-sm` (font-size) из size variant
        // и силится color пропадает. См. отчёт WHITE-BUTTONS-2026-05-06.
        primary:
          'bg-brand-600 !text-on-accent shadow-sm hover:bg-brand-700',
        secondary:
          'border border-gray-300 bg-white text-gray-700 hover:border-gray-400 hover:bg-gray-50',
        ghost: 'bg-transparent text-gray-700 hover:bg-gray-100',
        destructive: 'bg-error-600 text-white hover:bg-error-600/90',
        link: 'text-brand-600 underline-offset-4 hover:text-brand-700 hover:underline',
      },
      size: {
        sm: 'h-8 px-3 text-body-sm',
        md: 'h-10 px-4 text-body',
        lg: 'h-12 px-6 text-body-large',
      },
    },
    defaultVariants: { variant: 'primary', size: 'md' },
  },
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  loading?: boolean;
  leftIcon?: ReactNode;
  rightIcon?: ReactNode;
  asChild?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      className,
      variant,
      size,
      loading = false,
      leftIcon,
      rightIcon,
      asChild = false,
      disabled,
      children,
      type,
      ...props
    },
    ref,
  ) => {
    const leadingNode = loading ? (
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
    ) : leftIcon ? (
      <span className="shrink-0" aria-hidden="true">
        {leftIcon}
      </span>
    ) : null;
    const trailingNode =
      !loading && rightIcon ? (
        <span className="shrink-0" aria-hidden="true">
          {rightIcon}
        </span>
      ) : null;

    // Базовые props — общие для button и Slot. `type` — html-атрибут <button>,
    // он не должен попасть в Slot (TS error TS2322 от Radix). Поэтому держим
    // отдельно и передаём только в нативный <button>.
    const baseProps = {
      className: cn(buttonVariants({ variant, size }), className),
      'aria-busy': loading || undefined,
      'aria-disabled': disabled || loading || undefined,
      disabled: disabled || loading,
      ...props,
    };

    // При asChild Radix Slot требует РОВНО один child (Children.only).
    // Клонируем переданный child и пихаем иконки/loader внутрь его собственных
    // children — иначе Slot упадёт с React error #143 при наличии leftIcon/rightIcon.
    //
    // Fragment-children (`<Button asChild><><Link/></></Button>`) — отдельный кейс:
    // Children.only бросит на Fragment, поэтому проверяем по `type === Fragment` ДО
    // вызова Children.only. В этом случае иконки игнорируем и просто оборачиваем —
    // более правильное поведение, чем падать с React #143 в проде. Если внутри
    // Fragment один валидный child, мы продолжим обычный cloneElement-flow.
    if (asChild) {
      const childrenArray = Children.toArray(children);
      const onlyChild = childrenArray[0];

      // Fragment-обёртка: распакуем её и работаем с реальным элементом внутри.
      // Это исправляет TD-005 — раньше Children.only падал на `<><Link/></>`.
      const unwrapped =
        isValidElement(onlyChild) && onlyChild.type === Fragment
          ? Children.toArray((onlyChild as ReactElement<{ children?: ReactNode }>).props.children)[0]
          : onlyChild;

      if (!isValidElement(unwrapped)) {
        // Текстовая нода / число / null — просто Slot без cloneElement-магии.
        return (
          <Slot ref={ref} {...baseProps}>
            {children}
          </Slot>
        );
      }

      const validChild = unwrapped as ReactElement<{ children?: ReactNode }>;
      return (
        <Slot ref={ref} {...baseProps}>
          {cloneElement(
            validChild,
            undefined,
            leadingNode,
            validChild.props.children,
            trailingNode,
          )}
        </Slot>
      );
    }

    return (
      <button ref={ref} {...baseProps} type={type ?? 'button'}>
        {leadingNode}
        {children}
        {trailingNode}
      </button>
    );
  },
);
Button.displayName = 'Button';
