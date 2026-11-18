import { Modal, type OverlayProps } from './modal';
export function BottomSheet(props: OverlayProps) {
  return <Modal {...props} variant="sheet" />;
}
