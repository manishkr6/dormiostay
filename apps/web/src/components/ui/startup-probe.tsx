import { useEffect } from 'react';
import { apiClient } from '../../lib/api-client';

export function StartupProbe() {
  useEffect(() => {
    const controller = new AbortController();
    apiClient<{ status: string }>('/healthz', {
      signal: controller.signal,
      timeoutMs: 65_000,
    }).catch(() => {
      // Gracefully handle timeout, network failure, or abort without breaking UI
    });
    return () => {
      controller.abort();
    };
  }, []);

  return null;
}
