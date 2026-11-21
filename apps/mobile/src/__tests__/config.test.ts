import { validateApiBase, configuredApiBase, isDevelopment } from '../lib/config';

describe('Mobile API Base Configuration', () => {
  it('accepts a valid HTTPS API base ending in /api/v1', () => {
    const valid = 'https://api.dormiostay.com/api/v1';
    expect(validateApiBase(valid)).toBe(valid);
  });

  it('rejects unencrypted HTTP URLs', () => {
    expect(() => validateApiBase('http://api.dormiostay.com/api/v1')).toThrow(
      'API base must be an HTTPS URL ending in /api/v1.',
    );
  });

  it('rejects URLs without /api/v1 path', () => {
    expect(() => validateApiBase('https://api.dormiostay.com/')).toThrow(
      'API base must be an HTTPS URL ending in /api/v1.',
    );
    expect(() => validateApiBase('https://api.dormiostay.com/api/v2')).toThrow(
      'API base must be an HTTPS URL ending in /api/v1.',
    );
  });

  it('rejects URLs with query strings or hashes', () => {
    expect(() => validateApiBase('https://api.dormiostay.com/api/v1?test=1')).toThrow(
      'API base must be an HTTPS URL ending in /api/v1.',
    );
    expect(() => validateApiBase('https://api.dormiostay.com/api/v1#hash')).toThrow(
      'API base must be an HTTPS URL ending in /api/v1.',
    );
  });

  it('rejects URLs with embedded credentials', () => {
    expect(() => validateApiBase('https://user:pass@api.dormiostay.com/api/v1')).toThrow(
      'API base must be an HTTPS URL ending in /api/v1.',
    );
  });

  it('returns undefined when EXPO_PUBLIC_API_BASE_URL is not set', () => {
    const original = process.env.EXPO_PUBLIC_API_BASE_URL;
    delete process.env.EXPO_PUBLIC_API_BASE_URL;
    expect(configuredApiBase()).toBeUndefined();
    process.env.EXPO_PUBLIC_API_BASE_URL = original;
  });
});

describe('Mobile Development Runtime Detection', () => {
  const originalAppVariant = process.env.APP_VARIANT;
  const originalExpoAppVariant = process.env.EXPO_PUBLIC_APP_VARIANT;
  const originalNodeEnv = process.env.NODE_ENV;

  afterEach(() => {
    process.env.APP_VARIANT = originalAppVariant;
    process.env.EXPO_PUBLIC_APP_VARIANT = originalExpoAppVariant;
    process.env.NODE_ENV = originalNodeEnv;
  });

  it('returns true when APP_VARIANT is development', () => {
    process.env.APP_VARIANT = 'development';
    delete process.env.EXPO_PUBLIC_APP_VARIANT;
    expect(isDevelopment()).toBe(true);
  });

  it('returns false when APP_VARIANT is production or preview', () => {
    process.env.APP_VARIANT = 'production';
    expect(isDevelopment()).toBe(false);

    process.env.APP_VARIANT = 'preview';
    expect(isDevelopment()).toBe(false);
  });

  it('returns false when EXPO_PUBLIC_APP_VARIANT is production', () => {
    delete process.env.APP_VARIANT;
    process.env.EXPO_PUBLIC_APP_VARIANT = 'production';
    expect(isDevelopment()).toBe(false);
  });

  it('returns false when NODE_ENV is production and variants are unset', () => {
    delete process.env.APP_VARIANT;
    delete process.env.EXPO_PUBLIC_APP_VARIANT;
    process.env.NODE_ENV = 'production';
    expect(isDevelopment()).toBe(false);
  });
});
