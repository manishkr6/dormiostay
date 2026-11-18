import { useState, type ReactNode } from 'react';
import * as Primitive from '@radix-ui/react-toast';
import { CheckCircle, CircleAlert, Info, X } from 'lucide-react';
import { ToastContext, type ToastMessage } from '../../lib/toast-context';
import { Button } from './button';
export function Toast({
  message,
  tone = 'info',
  retry,
  onOpenChange,
}: ToastMessage & { onOpenChange?: (open: boolean) => void }) {
  const Icon = tone === 'success' ? CheckCircle : tone === 'error' ? CircleAlert : Info;
  return (
    <Primitive.Root
      className={`toast tone-${tone}`}
      duration={5000}
      {...(onOpenChange ? { onOpenChange } : {})}
    >
      <Icon aria-hidden="true" />
      <Primitive.Title>{message}</Primitive.Title>
      {retry && (
        <Primitive.Action altText="Retry the failed action" asChild>
          <Button variant="ghost" onClick={retry}>
            Retry
          </Button>
        </Primitive.Action>
      )}
      <Primitive.Close asChild>
        <Button variant="icon" aria-label="Dismiss notification">
          <X aria-hidden="true" />
        </Button>
      </Primitive.Close>
    </Primitive.Root>
  );
}
export function ToastProvider({ children }: { children: ReactNode }) {
  const [messages, setMessages] = useState<(ToastMessage & { id: number })[]>([]);
  const [nextId] = useState(() => ({ value: 0 }));
  function add(message: ToastMessage) {
    setMessages((items) => [...items, { ...message, id: nextId.value++ }]);
  }
  return (
    <ToastContext.Provider value={add}>
      <Primitive.Provider duration={5000} swipeDirection="right">
        {children}
        {messages.map((item) => (
          <Toast
            key={item.id}
            {...item}
            onOpenChange={(open) => {
              if (!open) setMessages((items) => items.filter((message) => message.id !== item.id));
            }}
          />
        ))}
        <Primitive.Viewport className="toast-viewport" />
      </Primitive.Provider>
    </ToastContext.Provider>
  );
}
