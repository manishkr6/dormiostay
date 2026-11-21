import { Screen } from '../../src/components/screen';
import { EmptyState } from '../../src/components/ui';

export default function OwnerVisitsScreen() {
  return (
    <Screen
      title="Visit Requests"
      subtitle="In-person visits requested by prospective student tenants"
      safeBottom={false}
    >
      <EmptyState
        title="No pending visit requests"
        message="When students request visit appointments for your properties, they will appear here for confirmation or rescheduling."
      />
    </Screen>
  );
}
