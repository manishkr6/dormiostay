import { Screen } from '../../src/components/screen';
import { EmptyState } from '../../src/components/ui';

export default function TenantNotificationsScreen() {
  return (
    <Screen
      title="Notifications"
      subtitle="Visit confirmations, owner messages, and listing updates"
      safeBottom={false}
    >
      <EmptyState
        title="No new notifications"
        message="Important updates about your visit bookings and property alerts will appear here."
      />
    </Screen>
  );
}
