import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';

export class SessionStorageError extends Error {
  constructor() {
    super('Secure session storage is unavailable.');
    this.name = 'SessionStorageError';
  }
}
// Per-API namespace prevents credentials crossing development/staging/production.
export function createSessionStore(apiBase: string) {
  const key = `dormiostay.session.${Array.from(apiBase)
    .map((c) => c.charCodeAt(0).toString(16))
    .join('')}`;
  const options = { keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY };
  async function guarded<T>(operation: () => Promise<T>): Promise<T> {
    try {
      if (Platform.OS === 'web' || !(await SecureStore.isAvailableAsync()))
        throw new SessionStorageError();
      return await operation();
    } catch {
      throw new SessionStorageError();
    }
  }
  return {
    get: () => guarded(() => SecureStore.getItemAsync(key, options)),
    set: (token: string) =>
      guarded(async () => {
        // eslint-disable-next-line no-control-regex
        if (!token || /[\s\x00-\x1f\x7f]/.test(token)) throw new SessionStorageError();
        await SecureStore.setItemAsync(key, token, options);
      }),
    delete: () => guarded(() => SecureStore.deleteItemAsync(key, options)),
  };
}
// No store is instantiated or connected to the app in T-04M. T-06/T-07 own auth.
