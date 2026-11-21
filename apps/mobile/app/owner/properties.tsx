import { Screen } from '../../src/components/screen';
import { Badge, Body, Button, Card, Heading } from '../../src/components/ui';
import { useFeedback } from '../../src/providers/feedback';

export default function OwnerPropertiesScreen() {
  const showFeedback = useFeedback();

  return (
    <Screen
      title="My Properties"
      subtitle="PGs and hostels listed under your owner profile"
      safeBottom={false}
    >
      <Card>
        <Badge label="Published" tone="success" />
        <Heading>Mountain View PG</Heading>
        <Body>Tadong, Gangtok • 12 rooms (2 available)</Body>
        <Body muted>₹8,500 / month • Verified</Body>
        <Button
          label="Manage Listing (Mock)"
          variant="secondary"
          onPress={() => {
            showFeedback({
              message: 'Listing management will be delivered in ticket T-23M.',
              tone: 'info',
            });
          }}
        />
      </Card>

      <Card>
        <Badge label="Draft" tone="warning" />
        <Heading>Valley Green Hostel</Heading>
        <Body>5th Mile, Tadong • 8 rooms (pending photos)</Body>
        <Body muted>₹7,000 / month • In review</Body>
        <Button
          label="Complete Wizard (Mock)"
          variant="secondary"
          onPress={() => {
            showFeedback({
              message: 'Owner listing wizard will be delivered in ticket T-21M.',
              tone: 'info',
            });
          }}
        />
      </Card>
    </Screen>
  );
}
