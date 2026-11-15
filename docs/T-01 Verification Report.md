# T-01 verification report

Last rechecked: 2026-11-14, approximately 18:55 IST. Scope: T-01 only.
**Status: incomplete; environment-blocked.**

This report reflects the latest retry in the permitted local macOS execution
environment. Docker's socket now exists, but this session cannot access it.
The earlier observation that the socket was absent no longer applies.

## Acceptance criteria

| Criterion | Status | Evidence |
| --- | --- | --- |
| Fresh clone plus README steps runs both apps locally | Environment-blocked | Clean frontend installation passes in a temporary copy. Vite cannot bind `127.0.0.1:5173` (`EPERM`). Backend environment creation succeeds, but PyPI DNS failure prevents dependency installation and API startup. This workspace has no Git repository, so an actual clone was not possible. |
| CI passes on an empty test for each app | Environment-blocked | All frontend CI commands pass, including one Vitest test. Backend tools cannot run because dependencies could not be installed. The workflow defines both jobs on push and pull request, but no hosted GitHub Actions execution was available. |
| No real secret exists in the repo; `.env.example` lists every variable with a comment | Passed for inspected source | All 37 architecture variables are unique, blank, and individually commented. Exactly the four permitted `VITE_` names are present. Maintained source inspection and credential-pattern checks found no secrets or local environment value files. No Git history exists here to inspect. |

## Checks performed

| Check | Status | Result |
| --- | --- | --- |
| Installed Python | Passed | `/opt/homebrew/bin/python3.12`, Python 3.12.16. |
| Backend virtual environment | Passed | Existing `apps/api/.venv` uses the local Python 3.12 interpreter; preserved without recreation. |
| Backend dependency installation | Environment-blocked | Retried `uv pip install -r pyproject.toml --group dev` with a writable cache in `/tmp`; exits 2 after retries because `pypi.org` cannot resolve while fetching Ruff. Ruff, mypy, and pytest are also absent from the checked local interpreter and uv wheel cache, so no offline installation was available. |
| Ruff lint and format checks | Environment-blocked | Both commands attempted; Ruff executable is absent because installation failed. |
| mypy | Environment-blocked | Command attempted; mypy executable is absent because installation failed. |
| pytest | Environment-blocked | Command attempted; pytest executable is absent. No backend tests executed. |
| API startup | Environment-blocked | Command attempted; Uvicorn executable is absent. |
| Clean frontend installation | Passed | `npm ci --offline --cache /tmp/dormiostay-npm-cache` succeeds in a temporary copy without existing `node_modules`. This verifies the lockfile using cached packages, not registry connectivity. |
| ESLint | Passed | `npm run lint`, zero warnings/errors. |
| Prettier | Passed | `npm run format:check`. |
| Strict TypeScript | Passed | `npm run typecheck`. |
| Vitest | Passed | `npm test`: 1 test in 1 file passes. |
| Production web build | Passed | `npm run build`. |
| Vite development server | Environment-blocked | `npm run dev:web` exits 1: sandbox denies binding the configured loopback port (`listen EPERM`). No HTTP/browser startup verification succeeded. |
| Docker CLI / Compose | Passed | Docker client 29.7.2 and Compose v5.5.0 are installed. |
| Compose configuration | Passed | `docker compose config --quiet` succeeds with a disposable validation-only password supplied through the environment. |
| Docker Desktop | Environment-blocked | `/Applications/Docker.app` is present. `docker desktop status` cannot read status and reports denied access to its host log directory; it does not establish whether the daemon is running. |
| Docker runtime | Environment-blocked | The socket at `/Users/manish/.docker/run/docker.sock` now exists. `docker info` and `docker compose ps` fail with permission denied. Postgres/Redis startup, container status, and health checks remain unverified. Docker is optional for T-01. |

No project lint, type, or assertion failures were observed. Backend checks remain
unexecuted, so their correctness is not established by this report. Environment
failures are not counted as passing checks.

## Changes made during the latest verification

- Updated only this report among maintained project files. Application source,
  dependency manifests, README, and CI configuration were unchanged.
- Reused the existing backend environment; did not recreate the project.
- Reran all frontend CI commands and the environment-template/source checks.
  A fresh `npm ci --offline` passed in a temporary copy at
  `/tmp/dormiostay-t01-recheck-p3cg06nr`, preserving the workspace installation.

No backend lint or test result is claimed: each command was attempted again and
failed to launch its missing executable. The PyPI, Vite, and Docker permission
errors are execution-environment limitations, not demonstrated project defects.

## Remaining verification

1. With PyPI access available, install backend dependencies and rerun Ruff,
   mypy, and pytest using the README commands; fix any failures revealed.
2. In an environment permitting local listening ports, start both apps using
   the README and verify the web page and API OpenAPI endpoint over HTTP.
3. Run the GitHub Actions workflow once a Git repository and remote are available.
4. For optional infrastructure verification, use a context permitted to access
   Docker's socket, establish Docker Desktop/Engine status, and check both
   Compose services become healthy using a local-only password.

The present execution context cannot resolve PyPI, bind the configured Vite
loopback port, or access Docker's socket. These checks require a permitted
execution environment with those capabilities; changing application code would
not resolve the observed restrictions. No sandbox restrictions or host socket
permissions were changed.

T-01 is not complete. T-02 and later tickets have not been started.
