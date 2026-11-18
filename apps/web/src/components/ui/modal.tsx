import type { ReactElement, ReactNode } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import { Button } from './button';

export type OverlayProps = {
  trigger: ReactElement;
  title: string;
  description: string;
  children: ReactNode;
  footer?: ReactNode;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  onCloseAutoFocus?: (event: Event) => void;
};
export function Modal({
  trigger,
  title,
  description,
  children,
  footer,
  open,
  onOpenChange,
  onCloseAutoFocus,
  variant = 'modal',
}: OverlayProps & { variant?: 'modal' | 'drawer' | 'sheet' }) {
  return (
    <Dialog.Root
      {...(open !== undefined ? { open } : {})}
      {...(onOpenChange ? { onOpenChange } : {})}
    >
      <Dialog.Trigger asChild>{trigger}</Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content
          className={`dialog dialog-${variant}`}
          {...(onCloseAutoFocus ? { onCloseAutoFocus } : {})}
        >
          {variant === 'sheet' && <div className="sheet-handle" aria-hidden="true" />}
          <header className="dialog-header">
            <Dialog.Title>{title}</Dialog.Title>
            <Dialog.Description>{description}</Dialog.Description>
          </header>
          <div className="dialog-body">{children}</div>
          {footer && <footer className="dialog-footer">{footer}</footer>}
          <Dialog.Close asChild>
            <Button variant="icon" className="dialog-close" aria-label="Close dialog">
              <X aria-hidden="true" />
            </Button>
          </Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
