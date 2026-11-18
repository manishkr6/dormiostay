import { Modal, type OverlayProps } from './modal';
export function Drawer(props: OverlayProps) {
  return <Modal {...props} variant="drawer" />;
}
