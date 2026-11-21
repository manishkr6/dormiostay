export function createRequestTracker() {
  let snapshot = { pending: 0, slow: 0 };
  const listeners = new Set<() => void>();
  function update(pending: number, slow: number) {
    snapshot = { pending: snapshot.pending + pending, slow: snapshot.slow + slow };
    listeners.forEach((listener) => listener());
  }
  return {
    getSnapshot: () => snapshot,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    begin: () => {
      let slow = false;
      let finished = false;
      update(1, 0);
      const timer = setTimeout(() => {
        slow = true;
        update(0, 1);
      }, 4000);
      return () => {
        if (finished) return;
        finished = true;
        clearTimeout(timer);
        update(-1, slow ? -1 : 0);
      };
    },
  };
}
export const requestTracker = createRequestTracker();
