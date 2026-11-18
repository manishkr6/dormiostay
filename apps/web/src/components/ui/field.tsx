import { type ReactNode } from 'react';
import { CircleAlert, Check } from 'lucide-react';

export type FieldProps = {
  label: string;
  helper?: string;
  error?: string;
  success?: string;
  id?: string;
};
export function Field({
  label,
  helper,
  error,
  success,
  id,
  children,
}: FieldProps & { children: ReactNode }) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children}
      {(error || success || helper) && (
        <p
          id={`${id}-message`}
          className={error ? 'field-error' : success ? 'field-success' : 'field-helper'}
          role={error ? 'alert' : undefined}
        >
          {error ? (
            <CircleAlert aria-hidden="true" />
          ) : success ? (
            <Check aria-hidden="true" />
          ) : null}
          {error || success || helper}
        </p>
      )}
    </div>
  );
}
