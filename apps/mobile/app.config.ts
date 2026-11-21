import type { ConfigContext, ExpoConfig } from 'expo/config';
import tokens from './src/theme/tokens.json';

function validateApiBase(value: string): string {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error('API base must be an HTTPS URL ending in /api/v1.');
  }
  if (
    url.protocol !== 'https:' ||
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    url.pathname !== '/api/v1'
  ) {
    throw new Error('API base must be an HTTPS URL ending in /api/v1.');
  }
  return url.href;
}

export default ({ config }: ConfigContext): ExpoConfig => {
  const variant = process.env.APP_VARIANT || 'development';
  if (!['development', 'preview', 'production'].includes(variant)) {
    throw new Error('Invalid APP_VARIANT');
  }
  const apiBase = process.env.EXPO_PUBLIC_API_BASE_URL;
  if (apiBase) validateApiBase(apiBase);
  const suffix = variant === 'production' ? '' : `.${variant}`;

  return {
    ...config,
    name: variant === 'production' ? 'DormioStay' : `DormioStay ${variant}`,
    slug: 'dormiostay-mobile',
    version: '0.1.0',
    scheme: `dormiostay${variant === 'production' ? '' : `-${variant}`}`,
    userInterfaceStyle: 'light',
    orientation: 'default',
    icon: './assets/icon.png',
    ios: {
      bundleIdentifier: `app.dormiostay.mobile${suffix}`,
      supportsTablet: true,
      config: { usesNonExemptEncryption: false },
    },
    android: {
      package: `app.dormiostay.mobile${suffix}`,
      permissions: [],
      blockedPermissions: [
        'android.permission.CAMERA',
        'android.permission.RECORD_AUDIO',
        'android.permission.ACCESS_FINE_LOCATION',
        'android.permission.ACCESS_COARSE_LOCATION',
        'android.permission.READ_CONTACTS',
        'android.permission.READ_MEDIA_IMAGES',
        'android.permission.READ_MEDIA_VIDEO',
        'android.permission.READ_EXTERNAL_STORAGE',
        'android.permission.WRITE_EXTERNAL_STORAGE',
        'android.permission.POST_NOTIFICATIONS',
        'android.permission.SYSTEM_ALERT_WINDOW',
      ],
      adaptiveIcon: {
        foregroundImage: './assets/icon.png',
        backgroundColor: tokens.colors.indigo,
      },
      softwareKeyboardLayoutMode: 'resize',
    },
    plugins: [
      'expo-router',
      ['expo-secure-store', { configureAndroidBackup: true }],
      [
        'expo-splash-screen',
        {
          image: './assets/icon.png',
          imageWidth: 120,
          backgroundColor: tokens.colors.ivory,
        },
      ],
      './plugins/with-ios-scene-delegate.cjs',
    ],
    experiments: { typedRoutes: true },
  };
};
