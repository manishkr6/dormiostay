/** Ordered Render Static Site routes, consumed by the T-05 deployment setup. */
export function renderRoutes(apiOrigin: string) {
  const url = new URL(apiOrigin);
  if (
    url.protocol !== 'https:' ||
    url.username ||
    url.password ||
    url.pathname !== '/' ||
    url.search ||
    url.hash
  )
    throw new Error('Provide a public HTTPS API origin without credentials or a path.');
  return [
    { type: 'rewrite', source: '/api/*', destination: `${url.origin}/api/*` },
    { type: 'rewrite', source: '/*', destination: '/index.html' },
  ];
}
