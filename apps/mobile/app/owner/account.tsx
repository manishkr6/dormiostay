import { View } from 'react-native';
import { useRouter } from 'expo-router';
import { Screen } from '../../src/components/screen';
import { Badge, Body, Button, Card, Heading } from '../../src/components/ui';
import { isDevelopment } from '../../src/lib/config';

export default function OwnerAccountScreen() {
  const router = useRouter();

  return (
    <Screen
      title="Owner Account"
      subtitle="Business contact details, banking info, and payout preferences"
      safeBottom={false}
    >
      <Card>
        <Badge label="Verified Owner" tone="success" />
        <Heading>Pemba Lepcha (Mock Account)</Heading>
        <Body>Registered PG Operator • Tadong, East Sikkim</Body>
        <Body muted>pemba.lepcha@gmail.com • +91 94340 54321</Body>
      </Card>

      <Card>
        <Heading>Navigation Shortcuts</Heading>
        <View style={{ gap: 12 }}>
          {isDevelopment() && (
            <Button
              label="Component Showcase"
              variant="secondary"
              onPress={() => router.push('/showcase')}
            />
          )}
          <Button label="Exit to Public Explore" variant="ghost" onPress={() => router.push('/')} />
        </View>
      </Card>
    </Screen>
  );
}
