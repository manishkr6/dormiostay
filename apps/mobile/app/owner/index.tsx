import { View } from 'react-native';
import { useRouter } from 'expo-router';
import { Screen } from '../../src/components/screen';
import { Badge, Body, Button, Card, Heading } from '../../src/components/ui';

export default function OwnerOverviewScreen() {
  const router = useRouter();

  return (
    <Screen
      title="Owner Dashboard"
      subtitle="Manage your properties, visits, and student inquiries"
      safeBottom={false}
    >
      <Card>
        <Badge label="Owner Navigation Shell" tone="info" />
        <Heading>Property Management Foundation</Heading>
        <Body>
          This is the foundation shell for the owner experience. The full owner onboarding wizard,
          pricing, and listing management will be implemented in tickets T-21M through T-23M.
        </Body>
        <View style={{ gap: 8, marginTop: 4 }}>
          <Button
            label="View Properties"
            variant="secondary"
            onPress={() => router.push('/owner/properties')}
          />
          <Button
            label="Switch to Tenant Shell"
            variant="ghost"
            onPress={() => router.push('/tenant')}
          />
          <Button label="Back to Public Explore" variant="ghost" onPress={() => router.push('/')} />
        </View>
      </Card>

      <Card>
        <Heading>Key Metrics (Foundation Mock)</Heading>
        <View style={{ gap: 6 }}>
          <Body>• Active Listings: 2 properties</Body>
          <Body>• Total Inquiries this month: 14</Body>
          <Body>• Confirmed Visits: 3 pending</Body>
        </View>
      </Card>
    </Screen>
  );
}
