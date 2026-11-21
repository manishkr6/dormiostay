import { useState } from 'react';
import { View } from 'react-native';
import { useRouter } from 'expo-router';
import { Screen } from '../../src/components/screen';
import { Badge, Body, Button, Card, Heading, Input, Tabs } from '../../src/components/ui';
import { useFeedback } from '../../src/providers/feedback';

export default function SignupScreen() {
  const router = useRouter();
  const showFeedback = useFeedback();
  const [role, setRole] = useState('Student Tenant');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');

  return (
    <Screen
      title="Create Account"
      subtitle="Visual signup foundation preview (no real authentication active in T-04M)"
    >
      <Card>
        <Badge label="Foundation Preview" tone="info" />
        <Heading>Join DormioStay</Heading>
        <Body muted>Choose your account type to get started:</Body>

        <Tabs options={['Student Tenant', 'Property Owner']} value={role} onChange={setRole} />

        <Input
          label="Full Name"
          value={name}
          onChangeText={setName}
          placeholder="e.g. Tenzing Norbu"
        />

        <Input
          label="Email Address"
          value={email}
          onChangeText={setEmail}
          placeholder={role === 'Student Tenant' ? 'student@icfai.edu.in' : 'owner@gmail.com'}
          keyboardType="email-address"
          autoCapitalize="none"
          autoCorrect={false}
        />

        <Input
          label="Mobile Phone"
          value={phone}
          onChangeText={setPhone}
          placeholder="+91 98000 00000"
          keyboardType="phone-pad"
        />

        <Button
          label="Create Account (Mock Preview)"
          variant="primary"
          onPress={() => {
            showFeedback({
              message: 'Account creation will be connected in tickets T-06/T-07.',
              tone: 'info',
            });
          }}
        />

        <View style={{ gap: 8, marginTop: 8 }}>
          <Button
            label="Already have an account? Sign In"
            variant="ghost"
            onPress={() => router.push('/auth/login')}
          />
          <Button label="Back to Explore" variant="secondary" onPress={() => router.push('/')} />
        </View>
      </Card>
    </Screen>
  );
}
