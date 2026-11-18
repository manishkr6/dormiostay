import { cn } from '../../lib/utils';
export function Skeleton({
  label = 'Loading content',
  className,
}: {
  label?: string;
  className?: string;
}) {
  return (
    <div role="status" aria-label={label} className={cn('skeleton', className)}>
      <span className="sr-only">{label}</span>
    </div>
  );
}
