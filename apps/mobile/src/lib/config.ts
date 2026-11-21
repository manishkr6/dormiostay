export function validateApiBase(value: string): string {
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

export function configuredApiBase(): string | undefined {
  const value = process.env.EXPO_PUBLIC_API_BASE_URL;
  return value ? validateApiBase(value) : undefined;
}

/**
 * Determines whether the current mobile runtime is in development mode.
 * Returns true only in development builds and local development environments.
 * Production and preview builds return false to guard internal tools and showcase routes.
 */
export function isDevelopment(): boolean {
  const variant = process.env.EXPO_PUBLIC_APP_VARIANT || process.env.APP_VARIANT;
  if (variant) {
    return variant === 'development';
  }
  const nodeEnv = process.env.NODE_ENV as string | undefined;
  if (nodeEnv === 'production') {
    return false;
  }
  if (typeof __DEV__ !== 'undefined') {
    return Boolean(__DEV__);
  }
  return nodeEnv !== 'production';
}
