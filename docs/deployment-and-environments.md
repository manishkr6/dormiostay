# DormioStay — Deployment and Environments Guide

**Document Version:** 1.2.0
**Associated Ticket:** T-05 (Render Deployment & Environments)
**Target Environments:** Development, Staging, Production
**Platforms:** Web Frontend (Render Static Site), API Backend (Render Python Web Service), Native Mobile (EAS Build / Local Toolchain)

---

## 1. System Architecture & Hosting Overview

DormioStay follows a cloud-native, decoupled monorepo architecture designed for cost efficiency, strong security isolation, and rapid deployment.

```text
[ Web Client ] --------> Render Static Site (dormiostay-web)
                           │  - Source: apps/web, Branch: main
                           │  - Rewrites: /* -> /index.html (React Router SPA fallback)
                           │  - Proxy: /api/* -> DISABLED until staging API destination is verified
                           ▼
[ Mobile Client ] ------> Hosted FastAPI Web Service (dormiostay-api)
                           │  - Python 3.12, Uvicorn ASGI, Plan: free, Branch: main
                           │  - Liveness: /healthz | Web Probe: /api/v1/healthz | Readiness: /readyz
                           ├──> Neon PostgreSQL (Serverless / Branching / PgBouncer)
                           ├──> Upstash Redis (Serverless / Rate Limiting / Cache)
                           ├──> Cloudflare R2 (Private Verification Documents)
                           ├──> Cloudinary (Public Listing Photos)
                           └──> Brevo (Transactional Email Outbox)
```

| Layer | Hosting Provider | Deployment Unit | Monorepo Source |
|---|---|---|---|
| **Web Frontend** | Render Static Site | Global CDN Static Site | `apps/web` (build: `npm ci && npm run build`, publish: `apps/web/dist`, branch: `main`) |
| **API Backend** | Render Web Service | Python 3.12 Web Service (`plan: free`) | `apps/api` (`pip install -r requirements.txt`, `uvicorn app.main:app`, branch: `main`) |
| **Email Worker** | Render Cron Job *(Deferred)* | Python Scheduled Job | `apps/api` (`scripts/worker.py` scheduled after T-09 delivery) |
| **Database** | Neon Serverless PostgreSQL | Managed PostgreSQL 17 | Connection pooled for runtime, direct for migrations |
| **Cache & Limiting**| Upstash Redis | Serverless Redis | REST API / Redis protocol |
| **Native Mobile** | EAS Build / Local CLI | Android AAB / iOS IPA | `apps/mobile` (Expo SDK 57 / React Native 0.86.3) |

> [!CAUTION]
> **Security Notice on API Proxy Rewrite:**
> The active `/api/*` reverse proxy rewrite in `render.yaml` is **disabled (commented out)** until the staging API service is provisioned and its actual Render-assigned URL is verified. Guessing or assuming hostnames creates a credential leakage risk. The Web-to-API proxy journey remains **BLOCKED** pending live destination verification.

---

## 2. Environment Matrix & Isolation Strategy

DormioStay strictly isolates development, staging, and production environments across client applications, compute services, and data layers.

| Dimension | Development | Staging (T-05 Baseline) | Production (Future Launch) |
|---|---|---|---|
| **Web Frontend URL** | `http://localhost:5173` | `https://dormiostay-web-staging.onrender.com` *(unverified placeholder)* | `https://dormiostay.com` |
| **Backend API URL** | `http://localhost:8000` | `https://dormiostay-api-staging.onrender.com` *(unverified placeholder)* | `https://api.dormiostay.com` |
| **Web Proxy / Routing** | Vite dev server proxy (`/api` -> `localhost:8000`) | Render Static Site rewrite (DISABLED until destination verification; SPA fallback active) | Render Static Site rewrite / Custom domain routing |
| **Render Service Plan** | N/A (Local) | `plan: free` (stateless compute) | `plan: starter` ($7/mo Always-On) |
| **Render Branch** | Local working branch | `main` | `main` |
| **`APP_ENV` Value** | `development` | `staging` | `production` |
| **Database Target** | Local PostgreSQL (Docker `5433`) or disposable test DB | Neon `staging` branch (isolated test data) | Neon `production` primary branch |
| **Database Pooling** | Direct connection or local pool | Neon Pooled Connection (`-pooler`) | Neon Pooled Connection (`-pooler`) |
| **Database Migrations** | Guarded disposable test DB (`TEST_DATABASE_NAME`) | ALL remote migrations DISABLED (pending verified infrastructure) | ALL remote migrations DISABLED in T-05 |
| **Redis Cache Target** | Local Redis (Docker `6380`) or Upstash dev DB | Dedicated Upstash staging DB | Dedicated Upstash production DB |
| **Mobile App Variant** | `development` (`app.dormiostay.mobile.development`) | `preview` (`app.dormiostay.mobile.preview`) | `production` (`app.dormiostay.mobile`) |
| **Mobile API Target** | Local HTTPS tunnel / dev server | `https://staging-api.dormiostay.com/api/v1` *(or staging API)* | `https://api.dormiostay.com/api/v1` |
| **Document Storage** | Local stub / dev R2 bucket | Staging R2 bucket (`dormiostay-docs-staging`) | Production R2 bucket (`dormiostay-docs-prod`) |
| **Email Delivery** | Development mock (console status) | Staging Brevo sender (test accounts only) | Verified domain production sender |
| **Telemetry (Sentry)** | Disabled / local mock | Staging Sentry DSN (redacted PII) | Production Sentry DSN (redacted PII) |

---

## 3. Render Infrastructure as Code (`render.yaml`)

DormioStay's Render infrastructure is managed via `render.yaml` at the repository root.

### 3.1 Web Application (`dormiostay-web`)
- **Runtime:** `static`
- **Branch:** `main`
- **Build Command:** `npm ci && npm run build`
- **Publish Directory:** `./apps/web/dist`
- **Routing Rules:**
  1. `/api/*`: **DISABLED** in `render.yaml` until destination verification.
  2. `/*` -> `/index.html` (Type: `rewrite`): Single Page Application (SPA) client-side routing fallback.
- **Security Headers:**
  - `X-Frame-Options: DENY` (prevents clickjacking)
  - `X-Content-Type-Options: nosniff` (prevents MIME sniffing)
  - `Referrer-Policy: strict-origin-when-cross-origin`
  - `Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()`

### 3.2 FastAPI Backend (`dormiostay-api`)
- **Runtime:** `python` (Python 3.12.9 pinned)
- **Plan:** `free` (explicitly declared; 512 MB RAM, 0.1 CPU, spun down after 15m inactivity)
- **Branch:** `main`
- **Root Directory:** `apps/api`
- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- **Health Check Path:** `/healthz` (liveness check with zero database dependency)
- **Readiness Check Path:** `/readyz` (verifies database connectivity)
- **Environment Variables:**
  - `PYTHON_VERSION`: `3.12.9`
  - `APP_ENV`: `staging`
  - `DATABASE_URL`: `sync: false` (pooled runtime connection configured via dashboard)
  - `FRONTEND_ORIGIN`: `sync: false` (exact allowed CORS origin)
  - `SENTRY_DSN`: `sync: false` (backend telemetry)
  - `REQUEST_ID_HEADER`: `X-Request-ID`
  - `DATABASE_CONNECT_TIMEOUT_SECONDS`: `5.0`
- **Disk:** Stateless (no persistent disk attachment; all uploads use presigned Cloudinary/R2 links)

### 3.3 Email Worker (Cron Job — T-09 Dependency)
- **Status:** Unprovisioned / commented out in `render.yaml`.
- **Reason:** T-09 owns `scripts/worker.py`. Scheduling a nonexistent worker would cause build failures and unnecessary compute billing. The cron job (`* * * * *`) will be activated upon T-09 implementation.

---

## 4. Web API Routing & Reverse Proxy Verification Procedure

DormioStay's security specification mandates same-origin cookie authentication for the Web client.

### 4.1 The `onrender.com` Public Suffix List (PSL) Boundary
Under Render's default domain structure, services receive subdomains such as:
- Frontend: `https://dormiostay-web-staging.onrender.com`
- Backend: `https://dormiostay-api-staging.onrender.com`

**Critical Browser Constraint:** `onrender.com` is listed in the Mozilla Public Suffix List (PSL). Browsers treat distinct subdomains under a public suffix as separate eTLD+1 sites (cross-site). Therefore, direct cross-subdomain `fetch` calls cannot set or send `SameSite=Lax` cookies.

### 4.2 Safe Proxy Activation Procedure (6-Step Verification)
To prevent forwarding cookies or `Authorization` headers to an unverified third-party host, the `/api/*` rewrite in `render.yaml` is disabled until the following verification is completed:

1. **Provision Staging API:** After explicit user approval, link the repository and provision `dormiostay-api` via Render Blueprint.
2. **Obtain Actual Render-Assigned URL:** Once the service deploys, copy the exact assigned hostname from the Render Dashboard (e.g. `https://dormiostay-api-xyz.onrender.com`).
3. **Verify Service Ownership:** Confirm in the dashboard that the assigned hostname corresponds exactly to our provisioned service instance (verify service ID and deployment ID).
4. **Verify Destination HTTPS:** Verify TLS termination and valid certificate at the assigned destination (`curl -Iv https://<assigned-host>/healthz`).
5. **Activate Proxy Rewrite:** In `render.yaml`, uncomment the rewrite rule and set:
   ```yaml
   - type: rewrite
     source: /api/*
     destination: https://<VERIFIED_STAGING_API_ORIGIN>/api/*
   ```
6. **Verify Proxy Behavior:** Verify that requests through `https://<web-domain>/api/...` proxy to the API, preserving headers (`Authorization`, `Cookie`, `Origin`, `X-Request-ID`) and supporting all HTTP methods (`GET`, `POST`, `PUT`, `PATCH`, `DELETE`) without 301/302 redirects.

---

## 5. Database Strategy & Migration Management (Neon PostgreSQL)

DormioStay uses Neon Serverless PostgreSQL with pgvector and connection pooling.

### 5.1 Connection Strings
Two separate connection strings are used:
1. **Pooled Runtime Application String (`DATABASE_URL`):**
   - Format: `postgresql+asyncpg://dormiostay_app:<password>@ep-<id>-pooler.<region>.aws.neon.tech/neondb?sslmode=require`
   - Uses Neon's built-in PgBouncer pooler (`-pooler`).
   - Connects as the restricted `dormiostay_app` role (`NOSUPERUSER`, `NOBYPASSRLS`).
2. **Direct Unpooled Migration String (`DATABASE_URL_DIRECT`):**
   - Format: `postgresql://neondb_owner:<password>@ep-<id>.<region>.aws.neon.tech/neondb?sslmode=require`
   - Bypasses PgBouncer (pooler mode interferes with DDL locks and transactional schema modifications).
   - Connects as the administrative migration role.

### 5.2 Controlled Alembic Migration Execution & Strict Migration Safety Policy
Migrations are **never** executed automatically during web service startup (`uvicorn app.main:app`). Running migrations at startup can cause race conditions during horizontal scaling or deployment rollouts.

To prevent accidental data loss or target misconfiguration, `app.db.migration_guard` enforces an executable, fail-closed policy:

1. **Local Disposable Test Migrations: PERMITTED under existing T-03 safeguards:**
   - If `TEST_DATABASE_NAME` is set, the target database must match `dormiostay_t03_test_[a-z0-9_]+` on `127.0.0.1`, `localhost`, or `::1`.
   - Accepted `localhost` targets are canonicalized to a literal loopback IP (`127.0.0.1`) before connection, preventing DNS resolution from redirecting to remote destinations. IPv6 loopback (`::1`) is supported safely.
   - Both upgrades and downgrades are permitted for local integration test suites.
   - Safe TLS configuration via `sslmode=require` (or `ssl=require`) is preserved and supported. Target parameter overrides (e.g. `host=`, `port=`, `database=`, `dsn=`, `service=`, `server_settings=`) and conflicting/duplicate TLS parameters (e.g. `ssl=disable&ssl=require`, `sslmode=require&ssl=require`) are strictly rejected.
2. **All Remote Alembic Migrations: DISABLED:**
   - For this T-05 handoff, **ALL remote database migrations are disabled** until independently verified staging infrastructure is available.
   - This applies strictly to:
     - Remote staging upgrades.
     - Remote staging downgrades.
     - Remote production upgrades.
     - Remote production downgrades.
     - Offline SQL generation targeting remote databases.
   - Any remote target (any host not in `{"127.0.0.1", "localhost", "::1"}`) is rejected immediately before any SQL, DDL, or migration output is generated.
3. **No Caller-Controlled Authorization Override:**
   - Self-declared `APPROVED_STAGING_*` environment variables cannot authorize remote execution.
   - `MIGRATION_ACTION=upgrade` cannot bypass downgrade protection; programmatic `command.downgrade()` and CLI downgrade arguments are detected fail-safe.
4. **Current Infrastructure & Review Status:**
   - Local disposable test database migrations: **PERMITTED** under existing safeguards.
   - All remote Alembic migrations: **DISABLED**.
   - Neon staging database provisioning: **PENDING**.
   - Independent Neon identity verification: **PENDING**.
   - Future staging migration activation: **REQUIRES a separately reviewed, controlled mechanism**.
   - Render API destination verification and proxy activation: **PENDING**.
   - Live staging deployment: **PENDING**.
   - Final Astra approval: **PENDING**.

---

## 6. Health & Readiness Probes, Cold Starts & Waking Banner

### 6.1 Probe Endpoints
- **Platform Liveness Probe (`GET /healthz`):**
  - Returns `{"status": "ok"}` with HTTP 200.
  - Zero external dependencies (does not touch PostgreSQL or Redis).
  - Used by Render's health check runner to determine container health.
- **Web Startup Probe (`GET /api/v1/healthz`):**
  - Mounted under `api_router` (`/api/v1`) for consumption by the Web frontend.
  - Returns `{"status": "ok"}` with HTTP 200.
- **Readiness Probe (`GET /readyz` and `GET /api/v1/readyz`):**
  - Executes a database connectivity check with a 5-second timeout (`database_connect_timeout_seconds`).
  - Returns `{"status": "ready"}` with HTTP 200 if database is reachable.
  - Returns `{"status": "unavailable"}` with HTTP 503 if database check fails.

### 6.2 Render Free-Tier Sleeping Behavior & Frontend Defense
- On Render's Free tier, the web service automatically spins down (sleeps) after **15 minutes of inactivity**.
- The next incoming request triggers a cold start, which takes approximately **40–70 seconds**.
- **Frontend Startup Probe & Waking Banner:**
  - Upon web application mount, `<StartupProbe />` (`apps/web/src/components/ui/startup-probe.tsx`) issues a lightweight, unauthenticated `GET /api/v1/healthz` with a 65-second timeout.
  - If the backend is sleeping, the request exceeds 4 seconds, triggering the `WakingBanner` (`apps/web/src/components/ui/waking-banner.tsx`):
    > *"Waking things up… this can take up to a minute."*
  - As soon as the API wakes up and responds, the banner automatically dismisses.
  - If the request times out or network is offline, the error is handled gracefully without crashing the UI, throwing uncaught rejections, or entering infinite request loops.
- **Always-On Upgrade Checklist (Pre-Launch):**
  1. Navigate to the Render Dashboard -> `dormiostay-api` -> **Settings**.
  2. Change Instance Type from **Free** to **Starter** ($7/month).
  3. Verify the service remains continuously running (`Always On` active, 0ms wake-up latency).

---

## 7. Native Mobile Environment Strategy & Link Handling

DormioStay Native Mobile (`apps/mobile`) supports three distinct build profiles configured in `eas.json` and `app.config.ts`.

### 7.1 Mobile Profile Matrix

| Configuration | Development | Preview (Staging) | Production |
|---|---|---|---|
| **EAS Profile** | `development` | `preview` | `production` |
| **Android Package** | `app.dormiostay.mobile.development` | `app.dormiostay.mobile.preview` | `app.dormiostay.mobile` |
| **iOS Bundle ID** | `app.dormiostay.mobile.development` | `app.dormiostay.mobile.preview` | `app.dormiostay.mobile` |
| **Custom URL Scheme** | `dormiostay-development://` | `dormiostay-preview://` | `dormiostay://` |
| **`EXPO_PUBLIC_API_BASE_URL`** | Local tunnel / dev API | `https://staging-api.dormiostay.com/api/v1` | `https://api.dormiostay.com/api/v1` |
| **Showcase Route (`/showcase`)** | Accessible | Blocked (`<Redirect href="/" />`) | Blocked (`<Redirect href="/" />`) |
| **Distribution Channel** | Internal dev client / simulator | Internal testing (TestFlight / APK) | App Store / Google Play |

### 7.2 Session & SecureStore Key Isolation
To prevent staging authentication credentials from leaking into production (or vice-versa), `createSessionStore(apiBase)` dynamically hashes the target API base URL into the hardware-backed SecureStore key:
```text
key = "dormiostay.session." + hex(apiBase)
```
Switching environments automatically targets an independent SecureStore storage partition, guaranteeing zero credential crossover.

### 7.3 Mobile HTTPS Deep Link Handling & Association Deferral
- **Deferred Native Association Declarations:**
  - Active native declarations (`associatedDomains` in iOS and `intentFilters` with `autoVerify: true` in Android) in `app.config.ts` are **intentionally deferred to T-10 / T-14M**.
  - **Prerequisites for Live Activation:**
    1. Production domain DNS configuration (`dormiostay.com`).
    2. Apple Developer Team ID and hosted `https://dormiostay.com/.well-known/apple-app-site-association`.
    3. Google Play Console app registration and hosted `https://dormiostay.com/.well-known/assetlinks.json` containing the release keystore SHA-256 fingerprint.
- **Safe Link Handling (`safeIncomingPath`):**
  - Even while native association declarations are deferred, `safeIncomingPath` in `apps/mobile/src/lib/linking.ts` is fully equipped to handle allowlisted HTTPS links:
    - Allowlisted hosts: `dormiostay.com`, `www.dormiostay.com`, `staging.dormiostay.com`, `preview.dormiostay.com`, `development.dormiostay.com`.
    - Any foreign host (e.g. `https://evil.com/phishing`) is safely rejected and routed to `/`.
    - URLs containing credentials, query parameters, or hash fragments are stripped and routed to `/`.
    - Only approved public and authenticated shell routes are navigable; admin routes (`/admin`) and unauthorized routes fall back to `/`.
    - Component Showcase (`/showcase`) remains strictly blocked in production and preview modes.

---

## 8. Secrets Ownership & Configuration Storage

**Zero-Secret Policy:** No real passwords, API tokens, provider secrets, or connection strings are ever committed to version control.

| Environment Variable | Where it Lives | Exposure |
|---|---|---|
| `APP_ENV` | `render.yaml` (`staging` for Blueprint) | Non-sensitive server configuration |
| `DATABASE_URL` | Render Dashboard (`sync: false`) | **Server Secret** (Pooled Neon connection) |
| `DATABASE_URL_DIRECT` | CI / Migration Terminal | **Server Secret** (Direct migration connection) |
| `APPROVED_STAGING_HOST` | Deferred (Future Staging) | Authoritative independently configured host (cannot override remote block) |
| `APPROVED_STAGING_DATABASE` | Deferred (Future Staging) | Authoritative independently configured db (cannot override remote block) |
| `APPROVED_STAGING_MIGRATION_ROLE` | Deferred (Future Staging) | Authoritative migration role (`neondb_owner`) |
| `MIGRATION_ENVIRONMENT` | CI / Migration Terminal | Non-sensitive environment flag (`staging`) |
| `MIGRATION_CONFIRMATION` | CI / Migration Terminal | Non-sensitive explicit confirmation flag |
| `FRONTEND_ORIGIN` | Render Dashboard (`sync: false`) | **Server Secret** (Exact CORS origin) |
| `SENTRY_DSN` | Render Dashboard (`sync: false`) | **Server Secret** (Backend Sentry DSN) |
| `REQUEST_ID_HEADER` | `render.yaml` (`X-Request-ID`) | Non-sensitive server configuration |
| `DATABASE_CONNECT_TIMEOUT_SECONDS` | `render.yaml` (`5.0`) | Non-sensitive server configuration |
| `EXPO_PUBLIC_API_BASE_URL` | `eas.json` / EAS Secrets | **Public Mobile Config** (HTTPS API base) |
| `EXPO_PUBLIC_SENTRY_DSN` | EAS Secrets | **Public Mobile Config** (Redacted native DSN) |
| Android Signing Keystore | EAS Secret Files | **CI/Build Secret** (Never in repository) |
| iOS Distribution Certificate | Apple Developer Portal / EAS Credentials | **CI/Build Secret** (Never in repository) |

---

## 9. Rollback, Troubleshooting & Monitoring

### 9.1 Render Web Service Rollback
1. Open the Render Dashboard -> Service -> **Events**.
2. Identify the last known successful deploy.
3. Click the kebab menu (`...`) and select **Rollback to this deploy**.
4. Render immediately switches traffic back to the prior release container.

### 9.2 Database Schema Rollback & Remote Migration Policy
- **All remote migrations are disabled, including upgrades, downgrades and forward fixes, until a separately reviewed enabling change is explicitly authorized.**
- In local development and test environments, schema rollbacks are tested against authorized disposable databases (`TEST_DATABASE_NAME=dormiostay_t03_test_*`).

---

## 10. Estimated Service Cost Matrix (Official Source Verification)

All tiers verified against current provider documentation as of November 2026.

| Provider | Service Tier | Quota / Allocation | Estimated Cost |
|---|---|---|---|
| **Render** | Free Web Service (`plan: free`) | 512 MB RAM, 0.1 CPU, 750 free hours/mo (spins down) | **$0 / month** |
| **Render** | Starter Web Service | 512 MB RAM, 0.5 CPU, Always-On (recommended for launch) | **$7 / month** |
| **Render** | Static Site | Unlimited bandwidth, global CDN | **$0 / month** |
| **Neon** | Free Tier | 0.5 GB storage, 10 branches, shared compute | **$0 / month** |
| **Neon** | Launch Plan | Point-in-time restore, higher compute, no scale-to-zero | **$19 / month** |
| **Upstash Redis** | Free Tier | 10,000 commands/day, 256 MB storage | **$0 / month** |
| **Cloudflare R2** | Free Tier | 10 GB storage/mo, 1M Class A, 10M Class B ops, zero egress | **$0 / month** |
| **Cloudinary** | Free Tier | 25 monthly credits (~25,000 transformations / 25 GB) | **$0 / month** |
| **Brevo** | Free Tier | 300 transactional emails/day | **$0 / month** |
| **Geoapify** | Free Tier | 3,000 credits/day (evaluation / non-commercial) | **$0 / month** |
| **Apple Developer** | Individual / Org | TestFlight, Universal Links Team ID, App Store distribution | **$99 / year** |
| **Google Play** | Developer Account | Play Console app distribution, Android App Links | **$25 one-time** |
| **Expo (EAS)** | Free Tier | 30 Android builds/mo, 15 iOS builds/mo | **$0 / month** |
| **Total MVP Run Rate** | **Development / Staging** | Full free-tier stack | **$0 / month** |
| **Total Launch Run Rate**| **Production** | Always-On Render API ($7) + Developer Accounts | **~$15 / month** |

---

## 11. Infrastructure Actions Requiring Explicit User Approval

The following operations must **never** be performed automatically by an AI agent without explicit user approval:
1. Creating or provisioning live Render services, Blueprints, or environment groups.
2. Creating Neon database branches or running schema migrations against staging or production.
3. Submitting builds to EAS (`eas build`) or uploading native credentials.
4. Purchasing domains or setting up live DNS records.
5. Staging, committing, or pushing changes to the Git repository.

---

## 12. Tracked Cross-Platform Mobile UI Issue

- **Identified Defect:** On the Android Create Account screen, typography is slightly larger and section layout differs from iOS.
- **Classification:** Deferred cross-platform UI consistency issue.
- **Status:** Documented and tracked for a dedicated post-foundation mobile UI polish ticket. Does not block deployment configuration or environment separation.
