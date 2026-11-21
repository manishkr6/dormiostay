import { useRouter } from 'expo-router';
import { Screen } from '../src/components/screen';
import { Body, Button, Card, Heading } from '../src/components/ui';

export default function NotFoundScreen() {
  const router = useRouter();

  return (
    <Screen title="Page Not Found" subtitle="The requested screen does not exist.">
      <Card>
        <Heading>Screen Not Found</Heading>
        <Body muted>This route was not found in the DormioStay mobile navigation tree.</Body>
        <Button label="Back to Explore" variant="primary" onPress={() => router.replace('/')} />
      </Card>
    </Screen>
  );
}
