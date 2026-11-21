import { validateApiBase } from './config';
import { createRequestTracker, requestTracker } from './request-state';

export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public status = 0,
    public fields: Record<string, string> = {},
    public requestId?: string,
    public retryAfter?: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}
type Transport = (url: string, init: RequestInit) => Promise<Response>;
type Options = {
  method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE';
  body?: unknown;
  signal?: AbortSignal;
  timeoutMs?: number;
};
type Dependencies = {
  fetch: Transport;
  tracker?: ReturnType<typeof createRequestTracker>;
  getSessionToken?: () => Promise<string | null>;
};
const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

export function createApiClient(base: string | undefined, dependencies: Dependencies) {
  const apiBase = base ? validateApiBase(base) : undefined;
  return async function request<T>(path: string, options: Options = {}): Promise<T> {
    if (!apiBase) throw new ApiError('NOT_CONFIGURED', 'The API connection is not configured.');
    // Relative resource paths only. Reject encodings as well as literal traversal separators.
    const pathname = path.split('?')[0] ?? '';
    if (
      !/^\/[A-Za-z0-9_-]+(?:\/[A-Za-z0-9_-]+)*$/.test(pathname) ||
      pathname.startsWith('/api') ||
      /[\\#\r\n]/.test(path)
    ) {
      throw new ApiError('INVALID_PATH', 'Invalid API resource path.');
    }
    const url = new URL(apiBase + path);
    if (url.origin !== new URL(apiBase).origin || !url.pathname.startsWith('/api/v1/'))
      throw new ApiError('INVALID_PATH', 'Invalid API resource path.');
    const timeoutMs = options.timeoutMs ?? 65000;
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0)
      throw new ApiError('INVALID_TIMEOUT', 'Invalid request deadline.');
    const controller = new AbortController();
    let timedOut = false;
    const abort = () => controller.abort(options.signal?.reason);
    if (options.signal?.aborted) abort();
    else options.signal?.addEventListener('abort', abort, { once: true });
    const deadline = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);
    const finish = (dependencies.tracker ?? requestTracker).begin();
    let rejectAbort: (() => void) | undefined;
    const cancelled = new Promise<never>((_, reject) => {
      rejectAbort = () => reject(controller.signal.reason ?? new Error('Request cancelled'));
      if (controller.signal.aborted) rejectAbort();
      else controller.signal.addEventListener('abort', rejectAbort, { once: true });
    });
    try {
      // Also bounds SecureStore/body-reading promises if an adapter ignores AbortSignal.
      const work = async () => {
        controller.signal.throwIfAborted();
        const token = await dependencies.getSessionToken?.();
        controller.signal.throwIfAborted();
        // eslint-disable-next-line no-control-regex
        if (token && /[\s\x00-\x1f\x7f]/.test(token))
          throw new ApiError('INVALID_CREDENTIAL', 'Session credential is invalid.');
        const headers: Record<string, string> = { Accept: 'application/json' };
        if (token) headers.Authorization = `Bearer ${token}`;
        let body: string | undefined;
        if (options.body !== undefined) {
          headers['Content-Type'] = 'application/json';
          try {
            body = JSON.stringify(options.body);
          } catch {
            throw new ApiError('INVALID_BODY', 'Request could not be encoded.');
          }
        }
        const response = await dependencies.fetch(url.href, {
          method: options.method ?? 'GET',
          headers,
          credentials: 'omit',
          redirect: 'error',
          signal: controller.signal,
          ...(body === undefined ? {} : { body }),
        });
        controller.signal.throwIfAborted();
        if (response.redirected || (response.status >= 300 && response.status < 400))
          throw new ApiError('REDIRECT_REJECTED', 'Unexpected API redirect.');
        if (response.url && new URL(response.url).origin !== url.origin)
          throw new ApiError('REDIRECT_REJECTED', 'Unexpected API destination.');
        if (response.status === 204) return undefined as T;
        let data: unknown;
        try {
          data = await response.json();
        } catch (error) {
          if (controller.signal.aborted) throw error;
          throw new ApiError(
            'INVALID_RESPONSE',
            'The server returned an unreadable response.',
            response.status,
          );
        }
        controller.signal.throwIfAborted();
        if (!response.ok) {
          const error = record(data) && record(data.error) ? data.error : {};
          const fields = record(error.fields)
            ? Object.fromEntries(
                Object.entries(error.fields).filter(
                  (entry): entry is [string, string] => typeof entry[1] === 'string',
                ),
              )
            : {};
          throw new ApiError(
            typeof error.code === 'string' ? error.code : 'REQUEST_FAILED',
            typeof error.message === 'string'
              ? error.message
              : 'The request could not be completed.',
            response.status,
            fields,
            typeof error.request_id === 'string'
              ? error.request_id
              : (response.headers.get('x-request-id') ?? undefined),
            response.headers.get('retry-after') ?? undefined,
          );
        }
        return data as T;
      };
      return await Promise.race([work(), cancelled]);
    } catch (error) {
      if (timedOut) throw new ApiError('TIMEOUT', 'The request took too long. Please try again.');
      if (controller.signal.aborted) throw options.signal?.reason ?? error;
      if (error instanceof ApiError) throw error;
      throw new ApiError(
        'NETWORK_ERROR',
        'Could not connect. Check your connection and try again.',
      );
    } finally {
      clearTimeout(deadline);
      finish();
      options.signal?.removeEventListener('abort', abort);
      if (rejectAbort) controller.signal.removeEventListener('abort', rejectAbort);
    }
  };
}
