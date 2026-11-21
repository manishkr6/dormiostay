import { Tabs } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Building, Calendar, Clock, Compass, User } from 'lucide-react-native';
import { tokens as t } from '../../src/theme';

export default function OwnerLayout() {
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
          title: 'Overview',
          tabBarLabel: 'Overview',
          tabBarAccessibilityLabel: 'Owner Overview Tab',
          tabBarIcon: ({ color, size }) => <Compass size={size} color={color} accessible={false} />,
        }}
      />
      <Tabs.Screen
        name="properties"
        options={{
          title: 'Properties',
          tabBarLabel: 'Properties',
          tabBarAccessibilityLabel: 'Owner Properties Tab',
          tabBarIcon: ({ color, size }) => (
            <Building size={size} color={color} accessible={false} />
          ),
        }}
      />
      <Tabs.Screen
        name="visits"
        options={{
          title: 'Visits',
          tabBarLabel: 'Visits',
          tabBarAccessibilityLabel: 'Owner Visits Tab',
          tabBarIcon: ({ color, size }) => (
            <Calendar size={size} color={color} accessible={false} />
          ),
        }}
      />
      <Tabs.Screen
        name="availability"
        options={{
          title: 'Availability',
          tabBarLabel: 'Availability',
          tabBarAccessibilityLabel: 'Owner Availability Tab',
          tabBarIcon: ({ color, size }) => <Clock size={size} color={color} accessible={false} />,
        }}
      />
      <Tabs.Screen
        name="account"
        options={{
          title: 'Account',
          tabBarLabel: 'Account',
          tabBarAccessibilityLabel: 'Owner Account Tab',
          tabBarIcon: ({ color, size }) => <User size={size} color={color} accessible={false} />,
        }}
      />
    </Tabs>
  );
}
