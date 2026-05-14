'use client';

import { Toaster as SonnerToaster, toast } from 'sonner';

/**
 * Sonner для тостов — см. 09_design_system.md §6.6.
 * Variants: default / success / warning / error через toast.<variant>(...).
 */
export function Toaster() {
  return (
    <SonnerToaster
      position="bottom-right"
      duration={4000}
      toastOptions={{
        classNames: {
          toast:
            'group rounded-lg border border-gray-200 bg-white p-4 text-body text-gray-700 shadow-md',
          title: 'font-medium text-gray-900',
          description: 'text-body-sm text-gray-500',
          success:
            'group [&_[data-icon]]:text-success-600 border-success-200',
          error:
            'group [&_[data-icon]]:text-error-600 border-error-200',
          warning:
            'group [&_[data-icon]]:text-warning-600 border-warning-200',
        },
      }}
    />
  );
}

export { toast };
