import * as Sentry from '@sentry/react-native';
import type { Event } from '@sentry/react-native';

/**
 * Strict allowlist redaction function:
 * Strips all event messages, exception values, stack traces, breadcrumbs, user info,
 * request headers/bodies, and extra context to ensure zero credentials, tokens, or PII
 * ever enter telemetry.
 */
export function redactEvent<T extends Event = Event>(event: T): T {
  const redacted: Record<string, unknown> = {
    message: 'Mobile foundation error',
    tags: { client: 'native-mobile' },
  };
  if (event.event_id !== undefined) redacted.event_id = event.event_id;
  if (event.timestamp !== undefined) redacted.timestamp = event.timestamp;
  if (event.platform !== undefined) redacted.platform = event.platform;
  if (event.level !== undefined) redacted.level = event.level;
  return redacted as T;
}

/**
 * Validates that a string is a valid HTTPS Sentry DSN.
 * Expected format: https://<publicKey>@<host>/<projectId>
 */
export function isValidDsn(dsn?: string | null): boolean {
  if (!dsn || typeof dsn !== 'string') return false;
  const trimmed = dsn.trim();
  if (!trimmed) return false;
  try {
    const url = new URL(trimmed);
    return (
      url.protocol === 'https:' &&
      Boolean(url.username) &&
      Boolean(url.hostname) &&
      url.pathname.length > 1 &&
      !url.search &&
      !url.hash
    );
  } catch {
    return false;
  }
}

/**
 * Initializes Sentry React Native SDK with strict privacy and security configurations.
 * Connects redactEvent to beforeSend and disables sensitive telemetry collection.
 * Missing, empty, or invalid DSN results in safe no-op behavior and returns false.
 */
export function initTelemetry(customDsn?: string | null): boolean {
  const dsn = customDsn !== undefined ? customDsn : process.env.EXPO_PUBLIC_SENTRY_DSN;
  if (!isValidDsn(dsn)) {
    return false;
  }

  Sentry.init({
    dsn: dsn!.trim(),
    beforeSend: (event) => redactEvent(event),
    enableNative: false,
    sendDefaultPii: false,
    enableAutoSessionTracking: false,
    tracesSampleRate: 0,
    maxBreadcrumbs: 0,
    attachScreenshot: false,
    attachViewHierarchy: false,
    enableUserInteractionTracing: false,
    enableCaptureFailedRequests: false,
    enableNativeFramesTracking: false,
  });

  return true;
}
