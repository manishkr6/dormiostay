import { Screen } from '../../src/components/screen';
import { EmptyState } from '../../src/components/ui';

export default function TenantSavedScreen() {
  return (
    <Screen
      title="Saved Accommodations"
      subtitle="Bookmarks and shortlisted student properties"
      safeBottom={false}
    >
      <EmptyState
        title="No saved properties yet"
        message="When you bookmark PGs or hostels while browsing, they will appear here for easy comparison."
      />
    </Screen>
  );
}
