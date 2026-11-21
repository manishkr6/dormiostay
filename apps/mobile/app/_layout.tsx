import { useEffect } from 'react';
import { Stack } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import {
  useFonts,
  Inter_400Regular,
  Inter_500Medium,
  Inter_600SemiBold,
} from '@expo-google-fonts/inter';
import { PlayfairDisplay_600SemiBold } from '@expo-google-fonts/playfair-display';
import { AppProviders } from '../src/providers/app-providers';
import { tokens as t } from '../src/theme';
import { initTelemetry } from '../src/lib/telemetry';

// Initialize Sentry telemetry with strict event redaction (safe no-op when DSN is unset)
initTelemetry();

SplashScreen.preventAutoHideAsync().catch(() => {});

export default function RootLayout() {
  const [fontsLoaded, fontError] = useFonts({
    Inter_400Regular,
    Inter_500Medium,
    Inter_600SemiBold,
    PlayfairDisplay_600SemiBold,
  });

  useEffect(() => {
    if (fontsLoaded || fontError) {
      SplashScreen.hideAsync().catch(() => {});
    }
  }, [fontsLoaded, fontError]);

  if (!fontsLoaded && !fontError) {
    return null;
  }

  return (
    <AppProviders>
      <StatusBar style="dark" />
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
        <Stack.Screen name="index" options={{ title: 'DormioStay' }} />
        <Stack.Screen name="property-preview" options={{ title: 'Property Preview' }} />
        <Stack.Screen name="help" options={{ title: 'Help & Support' }} />
        <Stack.Screen name="showcase" options={{ title: 'Component Showcase' }} />
        <Stack.Screen name="auth" options={{ headerShown: false }} />
        <Stack.Screen name="tenant" options={{ headerShown: false }} />
        <Stack.Screen name="owner" options={{ headerShown: false }} />
        <Stack.Screen name="+not-found" options={{ title: 'Not Found' }} />
      </Stack>
    </AppProviders>
  );
}
