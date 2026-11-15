# DormioStay — Technical Architecture Document

*Version 1.0 | November 2026 | Companion to the DormioStay PRD (v1 MVP)*

**Who this is for:** you (the founder) and any developer or AI coding tool that builds DormioStay. It explains what to build with, why, where every file lives, what the database looks like, and what configuration you need before writing code.

**Version note:** library and service details change. Pin exact versions when you create the project, and re-check provider docs (Geoapify, Brevo, Cloudinary, Cloudflare R2, Neon, Upstash, Gemini, Render) before relying on any limit or price quoted here.

---

## 1. Architecture at a glance

Plain English: the browser talks only to your website (a React + Vite app). Your website forwards API calls to your own backend (FastAPI). The backend is the only thing allowed to touch the database, send email, call AI or store files. Nothing sensitive is ever called straight from the browser.

```
Browser (guest / tenant / owner / admin)
   |
   v
React + Vite SPA  (pages, UI, maps)  -- dev proxy / Render rewrite: /api/* -->
   |
   v
FastAPI backend  (auth, permissions, business logic, share-preview route)
   |-- Neon PostgreSQL     (source of truth: users, listings, visits, audit)
   |-- Upstash Redis       (rate limits and short-lived cache only)
   |-- Brevo               (transactional email, via outbox table)
   |-- Geoapify            (address search; map tiles used by browser)
   |-- Cloudinary          (public property photos)
   |-- Cloudflare R2       (PRIVATE verification documents)
   |-- Gemini (AI)         (turns text into filters; drafts descriptions)
   |-- Sentry              (error tracking)
Hosting: Render (static site + api + cron worker)
```

**The five rules that shape everything:**

1. PostgreSQL is the single source of truth. Redis never stores accounts, listings or sessions.
2. The backend enforces every permission. The frontend only hides buttons.
3. AI suggests; the backend validates. AI never writes SQL or changes data.
4. The product works with AI, Redis hiccups or email delays degraded, not broken.
5. Gangtok and ICFAI are data rows, never constants in code.

## 2. Recommended tech stack and reasoning

| Layer | Choice | Why this fits DormioStay | Switch when |
| --- | --- | --- | --- |
| Frontend framework | **React + Vite + TypeScript + React Router** | Fast to build and cheap to host as a static site on Render. The blueprint selected React + Vite for the MVP. The property details page is client-side rendered via React Router with dynamic document title and JSON-LD structured data; use the small FastAPI share-preview route so shared links show a rich preview in WhatsApp and social apps. One codebase with nested layout routes serves tenant site, owner dashboard and admin console. Settles the open frontend question in the PRD. | Consider pre-rendering only if search visibility proves poor |
| Styling / UI | **Tailwind CSS + shadcn/ui (Radix)** | Fast to build, accessible modals, drawers and menus out of the box, fully restyleable to the DormioStay brand |  |
| Data fetching | **TanStack Query** | Caching, loading and error states for dashboards and search |  |
| Forms | **React Hook Form + Zod** | Multi-step owner wizard needs solid validation; Zod schemas can mirror backend rules |  |
| Maps (browser) | **MapLibre GL JS** with Geoapify tiles | Open-source, no per-map-load vendor lock | Heavy geo search later |
| Backend | **FastAPI (Python 3.12) + Pydantic v2** | Fast, typed, auto-generated API docs, and fits your AI/ML skills |  |
| ORM / migrations | **SQLAlchemy 2.0 (async) + Alembic** | Mature, explicit, safe schema migrations from day one |  |
| Database | **Neon PostgreSQL** | Real relational data (listings, rooms, visits) with strong constraints; free tier to start; branching for staging. Use pgvector later only if needed | Move plan tier as traffic grows |
| Password hashing | **pwdlib\[argon2\] (Argon2id)** | Current best practice; slow on purpose to resist stolen-database attacks |  |
| Sessions | **Custom server-side sessions in Postgres** | Instant revocation when you ban a user. See the Security document |  |
| Cache / rate limit | **Upstash Redis** | Serverless, pay-per-use, good for counters with expiry |  |
| Email | **Brevo transactional API** | Generous free tier and delivery events. Sender identity must be verified separately from your Gmail contact address | Postmark/SES if deliverability is poor |
| Address search | **Geoapify** | Autocomplete, geocoding and tiles under one key with a free daily quota | Self-host later if costs rise |
| Public images | **Cloudinary** | Resizing, optimisation and thumbnails without you building an image pipeline | R2 + image CDN at scale |
| Private documents | **Cloudflare R2 (private bucket)** | S3-compatible, no public URLs, short-lived signed links, cheap |  |
| AI | **Gemini via a provider-agnostic wrapper** | Cheap start; the wrapper lets you swap models without touching features | Any provider |
| Background jobs | **Outbox table + Render Cron Job** (later a real queue) | Free-tier servers sleep, so you cannot rely on in-memory tasks. Emails are saved first, sent by a worker | Redis queue (RQ/Arq) when volume grows |
| Hosting | **Render** (static site, api, cron) | Simple to start. Free API tier sleeps \~15 min and has an ephemeral disk, so it is for development and testing only | Paid always-on before public launch |
| Errors / logs | **Sentry + structured JSON logs** | You will not see problems unless something reports them |  |
| Testing | **pytest, Vitest, Playwright** | API logic, UI components, and end-to-end student and owner journeys |  |
| CI | **GitHub Actions** | Run tests and linting on every push |  |

## 3. Key architecture decisions

| # | Decision | Reason |
| --- | --- | --- |
| 1 | **Same-origin API proxy.** The browser calls `/api/...` on the website domain; Vite dev proxy (vite.config.ts) and Render Static Site rewrite rules forward to FastAPI. | Session cookies stay first-party, simplifying cookie and CSRF handling. No open credentialed CORS. |
| 2 | **Layered backend:** routes → services → repositories → database. | Routes only check input and permissions. Business rules live in services, so they are testable and reusable. |
| 3 | **UUID primary keys, UTC timestamps (timestamptz).** Display in Indian Standard Time on screen. | Prevents guessable IDs and time-zone bugs. |
| 4 | **Money stored as whole rupees (INTEGER, not NUMERIC).** | No floating-point errors. Add paise only if payments arrive. |
| 5 | **One property = one listing; sharing types are room configurations inside it.** | Matches the PRD and future plan limits. |
| 6 | **Soft deletion and status fields** instead of deleting important rows. | Preserves history for moderation, audits and disputes. |
| 7 | **Outbox pattern for email and notifications.** | A failed email never fails the user action; delivery status is recorded honestly. |
| 8 | **Database-backed config** (locations, amenities, homepage sections, AI limits) editable from the admin console. | No developer needed to change content. Security settings stay in environment variables. |
| 9 | **Postgres row-level security as a second lock** behind backend checks. | If a backend bug slips through, the database still refuses cross-user access. |
| 10 | **Future payment tables exist in design but not in v1 migrations.** | Avoids building billing before demand is proven. |

**Share previews for WhatsApp and social apps.** The property details page is client-side rendered via React Router with dynamic document title and JSON-LD structured data. Because social app crawlers do not execute JavaScript, a small FastAPI route, `GET /api/v1/share/property/{slug}`, returns a tiny page with those preview tags for approved listings and immediately sends real visitors on to the normal `/property/{slug}` page. The Share button on every property page copies this link. The same router also serves `sitemap.xml`. The route never includes a private address, phone number or owner detail.

## 4. Complete project structure

One repository (monorepo) with two apps. A separate `docs` folder keeps these documents with the code.

```
dormiostay/
├── README.md
├── .gitignore
├── .env.example                  # names only, never real secrets
├── docker-compose.yml            # optional local Postgres + Redis
├── .github/workflows/ci.yml      # lint + tests on every push
├── docs/
│   ├── prd.md
│   ├── architecture.md
│   ├── security.md
│   ├── frontend-spec.md
│   └── tickets.md
│
├── apps/
│   ├── api/                      # FastAPI backend
│   │   ├── pyproject.toml
│   │   ├── alembic.ini
│   │   ├── Dockerfile
│   │   ├── alembic/versions/     # migration files
│   │   ├── scripts/              # seed_locations.py, create_admin.py, worker.py
│   │   ├── app/
│   │   │   ├── main.py           # app creation, middleware, router mounting
│   │   │   ├── core/
│   │   │   │   ├── config.py     # reads environment variables
│   │   │   │   ├── security.py   # hashing, token generation
│   │   │   │   ├── sessions.py   # create/validate/revoke sessions
│   │   │   │   ├── permissions.py# role dependencies
│   │   │   │   ├── csrf.py       # Origin check + token
│   │   │   │   ├── rate_limit.py # Redis limits + fallback
│   │   │   │   ├── errors.py     # error classes + handlers
│   │   │   │   └── logging.py
│   │   │   ├── db/
│   │   │   │   ├── session.py    # async engine, per-request user context for RLS
│   │   │   │   ├── base.py
│   │   │   │   └── models/       # users.py, properties.py, visits.py, ...
│   │   │   ├── schemas/          # Pydantic request/response models
│   │   │   ├── repositories/     # all SQL lives here
│   │   │   ├── services/
│   │   │   │   ├── auth_service.py
│   │   │   │   ├── property_service.py
│   │   │   │   ├── search_service.py
│   │   │   │   ├── visit_service.py
│   │   │   │   ├── moderation_service.py
│   │   │   │   ├── notification_service.py
│   │   │   │   ├── email_service.py      # Brevo client
│   │   │   │   ├── storage_service.py    # Cloudinary + R2
│   │   │   │   ├── geo_service.py        # Geoapify + distance
│   │   │   │   └── ai/
│   │   │   │       ├── provider.py       # provider-agnostic interface
│   │   │   │       ├── search_parser.py  # text -> validated filters
│   │   │   │       └── description_gen.py
│   │   │   └── api/v1/
│   │   │       ├── auth.py  users.py  properties.py  search.py
│   │   │       ├── visits.py  messages.py  reviews.py  reports.py
│   │   │       ├── owner.py  notifications.py  locations.py
│   │   │       ├── share.py  seo.py   # WhatsApp share-preview page, sitemap.xml
│   │   │       └── admin/ (hosts.py, listings.py, content.py, reports.py, audit.py, settings.py)
│   │   └── tests/                # unit/, integration/, security/
│   │
│   └── web/                      # React + Vite frontend (single-page app)
│       ├── package.json
│       ├── index.html            # fonts (Google Fonts or self-hosted @font-face), root element
│       ├── vite.config.ts        # Vite dev proxy: /api -> http://localhost:8000
│       ├── tailwind.config.ts    # brand tokens
│       ├── public/               # robots.txt, favicon, fonts/
│       └── src/
│           ├── main.tsx
│           ├── app/
│           │   ├── router.tsx    # React Router routes + role guards
│           │   └── providers.tsx # query client, auth, toasts
│           ├── layouts/          # nested layout routes: PublicLayout, AuthLayout, TenantLayout, OwnerLayout, AdminLayout
│           ├── pages/            # nested layout routes: public/, auth/, tenant/, owner/, admin/
│           │   ├── auth/         # login, signup, verify-email, forgot-password, reset-password
│           │   ├── tenant/       # saved, visits, messages, notifications, profile
│           │   ├── owner/        # overview, properties, wizard, visits, availability
│           │   └── admin/        # hosts, listings, reports, locations, content, audit, settings
│           ├── features/         # auth, search, properties, visits, wizard, admin (hooks + API calls)
│           ├── components/
│           │   ├── ui/           # button, input, card, modal, drawer, toast, badge
│           │   ├── property/     # PropertyCard, RoomTable, Gallery, VerifiedBadge
│           │   ├── search/       # SearchBar, FilterSheet, ResultList, ResultMap
│           │   ├── wizard/       # the 7 owner wizard steps
│           │   ├── admin/        # DataTable, DetailDrawer, ConfirmDialog
│           │   └── seo/          # PageHead (title, meta tags, JSON-LD)
│           ├── lib/              # api-client.ts, auth.ts, format.ts, geo.ts
│           ├── hooks/
│           ├── schemas/          # Zod validation
│           └── styles/
```

## 5. Database schema in plain English

**Conventions for every table:** `id` (UUID), `created_at`, `updated_at` (UTC). Tables that matter for history also have `deleted_at` or a `status` field. "Belongs to" means a link (foreign key) that must point to a real row.

Only tables needed for v1 are listed. Later-phase tables are at the end.

### 5.1 Accounts and access

- **users** — one row per person who has an account. Fields: email (unique), password\_hash, role (`tenant`, `owner`, `admin`), email\_verified\_at, status (`active`, `suspended`, `banned`), failed\_login\_count, locked\_until, last\_login\_at. *Relationships:* one user has one profile, many sessions, and (if owner) one host application and many properties.
- **user\_profiles** — display details. Fields: full\_name, phone, avatar\_url, gender (optional), preferred budget range, preferred areas, institution\_id (their university). *Belongs to* one user.
- **user\_sessions** — each logged-in browser. Fields: user\_id, token\_hash (never the raw token), created\_at, last\_activity\_at, expires\_at, absolute\_expires\_at, revoked\_at, revocation\_reason. *Belongs to* one user; many per user.
- **email\_verification\_tokens** (not email\_action\_tokens) and **password\_reset\_tokens** — one-time links. Fields: user\_id, token\_hash, expires\_at, used\_at. *Belong to* a user.
- **admin\_mfa** (not admin\_totp\_credentials) — the admin's authenticator setup. Fields: user\_id, encrypted TOTP secret, enabled\_at. Plus **admin\_recovery\_codes** (user\_id, code\_hash, used\_at).
- **account\_status\_history** — who suspended or restored an account and why. Fields: user\_id, old\_status, new\_status, reason, changed\_by (an admin user).
- **host\_applications** — an owner's request to be approved. Fields: user\_id, business\_name, contact phone, address summary, status (`draft`, `submitted`, `needs_info`, `approved`, `rejected`), rejection\_reason, reviewed\_by, reviewed\_at. *Belongs to* one owner user.
- **verification\_documents** — files the owner uploaded. Fields: host\_application\_id, doc\_type, private\_storage\_key (path in the private bucket, never a public link), file\_size, content\_type, uploaded\_at. *Belongs to* a host application.

### 5.2 Locations and institutions (admin-managed)

- **locations** — a hierarchy of places: country, state, district, city, neighbourhood. Fields: name, type, parent\_id (points to another location row), latitude, longitude, aliases (list of alternate spellings), is\_active, display\_order. *Example:* Tadong belongs to Gangtok, which belongs to East Sikkim.
- **institutions** — universities and colleges. Fields: name, aliases, location\_id, latitude, longitude, is\_active. *Example:* ICFAI University, Sikkim.

### 5.3 Properties

- **properties** — one physical building or PG (one listing). Fields: owner\_id, name, slug (for the web address), category (PG, hostel, shared, student accommodation), description, gender\_policy, food\_policy, visitor\_policy, curfew\_time, smoking\_policy, status (`draft`, `pending_review`, `changes_requested`, `approved`, `rejected`, `suspended`, `archived`), verification\_status (`unverified`, `verified`) with verified\_by and verified\_at, published\_at, last\_availability\_update\_at, view\_count. *Relationships:* belongs to one owner; has one location record, many rooms, images, amenities.
- **property\_locations** — where it is. Fields: property\_id, location\_id (neighbourhood), private\_full\_address (never shown publicly), public\_address\_text, latitude, longitude (exact), public\_latitude, public\_longitude (what visitors see, possibly approximate), landmark. *One-to-one* with a property.
- **room\_configurations** — the sharing types inside a property, e.g. 'Double sharing at ₹6,000'. Fields: property\_id, sharing\_type (single, double, triple, four), description, monthly\_rent (INTEGER rupees, not NUMERIC), security\_deposit (INTEGER rupees, not NUMERIC), additional\_charges (text or small list), total\_beds, available\_beds, is\_active. *Belongs to* a property; a property has one to many.
- **property\_images** — photos. Fields: property\_id, cloudinary\_public\_id, url, alt\_text, sort\_order, is\_cover. *Belongs to* a property.
- **amenities** — the master list managed by admins (Wi-Fi, laundry, geyser...). Fields: name, icon, is\_active. **property\_amenities** links a property to amenities (many-to-many).
- **property\_categories** — admin-managed list behind the category field.
- **property\_revisions** *(Should-have)* — a pending edit to an already-live listing. Fields: property\_id, proposed\_changes (structured data), status, reviewed\_by. The live version stays visible until approved.

### 5.4 Tenant activity

- **saved\_properties** — favourites. Fields: user\_id, property\_id, saved\_at. Unique pair (a user cannot save the same property twice).
- **visits** — visit requests. Fields: property\_id, room\_configuration\_id (optional), tenant\_id, requested\_datetime, message, status (`pending`, `accepted`, `rejected`, `reschedule_requested`, `confirmed`, `completed`, `cancelled`, `no_show`), proposed\_datetime (if owner suggests another time), owner\_response\_note, tenant\_confirmed\_completed\_at. *Belongs to* a property and a tenant.
- **visit\_events** (not visit\_history) — a history line for every status change (who, what, when). *Belongs to* a visit.
- **conversations** and **messages** *(Should-have)* — a chat between one tenant and one owner about a property. Messages: conversation\_id, sender\_id, body, read\_at. Only the two participants can see them.
- **reviews** *(Should-have)* — Fields: property\_id, tenant\_id, visit\_id (proves a real visit; unique so one review per visit), rating, text, status (`pending`, `published`, `hidden`), owner\_response. *Belongs to* a visit.
- **reports** — complaints. Fields: reporter\_id, target\_type (`property`, `user`, `review`, `message`), target\_id, reason, details, status (`pending`, `investigating`, `action_required`, `resolved`, `dismissed`), resolution\_note, handled\_by.
- **search\_logs** — what people searched (filters only, no personal detail beyond user\_id when logged in). Used for demand insight.

### 5.5 Notifications and email

- **notifications** — the in-app bell. Fields: user\_id, type, title, body, link, read\_at.
- **email\_outbox** — every email we intend to send. Fields: user\_id (optional), to\_email, template, payload, status (`queued`, `sending`, `accepted_by_provider`, `delivered`, `failed`), provider\_message\_id, attempts, last\_error, send\_after. The worker reads this table. *This is how we avoid ever claiming an email was delivered when it was only attempted.*
- **notification\_templates** — editable email and in-app wording (can start as code and move to the database).

### 5.6 Admin, content and platform

- **homepage\_sections** — blocks on the homepage. Fields: key, type, title, body, data (structured), is\_visible, sort\_order, draft\_data, published\_at.
- **announcements** — Fields: title, body, audience (`all`, `tenants`, `owners`), status (`draft`, `scheduled`, `published`, `archived`), publish\_at.
- **platform\_settings** — a key/value table for safe, editable settings (AI on/off, daily AI limits, contact email). Never secrets.
- **ai\_usage\_records** — every AI call. Fields: user\_id (nullable), feature, model, input\_tokens, output\_tokens, success, error, created\_at. Enforces limits and tracks cost.
- **moderation\_actions** — what an admin did to a listing, review or account and why.
- **audit\_logs** — append-only record of sensitive admin actions. Fields: actor\_id, action, target\_type, target\_id, before (data), after (data), reason, ip (limited), created\_at. The application database role can insert but not update or delete.

### 5.7 Designed now, built later (do not create in v1)

`featured_campaigns`, `offers`, `offer_eligibility_rules`, `coupons`, `coupon_redemptions`, `subscription_plans`, `plan_entitlements`, `subscriptions`, `payments`, `payment_transactions`, `invoices`, `payment_webhook_events`, `property_ai_knowledge`, `property_faqs`, `ai_conversations`, `ai_messages`.

### 5.8 Relationship map (summary)

```
users 1--1 user_profiles          users 1--* user_sessions
users(owner) 1--1 host_applications 1--* verification_documents
users(owner) 1--* properties 1--1 property_locations
properties 1--* room_configurations   properties 1--* property_images
properties *--* amenities (via property_amenities)
locations 1--* locations (parent)     locations 1--* institutions
users(tenant) 1--* visits *--1 properties
visits 1--* visit_events              visits 1--0..1 reviews
users *--* properties (via saved_properties)
```

### 5.9 Indexes and search design

- Index properties on (status, published\_at), (owner\_id), and (slug unique).
- Index room\_configurations on (property\_id, monthly\_rent, sharing\_type, available\_beds).
- Index property\_locations on location\_id and coordinates. Distance uses the Haversine formula in SQL (straight-line, labelled as such); add PostGIS later when needed.
- Add trigram (pg\_trgm) indexes on location names and aliases so 'Tadong', 'tadong' and small typos still match.
- Add unique constraints: user email, saved\_properties(user, property), reviews(visit\_id), property slug.
- Add CHECK constraints: rent greater than 0, available\_beds between 0 and total\_beds, latitude between -90 and 90, longitude between -180 and 180.
- Search only returns properties with status `approved`. This rule lives in one repository function and in the row-level security policy.

### 5.10 How AI search flows through the system

1. Tenant types a sentence. 2. Backend checks the rate limit and the admin AI switch. 3. Backend sends the sentence plus a strict JSON schema to the AI provider. 4. AI replies with filters only (area, max budget, sharing type, amenities, gender). 5. Backend validates every value against real lists (known locations, known amenities, sane budget). 6. Backend runs the normal search with those filters and returns `{filters_applied, results}`. 7. If AI fails or times out, the page falls back to the normal filter form with a friendly message.

## 6. Environment variables and configuration

Never commit real values. Keep a `.env.example` with names only. Use separate values for development, staging and production, and never reuse production secrets locally.

### 6.1 Backend (apps/api)

| Variable | Purpose | Notes |
| --- | --- | --- |
| `APP_ENV` | development / staging / production | Controls cookie Secure flag, debug behaviour |
| `DATABASE_URL` | Neon connection used by the app | Use Neon's **pooled** connection string for the running app |
| `DATABASE_URL_DIRECT` | Neon direct connection for migrations | Migrations behave better without the pooler |
| `REDIS_URL` or `UPSTASH_REDIS_REST_URL` + `UPSTASH_REDIS_REST_TOKEN` | Rate limiting and cache | Use one method consistently |
| `SESSION_COOKIE_NAME` | Cookie name | e.g. `dormio_session` |
| `SESSION_IDLE_MINUTES`, `SESSION_ABSOLUTE_DAYS`, `ADMIN_SESSION_IDLE_MINUTES` | Session lifetimes | Admin sessions shorter |
| `FRONTEND_ORIGIN` | Exact allowed website origin | Used for Origin check and CORS; no wildcards |
| `CSRF_SECRET` | Signs CSRF tokens | Long random string |
| `TOTP_ENCRYPTION_KEY` | Encrypts admin TOTP secrets at rest | Losing it locks admins out; back it up safely |
| `BREVO_API_KEY` | Email API | Keep server-side only |
| `EMAIL_FROM_ADDRESS`, `EMAIL_FROM_NAME` | Verified sender | Do not assume dormiostay@gmail.com can be a sender |
| `PUBLIC_CONTACT_EMAIL` | Shown on site | dormiostay@gmail.com |
| `GEOAPIFY_API_KEY` | Server-side geocoding | Restrict key by usage; separate from the browser key |
| `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET` | Signed image uploads | Secret never reaches the browser |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_PRIVATE`, `R2_PRESIGN_EXPIRY_SECONDS` | Private documents | Short expiry, e.g. 300 seconds |
| `AI_PROVIDER`, `AI_MODEL`, `GEMINI_API_KEY` | AI configuration | Model name is a setting, never hardcoded |
| `AI_DEFAULT_DAILY_LIMIT_PER_USER`, `AI_GLOBAL_DAILY_LIMIT` | Starting caps | Admin console can override |
| `SENTRY_DSN` | Error tracking |  |
| `INITIAL_ADMIN_EMAIL` | Used by the create-admin script | One-time bootstrap; do not leave a default password anywhere |

### 6.2 Frontend (apps/web)

| Variable | Purpose | Notes |
| --- | --- | --- |
| `VITE_SITE_URL` | Canonical website address | Used for SEO and links |
| `VITE_GEOAPIFY_MAP_KEY` | Map tiles in the browser | This key is visible to users, so **restrict it to your domains** and use a different key from the server one |
| `VITE_SENTRY_DSN` | Browser error tracking |  |
| `VITE_CLOUDINARY_CLOUD_NAME` | Building image URLs | Public by design |

Anything starting with `VITE_` is visible to everyone. Never put a secret there. Vite bakes these values into the build, so set them on the static site before each build.

### 6.3 Configuration notes you must act on

1. **Cookies and domains.** Keep the website and API behind the same site address via Vite dev proxy (vite.config.ts) and Render Static Site rewrite rules. Cookie flags: HttpOnly, Secure (production), SameSite=Lax.
2. **Render free tier.** The API sleeps after about 15 minutes idle and takes about a minute to wake. Show a loading state. Do not store uploads on its disk. Upgrade to an always-on plan before public launch.
3. **Neon.** Use the pooled string for the app, direct for migrations. Turn on backups or point-in-time restore on a paid plan before launch and test a restore once.
4. **Row-level security role.** The app connects with a normal database role that is **not** a superuser and does **not** bypass RLS. A separate, rarely used role runs migrations.
5. **Geoapify.** Keep a daily-credit alert, cache geocoding results, show required attribution on every map, and restrict keys by domain or usage.
6. **Cloudinary.** Use signed uploads from the backend; set allowed formats and a maximum size; keep a folder per property.
7. **R2.** Bucket is private. Configure CORS only for your website origin if the browser uploads directly through a presigned URL.
8. **Brevo.** Verify your sender or domain and test real inbox delivery before turning on required email verification. Until then, run in 'dev mode' showing delivery status in the admin console.
9. **Time.** Store UTC, show IST. Visit times are shown in IST everywhere.
10. **Seed data.** A seed script creates East Sikkim, Gangtok, the neighbourhoods (Tadong, Sichey, Deorali, MG Marg, Ranipool, Majitar, Rangpo), ICFAI University Sikkim, and the starting amenity list.
11. **Secrets.** Store in Render environment settings. Rotate any key that was ever pasted into chat, email or a public repo.
12. **Environments.** Create three: development (local), staging (Render + a Neon branch), production. Staging uses test keys.

## 7. Recommended build order

1. Repo, tooling, CI, empty API and web deployed to Render.
2. Database models, first migration, locations seed.
3. Authentication, sessions, roles, rate limiting, admin MFA.
4. Owner onboarding, property wizard, moderation.
5. Search, property pages, maps, saved properties.
6. Visits, notifications, email outbox and worker.
7. Admin console tools, reports, audit log.
8. AI search (behind the admin switch).
9. Security testing, performance checks, paid hosting, controlled launch.
