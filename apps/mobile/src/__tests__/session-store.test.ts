import * as SecureStore from 'expo-secure-store';
import { createSessionStore, SessionStorageError } from '../lib/session-store';

jest.mock('expo-secure-store', () => ({
  isAvailableAsync: jest.fn(() => Promise.resolve(true)),
  getItemAsync: jest.fn(),
  setItemAsync: jest.fn(),
  deleteItemAsync: jest.fn(),
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: 'when_unlocked_this_device_only',
}));

describe('Session Store Abstraction', () => {
  const apiBase = 'https://api.dormiostay.com/api/v1';
  let store: ReturnType<typeof createSessionStore>;

  beforeEach(() => {
    jest.clearAllMocks();
    store = createSessionStore(apiBase);
  });

  it('stores a valid opaque session token in SecureStore with hardware-bound options', async () => {
    const validToken = 'a'.repeat(32);
    await store.set(validToken);

    expect(SecureStore.setItemAsync).toHaveBeenCalledTimes(1);
    expect(SecureStore.setItemAsync).toHaveBeenCalledWith(
      expect.stringContaining('dormiostay.session.'),
      validToken,
      { keychainAccessible: 'when_unlocked_this_device_only' },
    );
  });

  it('retrieves an existing session token from SecureStore', async () => {
    const validToken = 'b'.repeat(32);
    (SecureStore.getItemAsync as jest.Mock).mockResolvedValueOnce(validToken);

    const result = await store.get();
    expect(result).toBe(validToken);
    expect(SecureStore.getItemAsync).toHaveBeenCalledTimes(1);
  });

  it('deletes a session token from SecureStore', async () => {
    await store.delete();
    expect(SecureStore.deleteItemAsync).toHaveBeenCalledTimes(1);
  });

  it('rejects empty or whitespace-only tokens with SessionStorageError', async () => {
    await expect(store.set('')).rejects.toThrow(SessionStorageError);
    await expect(store.set('   ')).rejects.toThrow(SessionStorageError);
  });

  it('rejects tokens containing control characters with SessionStorageError', async () => {
    await expect(store.set('token\x00corrupted')).rejects.toThrow(SessionStorageError);
    await expect(store.set('token\nwithnewline')).rejects.toThrow(SessionStorageError);
  });

  it('throws SessionStorageError if SecureStore is unavailable on the device', async () => {
    (SecureStore.isAvailableAsync as jest.Mock).mockResolvedValueOnce(false);
    await expect(store.get()).rejects.toThrow(SessionStorageError);
  });

  it('throws SessionStorageError if underlying native storage throws', async () => {
    (SecureStore.setItemAsync as jest.Mock).mockRejectedValueOnce(
      new Error('Keychain access denied'),
    );
    await expect(store.set('valid-opaque-token-123456789012345')).rejects.toThrow(
      SessionStorageError,
    );
  });
});
