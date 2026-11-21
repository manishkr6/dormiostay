import { View } from 'react-native';
import { useRouter } from 'expo-router';
import { Screen } from '../../src/components/screen';
import { Badge, Body, Button, Card, Heading } from '../../src/components/ui';
import { isDevelopment } from '../../src/lib/config';

export default function TenantProfileScreen() {
  const router = useRouter();

  return (
    <Screen
      title="Student Profile"
      subtitle="Manage your personal information and student preferences"
      safeBottom={false}
    >
      <Card>
        <Badge label="Guest Preview" tone="info" />
        <Heading>Tenzing Norbu (Mock Profile)</Heading>
        <Body>ICFAI University Sikkim • B.Tech Computer Science</Body>
        <Body muted>tenzing.norbu@icfai.edu.in • +91 98000 12345</Body>
      </Card>

      <Card>
        <Heading>Account Settings</Heading>
        <Body muted>
          In ticket T-14M, profile editing, phone verification, and notification preferences will be
          managed here.
        </Body>
        <View style={{ gap: 12, marginTop: 8 }}>
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
