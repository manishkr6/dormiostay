import * as Sentry from '@sentry/react-native';
import type { Event } from '@sentry/react-native';
import { redactEvent, isValidDsn, initTelemetry } from '../lib/telemetry';

jest.mock('@sentry/react-native', () => ({
  init: jest.fn(),
}));

describe('Sentry Mobile Telemetry Redaction', () => {
  it('strips all sensitive user data, messages, breadcrumbs, and extra context', () => {
    const rawEvent = {
      event_id: 'evt_12345',
      timestamp: 1700000000,
      platform: 'react-native',
      level: 'error',
      message: 'Failed to authenticate user user@example.com with session abcdef123456',
      user: {
        id: 'usr_secret',
        email: 'secret@domain.com',
      },
      breadcrumbs: [{ message: 'User entered password: SecretPassword123' }],
      extra: {
        rawToken: 'bearer-token-should-never-be-sent',
      },
      request: {
        headers: {
          Authorization: 'Bearer secret_token',
        },
      },
    } as unknown as Event;

    const redacted = redactEvent(rawEvent);

    expect(redacted.event_id).toBe('evt_12345');
    expect(redacted.timestamp).toBe(1700000000);
    expect(redacted.platform).toBe('react-native');
    expect(redacted.level).toBe('error');
    expect(redacted.message).toBe('Mobile foundation error');
    expect(redacted.tags).toEqual({ client: 'native-mobile' });

    // Assert stripped properties
    expect((redacted as Record<string, unknown>).user).toBeUndefined();
    expect((redacted as Record<string, unknown>).breadcrumbs).toBeUndefined();
    expect((redacted as Record<string, unknown>).extra).toBeUndefined();
    expect((redacted as Record<string, unknown>).request).toBeUndefined();
  });
});

describe('Sentry DSN Validation', () => {
  it('accepts valid HTTPS Sentry DSN URLs', () => {
    expect(isValidDsn('https://abc12345@o123456.ingest.sentry.io/7890123')).toBe(true);
    expect(isValidDsn('  https://key@sentry.example.com/42  ')).toBe(true);
  });

  it('rejects unencrypted HTTP DSNs', () => {
    expect(isValidDsn('http://key@sentry.example.com/42')).toBe(false);
  });

  it('rejects DSNs without public key username or project path', () => {
    expect(isValidDsn('https://sentry.example.com/42')).toBe(false);
    expect(isValidDsn('https://key@sentry.example.com/')).toBe(false);
    expect(isValidDsn('https://key@sentry.example.com')).toBe(false);
  });

  it('rejects DSNs with query parameters or fragments', () => {
    expect(isValidDsn('https://key@sentry.example.com/42?secret=1')).toBe(false);
    expect(isValidDsn('https://key@sentry.example.com/42#frag')).toBe(false);
  });

  it('rejects null, undefined, empty, or malformed strings', () => {
    expect(isValidDsn(undefined)).toBe(false);
    expect(isValidDsn(null)).toBe(false);
    expect(isValidDsn('')).toBe(false);
    expect(isValidDsn('   ')).toBe(false);
    expect(isValidDsn('not-a-url')).toBe(false);
  });
});

describe('Sentry Initialization & Privacy Controls', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('initializes Sentry when a valid DSN is provided', () => {
    const validDsn = 'https://abc12345@o123456.ingest.sentry.io/7890123';
    const initialized = initTelemetry(validDsn);

    expect(initialized).toBe(true);
    expect(Sentry.init).toHaveBeenCalledTimes(1);

    const initCall = (Sentry.init as jest.Mock).mock.calls[0][0];
    expect(initCall.dsn).toBe(validDsn);
    expect(typeof initCall.beforeSend).toBe('function');
    expect(initCall.enableNative).toBe(false);
    expect(initCall.sendDefaultPii).toBe(false);
    expect(initCall.enableAutoSessionTracking).toBe(false);
    expect(initCall.tracesSampleRate).toBe(0);
    expect(initCall.maxBreadcrumbs).toBe(0);
    expect(initCall.attachScreenshot).toBe(false);
    expect(initCall.attachViewHierarchy).toBe(false);
  });

  it('disables native crash reporting to prevent unredacted native exception leaks', () => {
    const validDsn = 'https://abc12345@o123456.ingest.sentry.io/7890123';
    initTelemetry(validDsn);

    const initCall = (Sentry.init as jest.Mock).mock.calls[0][0];
    expect(initCall.enableNative).toBe(false);
  });

  it('connects redactEvent to beforeSend and sanitizes captured events', () => {
    const validDsn = 'https://abc12345@o123456.ingest.sentry.io/7890123';
    initTelemetry(validDsn);

    const initCall = (Sentry.init as jest.Mock).mock.calls[0][0];
    const beforeSend = initCall.beforeSend;

    const rawEvent = {
      event_id: 'evt_secret_999',
      level: 'fatal',
      message: 'Database password leak in memory: P@ssw0rd123!',
      user: { email: 'student@example.com' },
      breadcrumbs: [{ message: 'Submitted credentials' }],
    } as unknown as Event;

    const sanitized = beforeSend(rawEvent);
    expect(sanitized.event_id).toBe('evt_secret_999');
    expect(sanitized.level).toBe('fatal');
    expect(sanitized.message).toBe('Mobile foundation error');
    expect(sanitized.tags).toEqual({ client: 'native-mobile' });
    expect(sanitized.user).toBeUndefined();
    expect(sanitized.breadcrumbs).toBeUndefined();
  });

  it('safely no-ops and returns false when DSN is undefined or invalid', () => {
    const originalEnv = process.env.EXPO_PUBLIC_SENTRY_DSN;
    delete process.env.EXPO_PUBLIC_SENTRY_DSN;

    const emptyResult = initTelemetry();
    expect(emptyResult).toBe(false);
    expect(Sentry.init).not.toHaveBeenCalled();

    const invalidResult = initTelemetry('not-a-valid-dsn');
    expect(invalidResult).toBe(false);
    expect(Sentry.init).not.toHaveBeenCalled();

    process.env.EXPO_PUBLIC_SENTRY_DSN = originalEnv;
  });
});
