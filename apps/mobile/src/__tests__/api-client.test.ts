import { ApiError, createApiClient } from '../lib/api-client';
import { createRequestTracker } from '../lib/request-state';

describe('Mobile API Client', () => {
  const apiBase = 'https://api.dormiostay.com/api/v1';

  function mockResponse(options: {
    status?: number;
    body?: unknown;
    headers?: Record<string, string>;
    redirected?: boolean;
    url?: string;
  }): Response {
    const status = options.status ?? 200;
    const headersMap = new Map(Object.entries(options.headers ?? {}));
    return {
      status,
      ok: status >= 200 && status < 300,
      redirected: Boolean(options.redirected),
      url: options.url ?? `${apiBase}/healthz`,
      headers: {
        get: (name: string) => headersMap.get(name.toLowerCase()) ?? null,
      } as unknown as Headers,
      json: async () => options.body ?? {},
      text: async () => JSON.stringify(options.body ?? {}),
    } as unknown as Response;
  }

  it('fails fast if API base is not configured', async () => {
    const client = createApiClient(undefined, { fetch: jest.fn() });
    await expect(client('/healthz')).rejects.toThrow(ApiError);
    await expect(client('/healthz')).rejects.toMatchObject({
      code: 'NOT_CONFIGURED',
    });
  });

  it('rejects invalid resource paths with path traversal or illegal characters', async () => {
    const client = createApiClient(apiBase, { fetch: jest.fn() });
    await expect(client('/api/v1/properties')).rejects.toThrow(ApiError);
    await expect(client('//malicious.com')).rejects.toThrow(ApiError);
    await expect(client('/path/../traversal')).rejects.toThrow(ApiError);
    await expect(client('/path#hash')).rejects.toThrow(ApiError);
  });

  it('parses standard DormioStay typed error envelopes correctly', async () => {
    const mockFetch = jest.fn().mockResolvedValue(
      mockResponse({
        status: 400,
        headers: { 'x-request-id': 'req-fallback-456' },
        body: {
          error: {
            code: 'VALIDATION_ERROR',
            message: 'Invalid input fields',
            fields: { email: 'Email is required' },
            request_id: 'req-explicit-123',
          },
        },
      }),
    );

    const client = createApiClient(apiBase, { fetch: mockFetch });

    try {
      await client('/test-endpoint');
      fail('Expected request to throw ApiError');
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      const apiErr = error as ApiError;
      expect(apiErr.code).toBe('VALIDATION_ERROR');
      expect(apiErr.message).toBe('Invalid input fields');
      expect(apiErr.status).toBe(400);
      expect(apiErr.fields).toEqual({ email: 'Email is required' });
      expect(apiErr.requestId).toBe('req-explicit-123');
    }
  });

  it('rejects HTTP redirects with REDIRECT_REJECTED', async () => {
    const mockFetch = jest.fn().mockResolvedValue(
      mockResponse({
        status: 302,
        redirected: true,
      }),
    );

    const client = createApiClient(apiBase, { fetch: mockFetch });
    await expect(client('/redirect-target')).rejects.toMatchObject({
      code: 'REDIRECT_REJECTED',
    });
  });

  it('handles network failures by throwing NETWORK_ERROR', async () => {
    const mockFetch = jest.fn().mockRejectedValue(new Error('DNS lookup failed'));
    const client = createApiClient(apiBase, { fetch: mockFetch });

    await expect(client('/network-check')).rejects.toMatchObject({
      code: 'NETWORK_ERROR',
      message: 'Could not connect. Check your connection and try again.',
    });
  });

  it('handles request timeouts by aborting and throwing TIMEOUT', async () => {
    const mockFetch = jest.fn().mockImplementation(
      (_url: string, init: RequestInit) =>
        new Promise((_, reject) => {
          init.signal?.addEventListener('abort', () => {
            reject(new Error('aborted'));
          });
        }),
    );

    const client = createApiClient(apiBase, { fetch: mockFetch });
    await expect(client('/slow-endpoint', { timeoutMs: 50 })).rejects.toMatchObject({
      code: 'TIMEOUT',
      message: 'The request took too long. Please try again.',
    });
  });

  it('supports caller cancellation via AbortSignal', async () => {
    const controller = new AbortController();
    const mockFetch = jest.fn().mockImplementation(
      (_url: string, init: RequestInit) =>
        new Promise((_, reject) => {
          init.signal?.addEventListener('abort', () => {
            reject(new Error('User cancelled request'));
          });
        }),
    );

    const client = createApiClient(apiBase, { fetch: mockFetch });
    const promise = client('/cancellable', { signal: controller.signal });
    controller.abort(new Error('User cancelled request'));

    await expect(promise).rejects.toThrow('User cancelled request');
  });

  it('injects Bearer token in Authorization header when provided by getSessionToken', async () => {
    let capturedInit: RequestInit | undefined;
    const mockFetch = jest.fn().mockImplementation((_url: string, init: RequestInit) => {
      capturedInit = init;
      return Promise.resolve(mockResponse({ status: 200, body: { success: true } }));
    });

    const client = createApiClient(apiBase, {
      fetch: mockFetch,
      getSessionToken: async () => 'test-bearer-session-token-32-bytes',
    });

    const result = await client<{ success: boolean }>('/auth/me');
    expect(result).toEqual({ success: true });
    expect(capturedInit?.credentials).toBe('omit');
    expect((capturedInit?.headers as Record<string, string>)?.Authorization).toBe(
      'Bearer test-bearer-session-token-32-bytes',
    );
  });

  it('returns undefined for 204 No Content responses', async () => {
    const mockFetch = jest.fn().mockResolvedValue(mockResponse({ status: 204 }));

    const client = createApiClient(apiBase, { fetch: mockFetch });
    const result = await client('/empty-action');
    expect(result).toBeUndefined();
  });

  it('tracks active request lifecycle through request tracker', async () => {
    const tracker = createRequestTracker();
    expect(tracker.getSnapshot().pending).toBe(0);

    const mockFetch = jest
      .fn()
      .mockResolvedValue(mockResponse({ status: 200, body: { ok: true } }));

    const client = createApiClient(apiBase, { fetch: mockFetch, tracker });
    await client('/tracked');

    expect(tracker.getSnapshot().pending).toBe(0);
  });
});
