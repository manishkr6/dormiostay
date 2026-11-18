import type { ButtonHTMLAttributes } from 'react';
import { LoaderCircle } from 'lucide-react';
import { cn } from '../../lib/utils';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'destructive' | 'accent' | 'icon';
type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: 'small' | 'medium' | 'large' | 'default';
  loading?: boolean;
};
export function Button({
  variant = 'primary',
  size = 'default',
  loading = false,
  disabled,
  children,
  className,
  type = 'button',
  ...props
}: Props) {
  return (
    <button
      {...props}
      type={type}
      className={cn('button', `button-${variant}`, `button-${size}`, className)}
      disabled={disabled || loading}
      aria-label={
        props['aria-label'] ?? (loading && typeof children === 'string' ? children : undefined)
      }
      aria-busy={loading || undefined}
    >
      <span className={loading ? 'invisible' : undefined}>{children}</span>
      {loading && <span className="sr-only">{children}</span>}
      {loading && <LoaderCircle aria-hidden="true" className="button-spinner spin" />}
    </button>
  );
}
