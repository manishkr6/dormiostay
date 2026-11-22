import { act, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { StartupProbe } from './startup-probe';
import { WakingBanner } from './waking-banner';
import { requestActivity } from '../../lib/api-client';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

it('fires startup health check on mount and keeps UI non-blocking', async () => {
  const fetcher = vi.fn().mockResolvedValue(json({ status: 'ok' }));
  vi.stubGlobal('fetch', fetcher);

  render(
    <div>
      <StartupProbe />
      <WakingBanner />
      <main>Public Content</main>
    </div>,
  );

  expect(screen.getByText('Public Content')).toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledWith(
    '/api/v1/healthz',
    expect.objectContaining({
      method: 'GET',
      credentials: 'include',
    }),
  );
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it('displays waking banner after 4s delay and clears on response', async () => {
  vi.useFakeTimers();
  let resolveProbe!: (value: Response) => void;
  const probePromise = new Promise<Response>((resolve) => {
    resolveProbe = resolve;
  });
  const fetcher = vi.fn().mockReturnValue(probePromise);
  vi.stubGlobal('fetch', fetcher);

  render(
    <div>
      <StartupProbe />
      <WakingBanner />
    </div>,
  );

  // Before 4 seconds, banner is not visible
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3999);
  });
  expect(screen.queryByRole('status')).not.toBeInTheDocument();

  // At 4 seconds, waking banner appears
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2);
  });
  expect(screen.getByRole('status')).toHaveTextContent(
    'Waking things up… this can take up to a minute.',
  );

  // When API wakes up and responds, banner clears
  await act(async () => {
    resolveProbe(json({ status: 'ok' }));
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
  expect(requestActivity.getSnapshot()).toBe(false);
});

it('gracefully handles startup probe errors without crashing or looping', async () => {
  vi.useFakeTimers();
  const fetcher = vi.fn().mockRejectedValue(new Error('Network offline'));
  vi.stubGlobal('fetch', fetcher);

  render(
    <div>
      <StartupProbe />
      <WakingBanner />
      <span>UI Resilient</span>
    </div>,
  );

  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });

  expect(screen.getByText('UI Resilient')).toBeInTheDocument();
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
  expect(requestActivity.getSnapshot()).toBe(false);
  // Ensured probe fired only once (no infinite loop)
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it('aborts pending startup probe on component unmount', async () => {
  let abortSignal: AbortSignal | undefined;
  const fetcher = vi.fn((_url: string, options: RequestInit) => {
    abortSignal = options.signal as AbortSignal;
    return new Promise<Response>(() => {});
  });
  vi.stubGlobal('fetch', fetcher);

  const { unmount } = render(<StartupProbe />);
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(abortSignal?.aborted).toBe(false);

  unmount();
  expect(abortSignal?.aborted).toBe(true);
});
