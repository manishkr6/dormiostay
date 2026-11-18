import { useSyncExternalStore } from 'react';
import { LoaderCircle } from 'lucide-react';
import { requestActivity } from '../../lib/api-client';
export function WakingBanner({ preview = false }: { preview?: boolean }) {
  const slow = useSyncExternalStore(
    requestActivity.subscribe,
    requestActivity.getSnapshot,
    () => false,
  );
  if (!slow && !preview) return null;
  return (
    <div className="waking-banner" role="status">
      <LoaderCircle aria-hidden="true" className="spin" />
      Waking things up… this can take up to a minute.
    </div>
  );
}
