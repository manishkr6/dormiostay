# DormioStay

PG, hostel, and student accommodation marketplace for ICFAI University Sikkim and Gangtok.

This is the **T-01 tooling foundation**. It contains an empty FastAPI app and a minimal
React page so both development servers and test runners can be verified. Product
features are built ticket by ticket, with review between tickets.

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
The schema has no application routes yet. `/` returns FastAPI's default 404.

Terminal 2, from the repository root:

```sh
npm run dev:web
```

Visit <http://127.0.0.1:5173>. The page displays “DormioStay”. Stop either server
with Ctrl+C. T-01 does not require database, Redis, email, or AI credentials.

## Environment configuration

`.env.example` lists **every variable from Technical Architecture section 6**,
with a comment and a blank value, grouped into backend and frontend sections.
The bootstrap does not load backend configuration yet; that belongs to T-02.
When configuring later tickets, copy only the relevant section into
`apps/api/.env` or `apps/web/.env.local`. Both locations are ignored by Git.

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
Docker Desktop/Engine. From the repository root, generate a local-only database
password in your shell, then start Compose:

```sh
export POSTGRES_PASSWORD="$(openssl rand -hex 24)"
docker compose up -d --wait
docker compose ps
```

`POSTGRES_PASSWORD` is a Compose-only setup variable, separate from the
architecture's application variables. Preserve it in a secure local location
when reusing the database volume; changing the environment value does not change
the password inside an existing database.

Postgres uses database `dormiostay` and bootstrap user `dormiostay_local_admin`
on `127.0.0.1:5432`. This administrative user is for local setup only; the future
application role must be a separate non-superuser without RLS bypass. Redis is
available on `127.0.0.1:6379`. Ports are bound to loopback only.

```sh
docker compose down
```

This stops the services and preserves the named Postgres volume.

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
