import { createContext, useContext } from 'react';
export type ToastMessage = {
  message: string;
  tone?: 'success' | 'info' | 'error';
  retry?: () => void;
};
export const ToastContext = createContext<((toast: ToastMessage) => void) | null>(null);
export function useToast() {
  const context = useContext(ToastContext);
  if (!context) throw new Error('useToast requires ToastProvider');
  return context;
}
