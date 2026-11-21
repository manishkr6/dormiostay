import { safeIncomingPath } from '../src/lib/linking';
export function redirectSystemPath({ path }: { path: string; initial: boolean }) {
  return safeIncomingPath(path);
}
