# T-01 verification report

Last rechecked: 2026-11-17. Scope: T-01 only.
**Status: locally verified; hosted CI remains unexecuted.**

No T-02 or later ticket was started.

## Acceptance criteria

| Criterion | Status | Evidence |
| --- | --- | --- |
| Fresh clone plus README steps runs both apps locally | Passed locally | Backend dependencies installed from the pinned `pyproject.toml`; FastAPI started and `GET /openapi.json` returned 200. Vite started on `127.0.0.1:5173` with elevated local bind permission and returned the app HTML. |
| CI passes on an empty test for each app | Passed locally; hosted run pending | Web lint, Prettier, strict TypeScript, Vitest (1 test), and production build passed. API Ruff check, Ruff format check, mypy, and pytest (1 test) passed. The GitHub Actions workflow was not executed on a hosted runner in this session. |
| No real secret exists in the repo; `.env.example` lists every variable with a comment | Passed for maintained source | `.env.example` contains blank architecture variables plus documented local Compose names. `.env` contains only a generated local development password and is ignored by `.gitignore`; it is not tracked. Source credential-pattern inspection found no committed credentials. |

## Docker cleanup and fresh environment

The user explicitly approved deletion of these resources, and only these approved
resources were removed:

- Containers: `campusquest-postgres`, `campusquest-redis`, `campusquest-pg`, and `vibrant_spence`.
- Network: `campusquest_default`.
- Temporary BuildKit cache: 232.2 MB.

All six pre-existing Docker volumes were preserved, including
`campusquest_pgdata`; no persistent data was deleted. The existing images
`postgres:16-alpine` and `redis:7-alpine` were preserved and reused.

The new Compose project is explicitly named `dormiostay` and defines:

- PostgreSQL `16-alpine`, loopback host port `5433`, health check, restart policy,
  and dedicated volume `dormiostay_postgres_data`.
- Redis `7-alpine`, loopback host port `6380`, append-only persistence, health
  check, restart policy, and dedicated volume `dormiostay_redis_data`.
- Dedicated bridge network `dormiostay`.

Docker verification passed:

- `docker compose --project-name dormiostay config --quiet`.
- Both services reported `healthy` from `docker compose up -d --wait`.
- `pg_isready` reported accepting connections inside PostgreSQL.
- `redis-cli ping` returned `PONG` inside Redis.
- Host TCP probes succeeded for `127.0.0.1:5433` and `127.0.0.1:6380`.
- Network inspection showed only the two DormioStay services attached to the
  dedicated network.
- `docker compose ps` showed both services up and healthy.

## Checks performed

| Check | Result |
| --- | --- |
| `npm run lint` | Passed; zero warnings/errors |
| `npm run format:check` | Passed |
| `npm run typecheck` | Passed |
| `npm test` | Passed; 1 test in 1 file |
| `npm run build` | Passed |
| `uv run --no-sync ruff check .` | Passed |
| `uv run --no-sync ruff format --check .` | Passed; 3 files already formatted |
| `uv run --no-sync mypy` | Passed; no issues in 3 source files |
| `uv run --no-sync pytest` | Passed; 1 test, 1 warning from the pinned Starlette/httpx compatibility layer |
| FastAPI startup and `/openapi.json` | Passed; HTTP 200 |
| Vite startup and root page fetch | Passed with elevated local bind permission |
| Compose config, startup, health, and host access | Passed |

## Commands executed

```sh
docker rm -f campusquest-postgres campusquest-redis campusquest-pg vibrant_spence
docker network rm campusquest_default
docker builder prune -af

docker compose --project-name dormiostay config --quiet
docker compose --project-name dormiostay up -d --wait
docker compose --project-name dormiostay ps
docker exec dormiostay-postgres-1 pg_isready -U dormiostay_local_admin -d dormiostay
docker exec dormiostay-redis-1 redis-cli ping
nc -zv 127.0.0.1 5433
nc -zv 127.0.0.1 6380

npm run lint
npm run format:check
npm run typecheck
npm test
npm run build

cd apps/api
uv pip install -r pyproject.toml --group dev
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy
uv run --no-sync pytest
uv run --no-sync uvicorn app.main:app --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/openapi.json
```

The Docker commands above required elevated access in this agent sandbox because
the Docker Desktop socket is not readable by the sandbox. Vite and FastAPI HTTP
checks also required elevated local bind permission. The same commands can be
run from a normal macOS zsh terminal with Docker Desktop running.

## Remaining issue

The GitHub Actions workflow was not run on a hosted runner, so hosted CI status
is not claimed. All available local acceptance checks passed. T-01 is ready for
user review; no further ticket work was started.
