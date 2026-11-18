import { renderRoutes } from '../config/render-routes.ts';
const origin = process.argv[2];
if (!origin)
  throw new Error(
    'Usage: node --experimental-strip-types scripts/render-routes.ts https://YOUR-API.onrender.com',
  );
console.log(JSON.stringify(renderRoutes(origin), null, 2));
