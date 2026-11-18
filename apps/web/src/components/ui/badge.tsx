import type { HTMLAttributes } from 'react';
import { cn } from '../../lib/utils';
export function Badge({
  tone = 'neutral',
  className,
  ...props
}: HTMLAttributes<HTMLSpanElement> & {
  tone?: 'neutral' | 'success' | 'warning' | 'error' | 'info' | 'brand';
}) {
  return <span {...props} className={cn('badge', `tone-${tone}`, className)} />;
}
