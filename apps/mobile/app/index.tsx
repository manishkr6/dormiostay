import { View, Image } from 'react-native';
import { useRouter } from 'expo-router';
import { Screen } from '../src/components/screen';
import { Badge, Body, Button, Card, Heading } from '../src/components/ui';
import iconSource from '../assets/icon.png';
import { isDevelopment } from '../src/lib/config';

export default function ExploreScreen() {
  const router = useRouter();

  return (
    <Screen
      title="Explore Accommodations"
      subtitle="Verified student housing near ICFAI University Sikkim and Gangtok"
    >
      <Card>
        <Badge label="T-04M Mobile Foundation" tone="info" />
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, marginTop: 4 }}>
          <Image
            source={iconSource}
            style={{ width: 44, height: 44, borderRadius: 12 }}
            accessibilityLabel="DormioStay App Icon"
          />
          <Heading>Welcome to DormioStay</Heading>
        </View>
        <Body>
          This native mobile foundation demonstrates the DormioStay design tokens, accessible UI
          kit, and public, auth, tenant, and owner navigation shells with mock data.
        </Body>
        <View style={{ gap: 12, marginTop: 8 }}>
          {isDevelopment() && (
            <Button
              label="Open Component Showcase"
              variant="primary"
              onPress={() => router.push('/showcase')}
            />
          )}
          <Button
            label="Sample Property Preview"
            variant="secondary"
            onPress={() => router.push('/property-preview')}
          />
        </View>
      </Card>

      <Card>
        <Heading>Navigation Shells</Heading>
        <Body muted>
          Preview role-specific navigation shells with foundation mock data. Real authentication
          will be integrated in future tickets.
        </Body>
        <View style={{ gap: 12, marginTop: 8 }}>
          <Button
            label="Auth Shell (Login / Signup)"
            variant="secondary"
            onPress={() => router.push('/auth/login')}
          />
          <Button
            label="Tenant Navigation Shell"
            variant="secondary"
            onPress={() => router.push('/tenant')}
          />
          <Button
            label="Owner Navigation Shell"
            variant="secondary"
            onPress={() => router.push('/owner')}
          />
          <Button label="Help & Support" variant="ghost" onPress={() => router.push('/help')} />
        </View>
      </Card>

      <Card>
        <Badge label="Mock Preview" tone="success" />
        <Heading>Mountain View PG — Tadong</Heading>
        <Body>
          Single & twin sharing available. High-speed Wi-Fi, power backup, hot water, and 3 daily
          meals. 5-minute walk to ICFAI campus.
        </Body>
        <Body muted>₹8,500 / month • All bills included</Body>
        <Button
          label="View Property Details"
          variant="secondary"
          onPress={() => router.push('/property-preview')}
        />
      </Card>
    </Screen>
  );
}
