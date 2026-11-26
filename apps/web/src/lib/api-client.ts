export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly fields: Record<string, string> = {},
    public readonly requestId?: string,
    public readonly retryAfter?: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

const listeners = new Set<() => void>();
let slowRequests = 0;
export const requestActivity = {
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
  getSnapshot: () => slowRequests > 0,
};
function notifySlow(delta: number) {
  slowRequests += delta;
  listeners.forEach((listener) => listener());
}
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function apiPath(path: string) {
  // Accept only relative API resources. Never send cookies/CSRF tokens to arbitrary URLs.
  if (!path.startsWith('/') || path.startsWith('//') || /[\\#\s]/.test(path))
    throw new Error('Use an API resource path such as /properties.');
  const url = new URL(`/api/v1${path}`, 'https://same-origin.invalid');
  if (
    !url.pathname.startsWith('/api/v1/') ||
    path
      .split('?')[0]
      ?.split('/')
      .some((part) => {
        try {
          return (
            /[.\\/]/.test(decodeURIComponent(part)) &&
            (decodeURIComponent(part).includes('..') || /[\\/]/.test(decodeURIComponent(part)))
          );
        } catch {
          return true;
        }
      })
  )
    throw new Error('Invalid API resource path.');
  if (path.startsWith('/api/')) throw new Error('API paths are already prefixed with /api/v1.');
  return `${url.pathname}${url.search}`;
}

export type ApiOptions = Omit<RequestInit, 'credentials' | 'body' | 'method' | 'redirect'> & {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
  timeoutMs?: number;
};

async function request<T>(path: string, options: ApiOptions = {}, csrf = true): Promise<T> {
  const url = apiPath(path);
  const { body, timeoutMs = 65_000, signal, method = 'GET', ...init } = options;
  const controller = new AbortController();
  const onAbort = () => controller.abort(signal?.reason);
  signal?.addEventListener('abort', onAbort, { once: true });
  if (signal?.aborted) onAbort();
  let timedOut = false;
  let slow = false;
  const wakeTimer = setTimeout(() => {
    slow = true;
    notifySlow(1);
  }, 4000);
  const timeout = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  try {
    const headers = new Headers(init.headers);
    headers.set('Accept', 'application/json');
    if (body !== undefined) headers.set('Content-Type', 'application/json');
    if (csrf && method !== 'GET') {
      // T-07 supplies this endpoint. Do not cache across session rotation, or retry writes.
      const token = await request<unknown>('/auth/csrf', { signal: controller.signal }, false);
      if (!isRecord(token) || typeof token.token !== 'string' || !token.token)
        throw new ApiError(0, 'CSRF_UNAVAILABLE', 'Please refresh the page and try again.');
      storedCsrfToken = token.token;
      headers.set('X-CSRF-Token', token.token);
    }
    const response = await fetch(url, {
      ...init,
      method,
      headers,
      credentials: 'include',
      redirect: 'error',
      signal: controller.signal,
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
    if (response.status === 204) return undefined as T;
    let data: unknown;
    try {
      data = await response.json();
    } catch (error) {
      // Aborting a body download is cancellation, not malformed server JSON.
      // The outer handler still converts our own deadline into TIMEOUT.
      if (controller.signal.aborted) throw error;
      throw new ApiError(
        response.status,
        'INVALID_RESPONSE',
        'We could not read the server response. Please try again.',
      );
    }
    if (!response.ok) {
      const error = isRecord(data) && isRecord(data.error) ? data.error : undefined;
      if (error && typeof error.code === 'string' && typeof error.message === 'string') {
        const fields = isRecord(error.fields)
          ? Object.fromEntries(
              Object.entries(error.fields).filter(
                (entry): entry is [string, string] => typeof entry[1] === 'string',
              ),
            )
          : {};
        throw new ApiError(
          response.status,
          error.code,
          error.message,
          fields,
          typeof error.request_id === 'string' ? error.request_id : undefined,
          response.headers.get('Retry-After') ?? undefined,
        );
      }
      throw new ApiError(
        response.status,
        'HTTP_ERROR',
        'The request could not be completed. Please try again.',
      );
    }
    return data as T;
  } catch (error) {
    if (timedOut)
      throw new ApiError(
        0,
        'TIMEOUT',
        'The server is taking longer than expected. Please try again.',
      );
    if (controller.signal.aborted) throw error;
    if (error instanceof ApiError) throw error;
    throw new ApiError(
      0,
      'NETWORK_ERROR',
      'Unable to connect. Check your connection and try again.',
    );
  } finally {
    clearTimeout(wakeTimer);
    clearTimeout(timeout);
    signal?.removeEventListener('abort', onAbort);
    if (slow) notifySlow(-1);
  }
}
let storedCsrfToken: string | null = null;

export function getStoredCsrfToken(): string | null {
  return storedCsrfToken;
}

export function setStoredCsrfToken(token: string | null): void {
  storedCsrfToken = token;
}

export function clearCsrfToken(): void {
  storedCsrfToken = null;
}

export async function bootstrapCsrf(signal?: AbortSignal): Promise<string> {
  const options: ApiOptions = signal ? { signal } : {};
  const token = await request<unknown>('/auth/csrf', options, false);
  if (!isRecord(token) || typeof token.token !== 'string' || !token.token) {
    throw new ApiError(0, 'CSRF_UNAVAILABLE', 'Please refresh the page and try again.');
  }
  storedCsrfToken = token.token;
  return token.token;
}

export const apiClient = request;
