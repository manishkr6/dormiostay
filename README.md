# DormioStay

PG, hostel, and student accommodation marketplace for ICFAI University Sikkim and Gangtok.

This is the DormioStay foundation. It contains the T-02 FastAPI runtime skeleton
and the T-04 React frontend foundation with a component showcase, shared layouts,
and a same-origin API client. Product features are built ticket by ticket, with review between tickets.

## Requirements

- Node.js **24.21.0** (see `.nvmrc`) and npm **11.x**. If using nvm, run `nvm install`.
- Python **3.12** (see `apps/api/.python-version`).
- [uv](https://docs.astral.sh/uv/getting-started/installation/) **0.12.10** for Python
  environments and dependency installation. CI uses this version.
- Optional: Docker Engine/Desktop with Docker Compose v2, running locally.
- Network access to the npm and Python package registries for initial installation.

## Install from a fresh clone

Run from the repository root:

```sh
npm ci
cd apps/api
uv venv --python 3.12
uv pip install -r pyproject.toml --group dev
cd ../..
```

`uv venv --python 3.12` uses a locally installed Python 3.12 when available.
If none is installed, uv needs network access to download it. The dependency
installation also requires access to PyPI; creating the environment alone does
not install the backend or its test tools.

Direct Python and JavaScript dependencies are pinned to exact versions. The root
`package-lock.json` also locks the JavaScript dependency tree. Python transitive
versions are currently resolved during installation; Python transitive dependencies
are not yet locked because registry access is unavailable in this workspace.

The `minimatch` override pins the patched release for the lint dependency tree
([GHSA-g3hv-75wg-m249](https://github.com/advisories/GHSA-g3hv-75wg-m249)).

## Run locally

Terminal 1, from the repository root:

```sh
cd apps/api
uv run --no-sync uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Visit <http://127.0.0.1:8000/docs> or <http://127.0.0.1:8000/openapi.json>.
`/healthz` is the dependency-free liveness endpoint and `/readyz` checks the
configured database. The application routes are mounted below `/api/v1`.

Terminal 2, from the repository root:

```sh
npm run dev:web
```

Visit <http://127.0.0.1:5173/showcase> for the component library. Shared layout
previews are at `/auth`, `/tenant`, `/owner`, and `/admin`; they contain no protected
data or working product features. Stop either server with Ctrl+C. The showcase
works without database, Redis, email, or AI credentials.

## Environment configuration

`.env.example` lists **every variable from Technical Architecture section 6**,
with a comment and a blank value, grouped into backend and frontend sections.
T-02 validates `APP_ENV`, `DATABASE_URL`, and `FRONTEND_ORIGIN` at API startup;
missing required values stop startup with a clear validation error. Copy the
backend variables into `apps/api/.env` or export them in the API terminal, and
copy the frontend variables into `apps/web/.env.local`. Both locations are
ignored by Git. For local Compose, use the root `.env` as documented above.

Never commit credentials or reuse production secrets locally. Only
`VITE_SITE_URL`, `VITE_GEOAPIFY_MAP_KEY`, `VITE_SENTRY_DSN`, and
`VITE_CLOUDINARY_CLOUD_NAME` may be exposed to the browser. Keep geocoding keys
server-side and restrict the separate public map key by domain.

Choose either `REDIS_URL` or the Upstash REST URL/token pair consistently when
Redis integration is implemented. Database configuration uses a pooled URL for
the application and a direct URL for migrations, with separate roles.

## Checks

### T-03 database migrations and sessions

From `apps/api`, install the updated Python dependencies using the installation
steps above. Set `DATABASE_URL` to the pooled application connection and
`DATABASE_URL_DIRECT` to the direct migration-owner connection. Store credentials
in a gitignored environment file or your environment, never in alembic.ini.

For T-03 verification, Alembic requires `TEST_DATABASE_NAME` and a
`DATABASE_URL_DIRECT` pointing to that explicitly approved local disposable
database. Direct migration commands targeting the development database are
blocked. Authorizing deployment migrations is a separate future step.

Provision a separate application login with NOSUPERUSER, NOBYPASSRLS,
NOCREATEDB and NOCREATEROLE, without membership in privileged roles.
It must not own the schema or tables. As the migration owner, use
`psql "$DATABASE_URL_DIRECT" -v app_role=YOUR_APP_ROLE -f scripts/grant_application_role.sql`
to grant privileges. Supply a plain PostgreSQL URL to psql. The root Compose
bootstrap administrator is not the application's runtime role.

Use `get_session` as a FastAPI dependency, or `transaction` with an async session
factory. Identity comes from trusted authentication on request.state; absent
identity is a guest. The helper owns commit/rollback; do not commit early within it.
Do not put identity in connection-wide settings.

For integration verification, create a fresh separate database named
`dormiostay_t03_test_<unique_suffix>` and set `TEST_DATABASE_URL` to it, using
a test administrator allowed to create temporary test roles. Run
`uv run --no-sync pytest`. Tests refuse other database names and nonempty
databases, migrate up, run assertions and downgrade at teardown. They do not
fall back to DATABASE_URL or use the development database.
Set `TEST_DATABASE_NAME` to the exact disposable name and
`TEST_APP_DATABASE_URL` to the same database using the restricted
`dormiostay_app` login. The fixture checks the connected database name and
rejects URL query parameters that can override host, user or database.
Without TEST_DATABASE_URL, integration tests are reported as skipped.
Never run `alembic downgrade base` on development/production to verify migrations.

Category names/active flags, integer profile budget bounds, and text-array
preferred areas are the minimal representations of fields left unspecified in
the architecture. Future property and authentication tables are not created.

Local API credentials are in the ignored `apps/api/.env` (mode 0600):
DATABASE_URL uses dormiostay_app; DATABASE_URL_DIRECT uses the bootstrap
administrator. Startup/readiness and request sessions reject elevated runtime
roles, memberships, ownership and public-schema CREATE permission.
The development schema has not been migrated; application-role table privileges
were tested in the disposable database. Logging deliberately retains only
sanitized event codes, level, timestamp and validated request IDs; Sentry
discards exception payloads, request data, breadcrumbs and arbitrary context.

From the repository root, run the same checks as the web CI job:

```sh
npm run lint
npm run format:check
npm run typecheck
npm test
npm run build
```

For the API:

```sh
cd apps/api
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy
uv run --no-sync pytest
```

Use `npm run format` and `uv run --no-sync ruff format .` to format code.
`npm run test:watch --workspace @dormiostay/web` starts Vitest in watch mode.

GitHub Actions runs separate API and web jobs on every push and pull request.
Both jobs fail on lint, type, or test errors; the web job also checks formatting
and the production build. The supplied specifications in `docs/` are excluded
from automatic formatting to preserve their contents.

## Optional local Postgres and Redis

Neither service is needed for the empty apps. To start them later, first start
Docker Desktop/Engine. From the repository root, copy `.env.example` to the
gitignored `.env`, set a local-only password, then start Compose:

```sh
cp .env.example .env
openssl rand -hex 24
# Put the generated value in POSTGRES_PASSWORD in .env.
docker compose --project-name dormiostay up -d --wait
docker compose --project-name dormiostay ps
```

`POSTGRES_PASSWORD` is a Compose-only setup variable, separate from the
architecture's application variables. Preserve it in a secure local location
when reusing the database volume; changing the environment value does not change
the password inside an existing database.

Postgres uses database `dormiostay` and bootstrap user `dormiostay_local_admin`
on `127.0.0.1:5433`. This administrative user is for local setup only; the future
application role must be a separate non-superuser without RLS bypass. Redis is
available on `127.0.0.1:6380`. Both services use the dedicated `dormiostay`
network and dedicated persistent volumes. Ports are bound to loopback only.

```sh
docker compose --project-name dormiostay down
```

This stops the services and preserves both named volumes. Do not add `-v` unless
you separately intend to delete the local database and Redis data.

## Repository map and project rules

```text
.github/workflows/ci.yml   # API and web checks
apps/api/                 # Python tooling, minimal ASGI entry point, tests/unit
apps/web/                 # React/Vite entry point, strict TypeScript, component test
.env.example              # Documented variable names only
docker-compose.yml       # Optional local infrastructure
docs/                    # Source specifications
```

Follow the Global Context block in
[Feature Ticket List](docs/Feature%20Ticket%20List.md) for every ticket. Specifics live in:

- [Technical Architecture](docs/Technical%20Architecture%20Document.md)
- [Frontend Specification](docs/Frontend%20Specification%20Document.md)
- [Security and Access](docs/Security%20and%20Access%20Document.md)

Add architecture folders as their tickets need them: backend routes in `app/api/v1`,
business logic in `app/services`, SQL in `app/repositories`; frontend routes and
providers in `src/app`, layouts in `src/layouts`, shared UI in `src/components/ui`.
T-04 supplies design tokens, fonts, the base UI kit, nested layout previews,
page metadata, and the same-origin API client. Backend configuration and health
checks belong to T-02, models and migrations to T-03, and Render deployment to T-05.

## T-04 frontend foundation

`src/styles/tokens.css` is the colour, spacing, radius and shadow source of truth.
Tailwind v4 loads `tailwind.config.ts` explicitly through `@config`, mapping the
approved DormioStay tokens. Shared components live in `src/components/ui`;
Radix primitives handle dialogs, tabs, switches and toast accessibility. Native
labelled controls handle basic form input. Playfair Display and Inter load through
Google Fonts with `display=swap` and local fallback stacks.

The showcase covers all six button variants and all six states, form controls,
badges, cards, modal/drawer/bottom sheet, tabs, toasts, skeleton/empty/error states,
and the wake-up banner. The small admin button keeps a 44px hit target to satisfy
the ticket's accessibility criterion. Dark mode and product screens are deferred.

Use `apiClient<ResponseType>('/resource', options)` from `src/lib/api-client.ts`.
It prefixes `/api/v1`, includes session cookies, rejects external/traversal paths,
and parses standard errors into `ApiError` (`status`, `code`, `fields`, `requestId`,
`retryAfter`). Writes fetch `{token: string}` from `/api/v1/auth/csrf` and send
`X-CSRF-Token`; T-07 must supply that endpoint. Tokens are never stored in browser
storage or cached across sessions. Requests timeout after 65 seconds by default,
accept cancellation, and trigger the global wake-up banner after four seconds.
There are no automatic write retries. TanStack Query also defaults to no retries.

Vite proxies `/api` to `http://localhost:8000` without stripping `/api/v1` or
changing the browser's Origin header. `PageHead` owns title, description, Open
Graph tags and escaped JSON-LD with cleanup on route changes. Previews are noindex.
Client-side metadata is not a substitute for the later server share-preview ticket.

### Render deployment and environment configuration (T-05)

`render.yaml` defines the infrastructure blueprint for deploying DormioStay on Render:

- **Web Frontend (`dormiostay-web`)**: Render Static Site built with `npm ci && npm run build` and published from `./apps/web/dist`. Configured with ordered rewrite rules routing `/api/*` to the FastAPI backend service and `/*` to `/index.html` for React Router SPA navigation.
- **FastAPI Backend (`dormiostay-api`)**: Python 3.12 web service rooted in `apps/api/`, installing pinned dependencies from `requirements.txt` and starting ASGI server via `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. Liveness probe is mounted at `/healthz` and readiness probe at `/readyz`. Sensitive credentials (`DATABASE_URL`, `FRONTEND_ORIGIN`, `SENTRY_DSN`) are defined with `sync: false` for secure manual dashboard entry.
- **Email Worker**: Kept unprovisioned/commented out until T-09 implements `scripts/worker.py`.

For comprehensive details on environment mapping, Neon PostgreSQL database branching, Upstash Redis configuration, sleeping API cold-start behavior, always-on upgrade steps, and mobile environment profiles, refer to [Deployment and Environments Guide](docs/deployment-and-environments.md).
