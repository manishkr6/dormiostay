import React from 'react';
import { render } from '@testing-library/react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { FeedbackProvider } from '../providers/feedback';

// Mock expo-router without loading untransformed ESM node_modules
jest.mock('expo-router', () => ({
  useRouter: () => ({ push: jest.fn(), replace: jest.fn(), back: jest.fn() }),
  Redirect: ({ href }: { href: string }) => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { Text } = require('react-native');
    return <Text testID="redirect-target">{`Redirect:${href}`}</Text>;
  },
}));

import ShowcaseScreen from '../../app/showcase';
import ExploreScreen from '../../app/index';
import TenantProfileScreen from '../../app/tenant/profile';
import OwnerAccountScreen from '../../app/owner/account';

const safeAreaMetrics = {
  frame: { x: 0, y: 0, width: 390, height: 844 },
  insets: { top: 47, left: 0, right: 0, bottom: 34 },
};

describe('ShowcaseScreen Route Guard', () => {
  it('redirects to / and does not render showcase content when isDev is false', async () => {
    const { getByTestId, queryByText } = await render(<ShowcaseScreen isDev={false} />);

    // Renders the redirect target
    const redirect = getByTestId('redirect-target');
    expect(redirect.props.children).toBe('Redirect:/');

    // Does not render showcase heading or components
    expect(queryByText('Component Showcase')).toBeNull();
    expect(queryByText('DormioStay Mobile UI Kit & Design System Verification')).toBeNull();
    expect(queryByText('Validate & Submit Form')).toBeNull();
  });

  it('renders full component showcase and interactive sections when isDev is true', async () => {
    const { queryByTestId, getByText } = await render(
      <SafeAreaProvider initialMetrics={safeAreaMetrics}>
        <FeedbackProvider>
          <ShowcaseScreen isDev={true} />
        </FeedbackProvider>
      </SafeAreaProvider>,
    );

    // Does not render redirect
    expect(queryByTestId('redirect-target')).toBeNull();

    // Renders showcase title and sections
    expect(getByText('Component Showcase')).toBeTruthy();
    expect(getByText('Brand Color Tokens')).toBeTruthy();
    expect(getByText('Buttons & Variants')).toBeTruthy();
    expect(getByText('Validate & Submit Form')).toBeTruthy();
  });
});

describe('Navigation Entry Points Showcase Guarding', () => {
  const originalAppVariant = process.env.APP_VARIANT;
  const originalExpoAppVariant = process.env.EXPO_PUBLIC_APP_VARIANT;

  afterEach(() => {
    process.env.APP_VARIANT = originalAppVariant;
    process.env.EXPO_PUBLIC_APP_VARIANT = originalExpoAppVariant;
  });

  it('hides showcase link on ExploreScreen in production', async () => {
    process.env.APP_VARIANT = 'production';
    delete process.env.EXPO_PUBLIC_APP_VARIANT;

    const { queryByText } = await render(
      <SafeAreaProvider initialMetrics={safeAreaMetrics}>
        <ExploreScreen />
      </SafeAreaProvider>,
    );
    expect(queryByText('Open Component Showcase')).toBeNull();
  });

  it('shows showcase link on ExploreScreen in development', async () => {
    process.env.APP_VARIANT = 'development';
    delete process.env.EXPO_PUBLIC_APP_VARIANT;

    const { getByText } = await render(
      <SafeAreaProvider initialMetrics={safeAreaMetrics}>
        <ExploreScreen />
      </SafeAreaProvider>,
    );
    expect(getByText('Open Component Showcase')).toBeTruthy();
  });

  it('hides showcase link on TenantProfileScreen in production', async () => {
    process.env.APP_VARIANT = 'production';
    delete process.env.EXPO_PUBLIC_APP_VARIANT;

    const { queryByText } = await render(
      <SafeAreaProvider initialMetrics={safeAreaMetrics}>
        <TenantProfileScreen />
      </SafeAreaProvider>,
    );
    expect(queryByText('Component Showcase')).toBeNull();
  });

  it('shows showcase link on TenantProfileScreen in development', async () => {
    process.env.APP_VARIANT = 'development';
    delete process.env.EXPO_PUBLIC_APP_VARIANT;

    const { getByText } = await render(
      <SafeAreaProvider initialMetrics={safeAreaMetrics}>
        <TenantProfileScreen />
      </SafeAreaProvider>,
    );
    expect(getByText('Component Showcase')).toBeTruthy();
  });

  it('hides showcase link on OwnerAccountScreen in production', async () => {
    process.env.APP_VARIANT = 'production';
    delete process.env.EXPO_PUBLIC_APP_VARIANT;

    const { queryByText } = await render(
      <SafeAreaProvider initialMetrics={safeAreaMetrics}>
        <OwnerAccountScreen />
      </SafeAreaProvider>,
    );
    expect(queryByText('Component Showcase')).toBeNull();
  });

  it('shows showcase link on OwnerAccountScreen in development', async () => {
    process.env.APP_VARIANT = 'development';
    delete process.env.EXPO_PUBLIC_APP_VARIANT;

    const { getByText } = await render(
      <SafeAreaProvider initialMetrics={safeAreaMetrics}>
        <OwnerAccountScreen />
      </SafeAreaProvider>,
    );
    expect(getByText('Component Showcase')).toBeTruthy();
  });
});
