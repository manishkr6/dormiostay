import { Stack } from 'expo-router';
import { tokens as t } from '../../src/theme';

export default function AuthLayout() {
  return (
    <Stack
      screenOptions={{
        headerStyle: { backgroundColor: t.colors.ivory },
        headerTintColor: t.colors.indigo,
        headerTitleStyle: {
          fontFamily: t.fonts.heading,
          fontSize: 20,
          color: t.colors.indigo,
        },
        contentStyle: { backgroundColor: t.colors.ivory },
        headerShadowVisible: false,
      }}
    >
      <Stack.Screen name="login" options={{ title: 'Sign In' }} />
      <Stack.Screen name="signup" options={{ title: 'Create Account' }} />
    </Stack>
  );
}
