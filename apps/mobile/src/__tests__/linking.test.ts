import { safeIncomingPath, isAllowedRoute } from '../lib/linking';

describe('Deep Linking and Safe Path Redirection', () => {
  it('allows approved public routes', () => {
    expect(safeIncomingPath('/')).toBe('/');
    expect(safeIncomingPath('/property-preview')).toBe('/property-preview');
    expect(safeIncomingPath('/help')).toBe('/help');
  });

  it('allows approved auth shell routes', () => {
    expect(safeIncomingPath('/auth/login')).toBe('/auth/login');
    expect(safeIncomingPath('/auth/signup')).toBe('/auth/signup');
  });

  it('allows approved tenant shell routes', () => {
    expect(safeIncomingPath('/tenant')).toBe('/tenant');
    expect(safeIncomingPath('/tenant/saved')).toBe('/tenant/saved');
    expect(safeIncomingPath('/tenant/visits')).toBe('/tenant/visits');
    expect(safeIncomingPath('/tenant/notifications')).toBe('/tenant/notifications');
    expect(safeIncomingPath('/tenant/profile')).toBe('/tenant/profile');
  });

  it('allows approved owner shell routes', () => {
    expect(safeIncomingPath('/owner')).toBe('/owner');
    expect(safeIncomingPath('/owner/properties')).toBe('/owner/properties');
    expect(safeIncomingPath('/owner/visits')).toBe('/owner/visits');
    expect(safeIncomingPath('/owner/availability')).toBe('/owner/availability');
    expect(safeIncomingPath('/owner/account')).toBe('/owner/account');
  });

  it('allows custom scheme deep links matching valid routes', () => {
    expect(safeIncomingPath('dormiostay://property-preview')).toBe('/property-preview');
    expect(safeIncomingPath('dormiostay-development://help')).toBe('/help');
    expect(safeIncomingPath('dormiostay-preview://tenant')).toBe('/tenant');
  });

  it('allows allowlisted HTTPS deep links matching valid routes', () => {
    expect(safeIncomingPath('https://dormiostay.com/help')).toBe('/help');
    expect(safeIncomingPath('https://dormiostay.com/property-preview')).toBe('/property-preview');
    expect(safeIncomingPath('https://preview.dormiostay.com/tenant')).toBe('/tenant');
    expect(safeIncomingPath('https://staging.dormiostay.com/auth/login')).toBe('/auth/login');
  });

  it('safely falls back to root / for unknown or unapproved routes', () => {
    expect(safeIncomingPath('/admin')).toBe('/');
    expect(safeIncomingPath('/admin/moderation')).toBe('/');
    expect(safeIncomingPath('/unknown-path')).toBe('/');
    expect(safeIncomingPath('//evil.com')).toBe('/');
    expect(safeIncomingPath('https://dormiostay.com/admin')).toBe('/');
    expect(safeIncomingPath('https://dormiostay.com/admin/moderation')).toBe('/');
    expect(safeIncomingPath('https://dormiostay.com/unknown-route')).toBe('/');
  });

  it('safely falls back to root / for unauthorized hosts, schemes, credentials, or query injections', () => {
    expect(safeIncomingPath('https://evil.com/phishing')).toBe('/');
    expect(safeIncomingPath('https://evil.com/help')).toBe('/');
    expect(safeIncomingPath('https://not-dormiostay.com/help')).toBe('/');
    expect(safeIncomingPath('javascript:alert(1)')).toBe('/');
    expect(safeIncomingPath('dormiostay://user:pass@tenant')).toBe('/');
    expect(safeIncomingPath('dormiostay://tenant?arbitrary=1')).toBe('/');
    expect(safeIncomingPath('dormiostay://tenant#hash')).toBe('/');
    expect(safeIncomingPath('https://user:pass@dormiostay.com/help')).toBe('/');
    expect(safeIncomingPath('https://dormiostay.com/help?token=secret')).toBe('/');
    expect(safeIncomingPath('https://dormiostay.com/help#section')).toBe('/');
  });
});

describe('Component Showcase Route Guarding', () => {
  it('allows /showcase in development mode', () => {
    expect(isAllowedRoute('/showcase', true)).toBe(true);
    expect(safeIncomingPath('/showcase', true)).toBe('/showcase');
    expect(safeIncomingPath('dormiostay-development://showcase', true)).toBe('/showcase');
    expect(safeIncomingPath('https://dormiostay.com/showcase', true)).toBe('/showcase');
  });

  it('rejects /showcase in production and preview modes', () => {
    expect(isAllowedRoute('/showcase', false)).toBe(false);
    expect(safeIncomingPath('/showcase', false)).toBe('/');
    expect(safeIncomingPath('dormiostay://showcase', false)).toBe('/');
    expect(safeIncomingPath('dormiostay-development://showcase', false)).toBe('/');
    expect(safeIncomingPath('https://dormiostay.com/showcase', false)).toBe('/');
  });
});
