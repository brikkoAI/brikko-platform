import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}

export function EmptyState({ icon, title, description, action, className }: EmptyStateProps) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-3 rounded-lg border border-gray-200 bg-white py-12 px-6 text-center',
        className,
      )}
    >
      {icon ? <div className="text-gray-400">{icon}</div> : null}
      <h3 className="text-lg font-semibold text-gray-900">{title}</h3>
      {description ? (
        <p className="max-w-md text-body text-gray-500">{description}</p>
      ) : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}
