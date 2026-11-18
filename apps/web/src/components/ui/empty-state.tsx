import type { ReactNode } from 'react';
import { SearchX } from 'lucide-react';
export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action: ReactNode;
}) {
  return (
    <div className="state">
      <SearchX className="state-icon" aria-hidden="true" />
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}
