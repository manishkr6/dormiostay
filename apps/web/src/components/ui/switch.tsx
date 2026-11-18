import { useId, type ComponentProps } from 'react';
import * as Primitive from '@radix-ui/react-switch';
import { cn } from '../../lib/utils';

type Props = ComponentProps<typeof Primitive.Root> & { label: string };
export function Switch({ label, id, className, ...props }: Props) {
  const generatedId = useId();
  return (
    <div className="choice">
      <Primitive.Root {...props} id={id ?? generatedId} className={cn('switch', className)}>
        <Primitive.Thumb className="switch-thumb" />
      </Primitive.Root>
      <label htmlFor={id ?? generatedId}>{label}</label>
    </div>
  );
}
