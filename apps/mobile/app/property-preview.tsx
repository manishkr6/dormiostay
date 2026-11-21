import { View } from 'react-native';
import { useRouter } from 'expo-router';
import { Screen } from '../src/components/screen';
import { Badge, Body, Button, Card, Heading } from '../src/components/ui';
import { useFeedback } from '../src/providers/feedback';

export default function PropertyPreviewScreen() {
  const router = useRouter();
  const showFeedback = useFeedback();

  return (
    <Screen
      title="Mountain View PG"
      subtitle="Tadong, Gangtok • 5 min from ICFAI University Sikkim"
    >
      <Card>
        <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
          <Badge label="Verified by Admin" tone="success" />
          <Badge label="Boys & Girls Wings" tone="info" />
          <Badge label="Fast Wi-Fi" tone="info" />
        </View>
        <Heading>₹8,500 / month</Heading>
        <Body>
          Spacious, well-ventilated rooms with private balconies facing the valley. Includes 3 meals
          daily, 24/7 hot water geyser, power backup inverter, and laundry facilities.
        </Body>
      </Card>

      <Card>
        <Heading>Amenities & House Rules</Heading>
        <View style={{ gap: 8 }}>
          <Body>• High-speed Wi-Fi (100 Mbps fiber)</Body>
          <Body>• 3 North-Indian & Sikkimese meals included</Body>
          <Body>• Study desk, wardrobe & ergonomic chair per bed</Body>
          <Body>• Gate closing time: 9:30 PM</Body>
          <Body>• CCTV security in common areas</Body>
        </View>
      </Card>

      <Card>
        <Heading>Visit Scheduling</Heading>
        <Body muted>
          In T-29/T-30, tenants will be able to book in-person visits directly with the owner.
        </Body>
        <Button
          label="Request Visit (Mock Action)"
          variant="primary"
          onPress={() => {
            showFeedback({
              message: 'Visit requests will be enabled in ticket T-30.',
              tone: 'info',
            });
          }}
        />
        <Button label="Back to Explore" variant="secondary" onPress={() => router.back()} />
      </Card>
    </Screen>
  );
}
