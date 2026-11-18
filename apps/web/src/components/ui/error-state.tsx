import { CircleAlert } from 'lucide-react';
import { Button } from './button';
export function ErrorState({
  title = 'Something went wrong',
  description = 'Please try again in a moment.',
  onRetry,
}: {
  title?: string;
  description?: string;
  onRetry: () => void;
}) {
  return (
    <div className="state">
      <div role="alert">
        <CircleAlert className="state-icon" aria-hidden="true" />
        <h3>{title}</h3>
        <p>{description}</p>
      </div>
      <Button variant="secondary" onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}
