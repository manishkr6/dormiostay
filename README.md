# DormioStay

PG, hostel, and student accommodation marketplace for ICFAI University Sikkim and Gangtok.

This is the DormioStay foundation. It contains the T-02 FastAPI runtime skeleton
and a minimal React page so both development servers and test runners can be
verified. Product features are built ticket by ticket, with review between tickets.

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

Visit <http://127.0.0.1:5173>. The page displays “DormioStay”. Stop either server
with Ctrl+C. T-01 does not require database, Redis, email, or AI credentials.

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
The current page is an unstyled toolchain placeholder. Design tokens, fonts,
UI components, routing, and the same-origin API proxy belong to T-04. Backend
configuration and health checks belong to T-02, models and migrations to T-03,
and Render deployment to T-05.
