import type { ReactNode } from 'react';
import * as Primitive from '@radix-ui/react-tabs';
export function Tabs({
  label,
  items,
  defaultValue,
}: {
  label: string;
  items: { value: string; label: string; content: ReactNode; disabled?: boolean }[];
  defaultValue?: string;
}) {
  return (
    <Primitive.Root defaultValue={defaultValue ?? items[0]?.value}>
      <Primitive.List aria-label={label} className="tab-list">
        {items.map((item) => (
          <Primitive.Trigger
            key={item.value}
            value={item.value}
            disabled={item.disabled ?? false}
            className="tab"
          >
            {item.label}
          </Primitive.Trigger>
        ))}
      </Primitive.List>
      {items.map((item) => (
        <Primitive.Content key={item.value} value={item.value} className="tab-panel">
          {item.content}
        </Primitive.Content>
      ))}
    </Primitive.Root>
  );
}
