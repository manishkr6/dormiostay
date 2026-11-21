import { useEffect, useState, useSyncExternalStore, type ReactNode } from 'react';
import { KeyboardAvoidingView, Platform, RefreshControl, ScrollView, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { onlineManager } from '@tanstack/react-query';
import { requestTracker } from '../lib/request-state';
import { Badge, Heading, Body } from './ui';
import { tokens as t } from '../theme';
export function Screen({
  title,
  subtitle,
  children,
  onRefresh,
  refreshing = false,
  safeBottom = true,
}: {
  title: string;
  subtitle?: string;
  children?: ReactNode;
  onRefresh?: () => void;
  refreshing?: boolean;
  safeBottom?: boolean;
}) {
  const [online, setOnline] = useState(onlineManager.isOnline());
  useEffect(() => onlineManager.subscribe(setOnline), []);
  const requests = useSyncExternalStore(requestTracker.subscribe, requestTracker.getSnapshot);
  return (
    <SafeAreaView
      edges={safeBottom ? ['left', 'right', 'bottom'] : ['left', 'right']}
      style={{ flex: 1, backgroundColor: t.colors.ivory }}
    >
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
        keyboardVerticalOffset={Platform.OS === 'ios' ? 88 : 0}
      >
        <ScrollView
          keyboardShouldPersistTaps="handled"
          keyboardDismissMode="on-drag"
          automaticallyAdjustKeyboardInsets
          {...(onRefresh
            ? {
                refreshControl: (
                  <RefreshControl
                    refreshing={refreshing}
                    onRefresh={onRefresh}
                    tintColor={t.colors.violet}
                  />
                ),
              }
            : {})}
          contentContainerStyle={{
            padding: 16,
            paddingBottom: safeBottom ? 24 : 16,
            gap: 16,
            width: '100%',
            maxWidth: 720,
            alignSelf: 'center',
          }}
        >
          {!online && <Badge label="You’re offline. Previews still work." tone="warning" />}
          {requests.slow > 0 && (
            <Badge label="Waking things up… this can take up to a minute." tone="info" />
          )}
          <View style={{ gap: 4 }}>
            <Heading>{title}</Heading>
            {subtitle && <Body muted>{subtitle}</Body>}
          </View>
          {children}
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}
