import { Screen } from '../../src/components/screen';
import { EmptyState } from '../../src/components/ui';

export default function OwnerAvailabilityScreen() {
  return (
    <Screen
      title="Availability & Slots"
      subtitle="Configure days and hours when students can visit your properties"
      safeBottom={false}
    >
      <EmptyState
        title="Availability calendar"
        message="Setting your weekly visit schedules and blackout dates will be enabled in ticket T-32M."
      />
    </Screen>
  );
}
