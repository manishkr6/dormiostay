import { Screen } from '../../src/components/screen';
import { EmptyState } from '../../src/components/ui';

export default function TenantVisitsScreen() {
  return (
    <Screen
      title="Scheduled Visits"
      subtitle="Track your upcoming and past property visits"
      safeBottom={false}
    >
      <EmptyState
        title="No scheduled visits"
        message="When you schedule an in-person PG visit with a verified owner, your appointment details will appear here."
      />
    </Screen>
  );
}
