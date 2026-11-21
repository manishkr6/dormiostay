import { View } from 'react-native';
import { useRouter } from 'expo-router';
import { Screen } from '../src/components/screen';
import { Badge, Body, Button, Card, Heading } from '../src/components/ui';

export default function HelpScreen() {
  const router = useRouter();

  return (
    <Screen
      title="Help & Support"
      subtitle="Frequently asked questions for students and property owners"
    >
      <Card>
        <Badge label="FAQ" tone="info" />
        <Heading>How does DormioStay verify listings?</Heading>
        <Body>
          Every PG and hostel listing is verified by the DormioStay moderation team before being
          published. We check ownership documents, location accuracy, and safety standards.
        </Body>
      </Card>

      <Card>
        <Badge label="FAQ" tone="info" />
        <Heading>Are there brokerage fees?</Heading>
        <Body>
          No. DormioStay is 100% free for students of ICFAI University Sikkim and Gangtok colleges.
          You connect directly with verified property owners.
        </Body>
      </Card>

      <Card>
        <Badge label="FAQ" tone="info" />
        <Heading>How do visits work?</Heading>
        <Body>
          Students can request an in-person visit slot. Property owners confirm or suggest alternate
          timings directly through the platform.
        </Body>
      </Card>

      <Card>
        <Heading>Contact Support</Heading>
        <Body muted>Need help with your account or have a safety concern?</Body>
        <View style={{ gap: 8, marginTop: 4 }}>
          <Body>Email: support@dormiostay.com</Body>
          <Body>Campus Desk: Student Welfare Office, ICFAI Sikkim</Body>
        </View>
        <Button label="Back to Explore" variant="secondary" onPress={() => router.back()} />
      </Card>
    </Screen>
  );
}
