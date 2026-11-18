import { useField } from '../../hooks/use-field';
import type { SelectHTMLAttributes } from 'react';
import { Field, type FieldProps } from './field';
import { cn } from '../../lib/utils';

type Props = SelectHTMLAttributes<HTMLSelectElement> & FieldProps;
export function Select({
  label,
  helper,
  error,
  success,
  id,
  className,
  children,
  ...props
}: Props) {
  const fieldProps = {
    label,
    ...(id ? { id } : {}),
    ...(helper ? { helper } : {}),
    ...(error ? { error } : {}),
    ...(success ? { success } : {}),
  };
  const field = useField(fieldProps);
  return (
    <Field {...fieldProps} id={field.id}>
      <select {...props} {...field} className={cn('control', className)}>
        {children}
      </select>
    </Field>
  );
}
