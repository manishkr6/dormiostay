import { isDevelopment } from './config';

const baseRoutes = new Set([
  '/',
  '/property-preview',
  '/help',
  '/auth/login',
  '/auth/signup',
  '/tenant',
  '/tenant/saved',
  '/tenant/visits',
  '/tenant/notifications',
  '/tenant/profile',
  '/owner',
  '/owner/properties',
  '/owner/visits',
  '/owner/availability',
  '/owner/account',
]);

/**
 * Validates whether a given path is an approved target route.
 * Component Showcase (/showcase) is strictly guarded and allowed only in development.
 */
export function isAllowedRoute(route: string, isDev: boolean = isDevelopment()): boolean {
  if (route === '/showcase') {
    return isDev;
  }
  return baseRoutes.has(route);
}

/**
 * Sanitizes incoming deep link URLs or relative paths against an allowlist of approved routes.
 * Drops malicious schemes, credentials, query parameters, fragments, or unauthorized routes.
 * Rejects /showcase in production and preview environments.
 */
export function safeIncomingPath(path: string, isDev: boolean = isDevelopment()): string {
  try {
    // Release domain associations and auth action links are later tickets.
    if (path.startsWith('/') && !path.startsWith('//')) {
      return isAllowedRoute(path, isDev) ? path : '/';
    }
    const url = new URL(path);
    if (
      !['dormiostay:', 'dormiostay-development:', 'dormiostay-preview:'].includes(url.protocol) ||
      url.username ||
      url.password ||
      url.search ||
      url.hash
    ) {
      return '/';
    }
    const route = '/' + [url.hostname, ...url.pathname.split('/')].filter(Boolean).join('/');
    return isAllowedRoute(route, isDev) ? route : '/';
  } catch {
    return '/';
  }
}
