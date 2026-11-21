import { useEffect, useState, type ReactNode } from 'react';
import { AppState, Platform } from 'react-native';
import {
  focusManager,
  onlineManager,
  QueryClient,
  QueryClientProvider,
} from '@tanstack/react-query';
import * as Network from 'expo-network';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { FeedbackProvider } from './feedback';
export function AppProviders({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { retry: false, staleTime: 30000 },
          mutations: { retry: false },
        },
      }),
  );
  useEffect(() => {
    const app = AppState.addEventListener('change', (state) => {
      if (Platform.OS !== 'web') focusManager.setFocused(state === 'active');
    });
    onlineManager.setEventListener((setOnline) => {
      let mounted = true;
      const update = (state: Network.NetworkState) =>
        setOnline(state.isConnected !== false && state.isInternetReachable !== false);
      void Network.getNetworkStateAsync()
        .then((state) => {
          if (mounted) update(state);
        })
        .catch(() => {});
      const subscription = Network.addNetworkStateListener(update);
      return () => {
        mounted = false;
        subscription.remove();
      };
    });
    return () => {
      app.remove();
      onlineManager.setEventListener(() => () => {});
      client.clear();
    };
  }, [client]);
  return (
    <SafeAreaProvider>
      <QueryClientProvider client={client}>
        <FeedbackProvider>{children}</FeedbackProvider>
      </QueryClientProvider>
    </SafeAreaProvider>
  );
}
