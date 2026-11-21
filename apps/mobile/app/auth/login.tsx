import { useState } from 'react';
import { View } from 'react-native';
import { useRouter } from 'expo-router';
import { Screen } from '../../src/components/screen';
import { Badge, Body, Button, Card, Heading, Input } from '../../src/components/ui';
import { useFeedback } from '../../src/providers/feedback';

export default function LoginScreen() {
  const router = useRouter();
  const showFeedback = useFeedback();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');

  return (
    <Screen
      title="Sign In to DormioStay"
      subtitle="Visual auth foundation preview (no real authentication active in T-04M)"
    >
      <Card>
        <Badge label="Foundation Preview" tone="info" />
        <Heading>Welcome Back</Heading>
        <Body muted>
          Enter your university email or owner credentials. Full server-side session authentication
          will be activated in ticket T-06.
        </Body>

        <Input
          label="Email Address"
          value={email}
          onChangeText={setEmail}
          placeholder="student@icfai.edu.in"
          keyboardType="email-address"
          autoCapitalize="none"
          autoCorrect={false}
        />

        <Input
          label="Password"
          value={password}
          onChangeText={setPassword}
          placeholder="Enter your password"
          secureTextEntry
        />

        <Button
          label="Sign In (Mock Preview)"
          variant="primary"
          onPress={() => {
            showFeedback({
              message: 'Authentication will be connected in tickets T-06/T-07.',
              tone: 'info',
            });
          }}
        />

        <View style={{ gap: 8, marginTop: 8 }}>
          <Button
            label="Don't have an account? Sign Up"
            variant="ghost"
            onPress={() => router.push('/auth/signup')}
          />
          <Button label="Back to Explore" variant="secondary" onPress={() => router.push('/')} />
        </View>
      </Card>
    </Screen>
  );
}
