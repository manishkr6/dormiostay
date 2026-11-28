import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
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

describe('Render Blueprint Configuration (render.yaml)', () => {
  const rootPath = path.resolve(__dirname, '../../../render.yaml');
  const content = fs.readFileSync(rootPath, 'utf8');

  it('defines the web static site with branch, publish path and build command', () => {
    expect(content).toContain('name: dormiostay-web');
    expect(content).toContain('runtime: static');
    expect(content).toContain('branch: main');
    expect(content).toContain('buildCommand: npm ci && npm run build');
    expect(content).toContain('staticPublishPath: ./apps/web/dist');
  });

  it('preserves the SPA fallback route while keeping unverified API proxy rewrite disabled', () => {
    // SPA catch-all fallback must be actively configured
    expect(content).toMatch(/source:\s*\/\*\s*\n\s*destination:\s*\/index\.html/);

    // Active (uncommented) /api/* proxy rewrite must NOT be present until verified
    expect(content).not.toMatch(/^(\s*)-\s+type:\s*rewrite\s*\n\s+source:\s*\/api\/\*/m);

    // Explicit verification instructions must be present in comments
    expect(content).toContain(
      'SECURITY NOTICE: Disabled until the staging API service is provisioned',
    );
    expect(content).toContain('DO NOT activate with unverified placeholders');
  });

  it('defines the FastAPI web service with plan: free, branch: main, and APP_ENV: staging', () => {
    expect(content).toContain('name: dormiostay-api');
    expect(content).toContain('plan: free');
    expect(content).toContain('branch: main');
    expect(content).toContain('runtime: python');
    expect(content).toContain('rootDir: apps/api');
    expect(content).toContain('healthCheckPath: /healthz');
    expect(content).toContain('uvicorn app.main:app --host 0.0.0.0 --port $PORT');
    expect(content).toMatch(/key: AUTH_ENDPOINTS_ENABLED\s+value: 'false'/);
    expect(content).not.toMatch(/^\s*- key: (DATABASE_URL_DIRECT|NEON_API_KEY)\s*$/m);
    expect(content).toMatch(/key: APP_ENV\s+value: staging/);
    expect(content).toMatch(/key: DATABASE_URL\s+sync: false/);
    expect(content).toMatch(/key: FRONTEND_ORIGIN\s+sync: false/);
    expect(content).toMatch(/key: SENTRY_DSN\s+sync: false/);
  });

  it('keeps worker cron unprovisioned until T-09 delivery', () => {
    // Cron worker should not be an active (uncommented) service
    expect(content).not.toMatch(/^-\s+type:\s*cron/m);
  });
});
