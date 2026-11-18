import { useId, type InputHTMLAttributes } from 'react';
import { cn } from '../../lib/utils';

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> & { label: string };
export function Checkbox({ label, id, className, ...props }: Props) {
  const generatedId = useId();
  return (
    <label className={cn('choice', className)} htmlFor={id ?? generatedId}>
      <input {...props} id={id ?? generatedId} type="checkbox" />
      {label}
    </label>
  );
}
