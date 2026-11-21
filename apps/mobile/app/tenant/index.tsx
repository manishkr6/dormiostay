import { View } from 'react-native';
import { useRouter } from 'expo-router';
import { Screen } from '../../src/components/screen';
import { Badge, Body, Button, Card, Heading } from '../../src/components/ui';

export default function TenantExploreScreen() {
  const router = useRouter();

  return (
    <Screen
      title="Student Discover"
      subtitle="Find student PGs, hostels, and flats near Gangtok colleges"
      safeBottom={false}
    >
      <Card>
        <Badge label="Tenant Navigation Shell" tone="info" />
        <Heading>Welcome, Student Explorer</Heading>
        <Body>
          This is the foundation shell for the tenant mobile experience. Real property search and
          filtering will be delivered in tickets T-24 through T-28M.
        </Body>
        <View style={{ gap: 12, marginTop: 8 }}>
          <Button
            label="View Sample Listing"
            variant="secondary"
            onPress={() => router.push('/property-preview')}
          />
          <Button
            label="Switch to Owner Shell"
            variant="ghost"
            onPress={() => router.push('/owner')}
          />
          <Button label="Back to Public Explore" variant="ghost" onPress={() => router.push('/')} />
        </View>
      </Card>
    </Screen>
  );
}
