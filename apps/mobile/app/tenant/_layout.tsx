import { Tabs } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Bell, Bookmark, Calendar, Compass, User } from 'lucide-react-native';
import { tokens as t } from '../../src/theme';

export default function TenantLayout() {
  const insets = useSafeAreaInsets();
  const bottomInset = Math.max(insets.bottom, 8);

  return (
    <Tabs
      screenOptions={{
        headerStyle: { backgroundColor: t.colors.ivory },
        headerTintColor: t.colors.indigo,
        headerTitleStyle: {
          fontFamily: t.fonts.heading,
          fontSize: 20,
          color: t.colors.indigo,
        },
        headerShadowVisible: false,
        tabBarStyle: {
          backgroundColor: t.colors.surface,
          borderTopColor: t.colors.border,
          borderTopWidth: 1,
          height: 56 + bottomInset,
          paddingBottom: bottomInset,
          paddingTop: 6,
        },
        tabBarActiveTintColor: t.colors.violet,
        tabBarInactiveTintColor: t.colors.slate,
        tabBarLabelStyle: {
          fontFamily: t.fonts.medium,
          fontSize: 12,
          lineHeight: 14,
        },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: 'Explore',
          tabBarLabel: 'Explore',
          tabBarAccessibilityLabel: 'Tenant Explore Tab',
          tabBarIcon: ({ color, size }) => <Compass size={size} color={color} accessible={false} />,
        }}
      />
      <Tabs.Screen
        name="saved"
        options={{
          title: 'Saved',
          tabBarLabel: 'Saved',
          tabBarAccessibilityLabel: 'Tenant Saved Tab',
          tabBarIcon: ({ color, size }) => (
            <Bookmark size={size} color={color} accessible={false} />
          ),
        }}
      />
      <Tabs.Screen
        name="visits"
        options={{
          title: 'Visits',
          tabBarLabel: 'Visits',
          tabBarAccessibilityLabel: 'Tenant Visits Tab',
          tabBarIcon: ({ color, size }) => (
            <Calendar size={size} color={color} accessible={false} />
          ),
        }}
      />
      <Tabs.Screen
        name="notifications"
        options={{
          title: 'Notifications',
          tabBarLabel: 'Notifications',
          tabBarAccessibilityLabel: 'Tenant Notifications Tab',
          tabBarIcon: ({ color, size }) => <Bell size={size} color={color} accessible={false} />,
        }}
      />
      <Tabs.Screen
        name="profile"
        options={{
          title: 'Profile',
          tabBarLabel: 'Profile',
          tabBarAccessibilityLabel: 'Tenant Profile Tab',
          tabBarIcon: ({ color, size }) => <User size={size} color={color} accessible={false} />,
        }}
      />
    </Tabs>
  );
}
