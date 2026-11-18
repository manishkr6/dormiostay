import { useField } from '../../hooks/use-field';
import { useState, type InputHTMLAttributes } from 'react';
import { Eye, EyeOff } from 'lucide-react';
import { Field, type FieldProps } from './field';
import { Button } from './button';
import { cn } from '../../lib/utils';

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, 'prefix'> &
  FieldProps & { prefix?: string };
export function Input({
  label,
  helper,
  error,
  success,
  id,
  className,
  type = 'text',
  prefix,
  ...props
}: Props) {
  const field = useField({
    label,
    ...(id ? { id } : {}),
    ...(helper ? { helper } : {}),
    ...(error ? { error } : {}),
    ...(success ? { success } : {}),
  });
  const [visible, setVisible] = useState(false);
  return (
    <Field
      label={label}
      {...(helper ? { helper } : {})}
      {...(error ? { error } : {})}
      {...(success ? { success } : {})}
      id={field.id}
    >
      <div className="input-wrap">
        {prefix && (
          <span aria-hidden="true" className="input-prefix">
            {prefix}
          </span>
        )}
        <input
          {...props}
          {...field}
          type={type === 'password' && visible ? 'text' : type}
          className={cn(
            'control',
            prefix && 'has-prefix',
            type === 'password' && 'has-toggle',
            className,
          )}
        />
        {type === 'password' && (
          <Button
            variant="icon"
            className="password-toggle"
            disabled={props.disabled}
            aria-label={visible ? 'Hide password' : 'Show password'}
            aria-pressed={visible}
            onClick={() => setVisible(!visible)}
          >
            {visible ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
          </Button>
        )}
      </div>
    </Field>
  );
}
