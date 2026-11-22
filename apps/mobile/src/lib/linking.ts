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

const ALLOWED_HTTPS_HOSTS = new Set([
  'dormiostay.com',
  'www.dormiostay.com',
  'staging.dormiostay.com',
  'preview.dormiostay.com',
  'development.dormiostay.com',
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
 * Drops malicious schemes, credentials, query parameters, fragments, unauthorized hosts, or unauthorized routes.
 * Rejects /showcase in production and preview environments.
 * Supports allowlisted HTTPS domains (dormiostay.com, *.dormiostay.com) and custom schemes.
 */
export function safeIncomingPath(path: string, isDev: boolean = isDevelopment()): string {
  try {
    if (path.startsWith('/') && !path.startsWith('//')) {
      return isAllowedRoute(path, isDev) ? path : '/';
    }
    const url = new URL(path);
    if (url.username || url.password || url.search || url.hash) {
      return '/';
    }

    if (url.protocol === 'https:') {
      if (!ALLOWED_HTTPS_HOSTS.has(url.hostname)) {
        return '/';
      }
      const route = url.pathname.replace(/\/+$/, '') || '/';
      return isAllowedRoute(route, isDev) ? route : '/';
    }

    if (!['dormiostay:', 'dormiostay-development:', 'dormiostay-preview:'].includes(url.protocol)) {
      return '/';
    }
    const route = '/' + [url.hostname, ...url.pathname.split('/')].filter(Boolean).join('/');
    return isAllowedRoute(route, isDev) ? route : '/';
  } catch {
    return '/';
  }
}
