import { useField } from '../../hooks/use-field';
import { useState, type TextareaHTMLAttributes } from 'react';
import { Field, type FieldProps } from './field';
import { cn } from '../../lib/utils';

type Props = TextareaHTMLAttributes<HTMLTextAreaElement> & FieldProps;
export function Textarea({
  label,
  helper,
  error,
  success,
  id,
  className,
  onChange,
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
  const [length, setLength] = useState(String(props.defaultValue ?? '').length);
  return (
    <Field {...fieldProps} id={field.id}>
      <textarea
        {...props}
        {...field}
        rows={props.rows ?? 4}
        className={cn('control', className)}
        onChange={(event) => {
          setLength(event.target.value.length);
          onChange?.(event);
        }}
      />
      {props.maxLength !== undefined && (
        <span className="field-helper">
          {props.value !== undefined ? String(props.value).length : length} / {props.maxLength}{' '}
          characters
        </span>
      )}
    </Field>
  );
}
