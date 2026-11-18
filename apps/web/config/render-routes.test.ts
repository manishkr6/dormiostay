import { expect, it } from 'vitest';
import { renderRoutes } from './render-routes';
it.each(['https://staging-api.example.invalid', 'https://production-api.example.invalid'])(
  'keeps API rewrite before SPA fallback for %s',
  (origin) => {
    expect(renderRoutes(origin)).toEqual([
      { type: 'rewrite', source: '/api/*', destination: `${origin}/api/*` },
      { type: 'rewrite', source: '/*', destination: '/index.html' },
    ]);
  },
);
it.each([
  'http://example.invalid',
  'https://user:password@example.invalid',
  'https://example.invalid/api',
  'https://example.invalid?secret=x',
])('rejects invalid backend origin %s', (origin) => {
  expect(() => renderRoutes(origin)).toThrow();
});
