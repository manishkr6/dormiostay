import { useId } from 'react';
import type { FieldProps } from '../components/ui/field';
export function useField({ id, helper, error, success }: FieldProps) {
  const generatedId = useId();
  const fieldId = id ?? generatedId;
  return {
    id: fieldId,
    'aria-invalid': error ? (true as const) : undefined,
    'aria-describedby': helper || error || success ? `${fieldId}-message` : undefined,
  };
}
