import { fetch } from 'expo/fetch';
import { createApiClient } from './api-client';
import { configuredApiBase } from './config';
// Deliberately no SecureStore/token provider: T-04M has no active authentication.
// Expo fetch implements native redirect rejection and cookie omission.
export const apiClient = createApiClient(configuredApiBase(), {
  fetch: (url, init) => fetch(url, init) as unknown as Promise<Response>,
});
