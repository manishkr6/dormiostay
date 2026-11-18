import { act, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { apiClient, ApiError, requestActivity } from './api-client';
import { WakingBanner } from '../components/ui/waking-banner';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
const json = (data: unknown, status = 200, headers?: HeadersInit) =>
  new Response(JSON.stringify(data), { status, ...(headers ? { headers } : {}) });
it('prefixes resources, keeps query parameters and sends first-party credentials', async () => {
  const fetcher = vi.fn().mockResolvedValue(json({ items: [] }));
  vi.stubGlobal('fetch', fetcher);
  expect(await apiClient('/properties?page=2')).toEqual({ items: [] });
  expect(fetcher).toHaveBeenCalledWith(
    '/api/v1/properties?page=2',
    expect.objectContaining({ credentials: 'include', redirect: 'error', method: 'GET' }),
  );
  expect(fetcher).toHaveBeenCalledTimes(1);
});
it.each(['POST', 'PATCH', 'PUT', 'DELETE'] as const)(
  '%s obtains CSRF before sending JSON, with no write retries',
  async (method) => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(json({ token: 'test-csrf' }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetcher);
    expect(await apiClient('/example', { method, body: { example: true } })).toBeUndefined();
    expect(fetcher.mock.calls[0]?.[0]).toBe('/api/v1/auth/csrf');
    const options = fetcher.mock.calls[1]?.[1] as RequestInit;
    expect(new Headers(options.headers).get('X-CSRF-Token')).toBe('test-csrf');
    expect(options.body).toBe('{"example":true}');
    expect(options.credentials).toBe('include');
    expect(fetcher).toHaveBeenCalledTimes(2);
  },
);
it('rejects a missing CSRF token before making a write', async () => {
  const fetcher = vi.fn().mockResolvedValue(json({}));
  vi.stubGlobal('fetch', fetcher);
  await expect(apiClient('/example', { method: 'POST' })).rejects.toMatchObject({
    code: 'CSRF_UNAVAILABLE',
  });
  expect(fetcher).toHaveBeenCalledOnce();
});
it('parses standard typed errors, field errors, request id and retry-after', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      json(
        {
          error: {
            code: 'VALIDATION_ERROR',
            message: 'Check your email',
            fields: { email: 'Invalid', ignored: 42 },
            request_id: 'req-test',
          },
        },
        422,
        { 'Retry-After': '30' },
      ),
    ),
  );
  const error = await apiClient('/example').catch((value: unknown) => value);
  expect(error).toBeInstanceOf(ApiError);
  expect(error).toMatchObject({
    status: 422,
    code: 'VALIDATION_ERROR',
    message: 'Check your email',
    fields: { email: 'Invalid' },
    requestId: 'req-test',
    retryAfter: '30',
  });
});
it.each([401, 403, 404, 409, 429, 500, 503])('preserves HTTP status %s', async (status) => {
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockResolvedValue(
        json(
          { error: { code: 'EXPECTED', message: 'Safe message', fields: {}, request_id: 'req' } },
          status,
        ),
      ),
  );
  await expect(apiClient('/example')).rejects.toMatchObject({ status, code: 'EXPECTED' });
});
it('does not surface malformed server text or network details', async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(new Response('private traceback', { status: 500 }))
    .mockRejectedValueOnce(new Error('private network details'));
  vi.stubGlobal('fetch', fetcher);
  await expect(apiClient('/example')).rejects.toMatchObject({ code: 'INVALID_RESPONSE' });
  await expect(apiClient('/example')).rejects.toMatchObject({
    code: 'NETWORK_ERROR',
    message: 'Unable to connect. Check your connection and try again.',
  });
});
it.each([
  'https://external.invalid/api',
  '//external.invalid',
  '/../healthz',
  '/%2e%2e/healthz',
  '/foo\\bar',
  '/%2f%2fexternal.invalid',
  '/api/v1/example',
  '/example#fragment',
])('rejects unsafe resource %s without fetching', async (path) => {
  const fetcher = vi.fn();
  vi.stubGlobal('fetch', fetcher);
  await expect(apiClient(path)).rejects.toThrow();
  expect(fetcher).not.toHaveBeenCalled();
});
it('shows wake-up after four seconds and clears only after all slow requests finish', async () => {
  vi.useFakeTimers();
  let first!: (response: Response) => void;
  let second!: (response: Response) => void;
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise<Response>((resolve) => {
            first = resolve;
          }),
      )
      .mockImplementationOnce(
        () =>
          new Promise<Response>((resolve) => {
            second = resolve;
          }),
      ),
  );
  render(<WakingBanner />);
  const a = apiClient('/a');
  const b = apiClient('/b');
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3999);
  });
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2);
  });
  expect(screen.getByRole('status')).toHaveTextContent(
    'Waking things up… this can take up to a minute.',
  );
  await act(async () => {
    first(json({}));
    await a;
  });
  expect(screen.getByRole('status')).toBeVisible();
  await act(async () => {
    second(json({}));
    await b;
  });
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
  expect(requestActivity.getSnapshot()).toBe(false);
});
it('times out and cleans up the banner, with caller cancellation preserved', async () => {
  vi.useFakeTimers();
  vi.stubGlobal(
    'fetch',
    vi.fn(
      (_url: string, options: RequestInit) =>
        new Promise((_resolve, reject) => {
          options.signal?.addEventListener('abort', () =>
            reject(new DOMException('Aborted', 'AbortError')),
          );
        }),
    ),
  );
  const pending = apiClient('/slow', { timeoutMs: 5000 });
  const assertion = expect(pending).rejects.toMatchObject({ code: 'TIMEOUT' });
  await vi.advanceTimersByTimeAsync(5001);
  await assertion;
  expect(requestActivity.getSnapshot()).toBe(false);
  const controller = new AbortController();
  const cancelled = apiClient('/cancelled', { signal: controller.signal });
  const cancellation = expect(cancelled).rejects.toMatchObject({ name: 'AbortError' });
  controller.abort();
  await cancellation;
  expect(requestActivity.getSnapshot()).toBe(false);
});
it('fast errors never leave a delayed wake-up notification', async () => {
  vi.useFakeTimers();
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
  await expect(apiClient('/example')).rejects.toBeInstanceOf(ApiError);
  await vi.advanceTimersByTimeAsync(5000);
  expect(requestActivity.getSnapshot()).toBe(false);
});

it.each(
  (['GET response', 'CSRF response', 'write response'] as const).flatMap((phase) =>
    (['cancel', 'timeout'] as const).map((stop) => ({ phase, stop })),
  ),
)(
  '$stop during $phase JSON reading preserves cancellation/deadline semantics',
  async ({ phase, stop }) => {
    vi.useFakeTimers();
    const controller = new AbortController();
    const reason = new DOMException('Navigation cancelled the request', 'AbortError');
    let reading!: () => void;
    const started = new Promise<void>((resolve) => {
      reading = resolve;
    });
    const fetcher = vi.fn(async (url: string, options: RequestInit) => {
      if (phase === 'write response' && url.endsWith('/auth/csrf'))
        return json({ token: 'test-csrf' });
      return {
        status: 200,
        ok: true,
        headers: new Headers(),
        json: () =>
          new Promise((_resolve, reject) => {
            reading();
            options.signal?.addEventListener('abort', () => reject(options.signal?.reason), {
              once: true,
            });
          }),
      };
    });
    vi.stubGlobal('fetch', fetcher);
    const pending = apiClient('/example', {
      method: phase === 'GET response' ? 'GET' : 'POST',
      signal: controller.signal,
      timeoutMs: 5000,
    }).catch((error: unknown) => error);
    await started;
    await vi.advanceTimersByTimeAsync(4001);
    expect(requestActivity.getSnapshot()).toBe(true);
    if (stop === 'cancel') {
      controller.abort(reason);
      expect(await pending).toBe(reason);
    } else {
      await vi.advanceTimersByTimeAsync(1000);
      expect(await pending).toMatchObject({ name: 'ApiError', code: 'TIMEOUT', status: 0 });
    }
    expect(fetcher).toHaveBeenCalledTimes(phase === 'write response' ? 2 : 1);
    expect(requestActivity.getSnapshot()).toBe(false);
    await vi.advanceTimersByTimeAsync(65_000);
    expect(requestActivity.getSnapshot()).toBe(false);
  },
);
