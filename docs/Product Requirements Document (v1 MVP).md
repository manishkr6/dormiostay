# DormioStay — Product Requirements Document (v1 MVP)

_Find Your Stay. Live With Confidence._

**Status:** Draft for review | **Date:** November 2026 | **Source:** DormioStay Product Blueprint v1.0 | **Launch market:** ICFAI University Sikkim and Gangtok

---

## 1. Summary

DormioStay is a PG, hostel and student-accommodation marketplace that helps students find a trustworthy place to live near their university, and gives PG owners a simple way to publish rooms, receive enquiries and arrange visits. AI is used to make search easier (type what you want in plain language), but the product must work fully without it.

The blueprint describes a very large platform. This PRD takes a product-manager view: it keeps the vision, but defines a **smaller, sharper v1** whose only job is to prove one thing: _students near ICFAI can find and visit a real, trustworthy PG through DormioStay faster than through word of mouth, WhatsApp groups and brokers._

### 1.1 Target platforms and implementation baseline

DormioStay v1 consists of a **responsive Web application, native Android application, native iOS application, and one shared FastAPI backend**. Native Mobile uses React Native + Expo + TypeScript; the completed React 19 + Vite Web application remains in place. PostgreSQL is the single source of truth for all clients.

Tenant and Owner journeys are available on Web and Native Mobile. **Admin remains Web-only for v1:** host review, listing moderation, reports investigation, content management, audit log and platform settings belong in the web control centre. Its information-dense workflows suit the web; excluding native Admin screens reduces mobile scope and security surface.

T-01, T-02, T-03 and T-04 are completed, committed, pushed and GitHub Actions CI-verified. This expanded scope is a forward plan, not a claim that native functionality already exists. T-04M adds `apps/mobile`; T-06/T-07 implement the two session transports. Existing code, Git history and applied migrations remain valid and are not rewritten.

## 2. Problem

### 2.1 Tenant / student problems

- Listings live in WhatsApp groups, broker contacts and word of mouth, so options are scattered and hard to compare.
- Rent, deposit, food, curfew and visitor rules are unclear until you visit or call.
- No reliable way to know if a listing is real, current or accurately described.
- Students relocating to Sikkim cannot easily judge distance from campus or safety of a neighbourhood from far away.

### 2.2 Owner problems

- Vacancies are filled through phone calls and informal messages, with no record of enquiries.
- Owners get repeated identical questions (rent, food, rules) and weak leads.
- Little visibility to students who are new to the city.

### 2.3 Why now / why here

A concentrated, recurring demand (every academic intake) around one institution, a fragmented local supply, and a founder with direct access to the target students make a location-first launch practical.

## 3. Target users

| User                                   | Who they are                                                                              | Primary goal                                                              | Key frustration                                        |
| -------------------------------------- | ----------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- | ------------------------------------------------------ |
| **Student / tenant** (primary)         | ICFAI Sikkim students, and others relocating to Gangtok for study                         | Find an affordable, safe PG near campus and visit it                      | Unverified, unclear, scattered listings                |
| **PG / hostel owner** (primary supply) | Small PG and hostel owners in Tadong, Sichey, Deorali, MG Marg, Ranipool, Majitar, Rangpo | Fill beds with suitable tenants with minimal effort                       | Phone-call-driven leads, low reach, limited tech skill |
| **Main Admin** (internal)              | DormioStay team                                                                           | Keep listings trustworthy and the platform running without developer help | Manual verification workload, moderation               |
| **Guest**                              | Any visitor                                                                               | Browse before committing                                                  | Forced sign-up before seeing value                     |

**Design assumptions:** students are mobile-first across responsive Web, Android and iOS; many owners are non-technical and will use a phone. Guest browsing never requires installation or signup. Native screens respect device navigation, safe areas, assistive technology and unreliable connections; trust matters more than volume.

## 4. Product principles (decision filters)

1. **Trust before growth.** A small number of accurate, verified listings beats many doubtful ones.
2. **Tenant discovery is free and mobile-first.**
3. **Owner tools must be usable by a non-technical owner on a phone.**
4. **AI assists, never invents.** No AI-generated prices, amenities, availability or verification status.
5. **The marketplace works without AI.** AI is an enhancement, not a dependency.
6. **Verification is never for sale.** Paid promotion must be labelled and cannot buy a verified badge.
7. **Configurable, not hardcoded.** Gangtok and ICFAI are data records, so expansion needs configuration, not a rewrite.

## 5. Goals and non-goals

### 5.1 Goals for v1

- Deliver the same core Tenant and Owner journeys on Web, Android and iOS against one API, with consistent permissions, data and status meanings.
- Onboard a credible initial supply of real, moderated PG listings around ICFAI and Gangtok.
- Let a student go from search to a confirmed visit request in a few minutes on a phone.
- Give owners a simple way to list, update availability and respond to visit requests.
- Give the admin full operational control of listings, hosts, locations and reports without code changes.

### 5.2 Explicit non-goals for v1 (what we are deliberately NOT building)

| Not in v1                                                                                                          | Why                                                                                                              |
| ------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------- |
| Owner subscription payments (Starter / Growth / Professional)                                                      | Prices unvalidated. Validate with real owners first. Keep the data model ready, do not switch on billing.        |
| Rent collection, split settlements, automated disbursement, commissions                                            | Needs legal, tax, regulatory and payment-provider review. High risk, low value before demand is proven.          |
| Native Admin, separate mobile backend/database, offline-first database synchronization                             | Keep one source of truth and a focused Web control centre. Native Mobile is in v1, but these expansions are not. |
| Custom Swift/Kotlin applications, Flutter, Ionic or Capacitor clients; replacement auth providers or JWT migration | Use Expo/React Native and the existing custom server-side session architecture.                                  |
| Advanced recommendation models                                                                                     | Insufficient data; basic filters plus AI search are enough.                                                      |
| Multi-country / multi-state operations                                                                             | Architecture supports it; operations do not.                                                                     |
| Advanced routing (walking/road distance and time)                                                                  | Use straight-line distance, labelled as such.                                                                    |
| Complex analytics dashboards for owners or admin                                                                   | Simple counts are enough at launch.                                                                              |
| Paid featured placements                                                                                           | Needs supply and demand first; adds ranking-trust risk.                                                          |
| Tenant paid plans                                                                                                  | Essential discovery stays free.                                                                                  |
| In-app payments of any kind (including coupon redemption)                                                          | No monetised product exists yet.                                                                                 |
| Persistent AI property assistant for every listing                                                                 | Ship AI search first; assistants come after listing data is dependable.                                          |

## 6. Core features: must-have vs nice-to-have

Classification key: **M** = Must-have for v1 launch. **S** = Should-have (include if time allows, can ship a fast-follow). **N** = Nice-to-have (post-v1).

### 6.1 Accounts, access and security

| Feature                                          | Priority | Notes                                                                          |
| ------------------------------------------------ | -------- | ------------------------------------------------------------------------------ |
| Guest browsing of listings and details           | **M**    | No login wall for discovery                                                    |
| Tenant and owner signup, login, logout           | **M**    | Custom server-side sessions, Argon2id hashing                                  |
| Email verification                               | **M**    | Only enabled once a compliant Brevo sender exists; show honest delivery status |
| Password reset                                   | **M**    | Single-use, short expiry; revokes all sessions                                 |
| Role-based permissions enforced on the backend   | **M**    | Guest, tenant, pending host, approved host, admin                              |
| Admin login with TOTP MFA                        | **M**    | Not bypassable through normal login                                            |
| Rate limiting on signup, login, reset, enquiries | **M**    | Upstash Redis; stricter fallback if Redis is down                              |
| Immediate session revocation on suspension       | **M**    |                                                                                |
| Audit log for sensitive admin actions            | **M**    |                                                                                |
| Account suspension with appeal route             | **S**    | Manual appeal by email is acceptable in v1                                     |

### 6.2 Owner onboarding and listing management

| Feature                                                         | Priority | Notes                                                                                                       |
| --------------------------------------------------------------- | -------- | ----------------------------------------------------------------------------------------------------------- |
| Host application with verification documents                    | **M**    | Collect only what is required; private storage; no unnecessary Aadhaar copies                               |
| Admin approval or rejection of hosts with reason                | **M**    | Pending hosts cannot publish                                                                                |
| Property creation wizard (7 steps)                              | **M**    | Basic info, address and map, rooms, amenities, rules, photos, review and submit                             |
| Multiple room configurations per property                       | **M**    | One building = one listing, several configurations                                                          |
| Draggable map pin for location                                  | **M**    | Shared backend Geoapify autocomplete; MapLibre GL JS on Web, a React Native compatible map on Native Mobile |
| Photo upload with validation                                    | **M**    | Cloudinary or similar; size and type limits                                                                 |
| Listing moderation (approve, reject, request changes)           | **M**    | Nothing goes public automatically                                                                           |
| Manual availability updates                                     | **M**    | Core to trust in freshness                                                                                  |
| Property revisions (live version stays until revision approved) | **S**    | For v1, a simple edit-and-resubmit flow is acceptable if revisions slip                                     |
| AI listing description generator                                | **S**    | Draft only; owner must confirm; never invents facts                                                         |
| Owner dashboard: enquiries, visit requests, listing counts      | **M**    | Responsive Web and Native Mobile                                                                            |
| Listing performance (views, enquiries)                          | **N**    | Simple counts at most                                                                                       |
| Multi-property management at scale                              | **N**    | Single owner with a few listings is enough                                                                  |

### 6.3 Tenant discovery

| Feature                                                                                           | Priority | Notes                                                                |
| ------------------------------------------------------------------------------------------------- | -------- | -------------------------------------------------------------------- |
| Homepage with hero, search, browse by location                                                    | **M**    | Content managed from the admin dashboard                             |
| Filter search: location, university, budget, sharing type, gender policy, amenities, availability | **M**    | Works with AI fully off                                              |
| Result cards (image, area, rent, sharing, amenities, verified, availability)                      | **M**    | Owner claims not shown as platform-confirmed facts                   |
| Map and list views with mobile toggle                                                             | **M**    | Filters in a bottom sheet on mobile                                  |
| Distance from selected institution                                                                | **M**    | Straight-line, labelled as such                                      |
| Property details page                                                                             | **M**    | Rooms, rent, deposit, rules, amenities, approximate location         |
| Sorting (relevance, price, recently updated, distance)                                            | **M**    | No silent paid ranking                                               |
| AI natural-language search                                                                        | **S**    | AI returns structured filters only, backend validates; never raw SQL |
| Save favourites                                                                                   | **M**    | Requires login                                                       |
| Compare properties (up to a small limit)                                                          | **S**    |                                                                      |
| Property AI assistant (Q&A on a listing)                                                          | **N**    | Post-v1 once listing data is dependable                              |
| Personalised recommendations                                                                      | **N**    |                                                                      |
| Search history                                                                                    | **N**    |                                                                      |

### 6.4 Visits, communication and trust

| Feature                                                 | Priority | Notes                                                                                                 |
| ------------------------------------------------------- | -------- | ----------------------------------------------------------------------------------------------------- |
| Visit request (date, time, message)                     | **M**    | The primary conversion action                                                                         |
| Owner accepts, rejects or asks to reschedule            | **M**    | Statuses: Pending, Accepted, Rejected, Reschedule requested, Confirmed, Completed, Cancelled, No-show |
| Email and in-app notifications for visit events         | **M**    | Distinguish created, queued, accepted by provider, failed                                             |
| Report a listing or user                                | **M**    | Needed for trust and moderation                                                                       |
| In-platform messaging                                   | **S**    | Visit-request message thread can cover v1 if full chat slips                                          |
| Reviews (only after a verified completed visit or stay) | **S**    | Owner cannot self-mark a visit complete; plan the rules now, ship when there is volume                |
| Visit reminders                                         | **N**    |                                                                                                       |

### 6.5 Main Admin control centre (Web-only)

| Feature                                                        | Priority | Notes                                                                        |
| -------------------------------------------------------------- | -------- | ---------------------------------------------------------------------------- |
| Host application review and approval                           | **M**    |                                                                              |
| Listing moderation queue                                       | **M**    |                                                                              |
| Dynamic locations and institutions                             | **M**    | Add or edit areas and universities without a deployment                      |
| Dynamic amenities, categories, room types                      | **M**    | Soft-deactivation to preserve history                                        |
| Homepage content editor (hero, banners, sections) with preview | **S**    | A simple version of the editor is enough for launch                          |
| Reports and moderation tools                                   | **M**    | Statuses: Pending, Under Investigation, Action Required, Resolved, Dismissed |
| Announcements                                                  | **S**    |                                                                              |
| Featured properties                                            | **N**    | Defer until there is supply; always labelled                                 |
| Offers and coupons                                             | **N**    | Data structure only; no active redemption in v1                              |
| AI settings (enable switch, limits, provider)                  | **S**    | Emergency disable switch is a must if AI search ships                        |
| Subscription plan configuration                                | **N**    | Architecture only                                                            |
| Dashboard metrics from real data                               | **M**    | Hosts pending, listings by status, visits, reports                           |

### 6.6 Platform quality

| Feature                                                                    | Priority | Notes                                                                                                                                             |
| -------------------------------------------------------------------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| Responsive Web and native Android/iOS experiences                          | **M**    | Shared brand and business rules; separate platform UI components                                                                                  |
| Tenant profile and Owner profile/account                                   | **M**    | Own profile and account state on both clients; server-validated edits                                                                             |
| Native navigation and device integration                                   | **M**    | Safe areas, back gestures, keyboard avoidance, native share sheet, safe deep links, pull-to-refresh                                               |
| Network resilience                                                         | **M**    | Loading, empty, offline and retry states; no offline writes or local database synchronization                                                     |
| Friendly wake-up state while the Render free-tier backend restarts         | **M**    | Also the reason to upgrade hosting before public launch                                                                                           |
| Basic SEO (titles, meta, clean URLs, sitemap, no private listings indexed) | **S**    | Pages are client-rendered (React + Vite), so measure search visibility before pre-rendering                                                       |
| Share-preview link for WhatsApp and social apps (small FastAPI route)      | S        | Client-rendered pages cannot show a title, description and photo in link previews; the route serves them and sends people on to the property page |
| Accessibility basics (labels, contrast, keyboard, screen readers)          | **M**    | Preserve T-04 rules; VoiceOver/TalkBack, scalable text, reduced motion and at least 44pt iOS / 48dp Android touch targets                         |
| Help, policies, Terms, Privacy                                             | **M**    | Required before public launch                                                                                                                     |

## 7. User flows

The following Tenant and Owner flows apply to Web and Native Mobile; the Admin flow applies only to Web. Native Tenant navigation: **Explore, Saved, Visits, Notifications, Profile**. Native Owner navigation: **Overview, Properties, Visits, Availability, Account**. Authentication returns to an allowed intended destination without bypassing backend access checks. Native email links use verified Android App Links / iOS Universal Links with a Web fallback; session tokens never travel in links.

Native Tenant v1 includes public browsing, homepage/discovery, search and filters, property details, signup/login, email verification, password reset, saved properties, visit requests and statuses, notifications, profile and property sharing. Native Owner v1 includes authentication, application and status, property wizard and management, photo uploads, availability, visits, notifications and profile/account. The Owner notification inbox is reachable from Overview/Account. In-app notifications are required; optional push delivery uses Expo Notifications when configured and never gates the inbox or core journeys.

### 7.1 Student: from search to visit

1. **Land on homepage / Explore** in Web or Native Mobile via a campus referral, WhatsApp share or search engine. See the value proposition and a search box without signing in.
2. **Search.** Choose area or university (for example ICFAI), budget and sharing type, or type a plain-language query such as _"Girls' PG near Tadong with Wi-Fi under ₹7,000"_. If AI is unavailable, the same filters still work.
3. **Browse results** in list or map view. Cards show rent, distance, amenities and a verification indicator.
4. **Open a property.** Review photos, room configurations, rent, deposit, rules, food policy and approximate location.
5. **Save or compare** (prompted to register or log in if a guest).
6. **Register or log in.** Verify email.
7. **Request a visit** with a preferred date, time and a short message.
8. **Receive notification** when the owner responds (accept, reject or suggest another time).
9. **Visit the property** in person. Confirm that the visit happened.
10. **Decide and leave feedback** (later phases: review, report any inaccuracies).

**Edge cases:** no results (suggest widening filters or nearby areas); owner does not respond (reminder to owner, tenant can request elsewhere); listing becomes unavailable (clear status, similar properties suggested).

### 7.2 Owner: from signup to first visit request

1. Visit "List Your Property"; register as an owner and verify email.
2. Complete owner profile and **submit a host application** with verification documents. Status becomes _Pending_.
3. Admin reviews and **approves or rejects with a reason**. Owner is notified; if rejected they can correct and resubmit.
4. Approved owner opens the **property wizard**: basic info, address and map pin, room configurations and pricing, amenities, house rules, photos, review.
5. **Submit for moderation.** Listing status becomes _Pending Review_.
6. Admin approves, rejects or requests changes. Owner is notified.
7. Listing goes **live** and appears in search.
8. Owner keeps **availability** up to date and receives **visit requests** by email and the Web dashboard or native inbox. Native photo selection and uploads use backend-issued signed URLs.
9. Owner **accepts, rejects or reschedules** each request and keeps the response time short.

### 7.3 Admin: moderation loop

1. Log in with password plus TOTP.
2. Open dashboard; see pending host applications, pending listings and open reports.
3. Review documents or listing content; approve, reject or request changes with a recorded reason.
4. Handle reports: investigate, hide or remove content, suspend where justified, record the decision.
5. Update locations, amenities or homepage content as needed, with no developer involvement.
6. All sensitive actions land in the audit log.

## 8. MVP definition

### 8.1 MVP statement

A marketplace on responsive Web, native Android and native iOS, backed by one FastAPI API and PostgreSQL source of truth, where ICFAI-area students can browse and filter **moderated** PG listings, see them on a map, save them and **request visits**, and where approved owners can list rooms, keep availability current and respond to requests, all moderated through a Web-only Admin control centre.

### 8.2 In scope (v1 launch)

- Web and Native Mobile public browsing, homepage/discovery, filters, list and platform-appropriate map, property details and sharing.
- Tenant profile and Owner profile/account in both clients; native navigation and network/error states.
- Tenant and owner accounts; email verification; password reset; role-based access; admin MFA.
- Host application, host approval, property wizard with room configurations, photo upload, listing moderation.
- Saved properties and visit requests with status tracking and notifications.
- Reports and moderation; audit logging.
- Admin dashboard: hosts, listings, locations and institutions, amenities and categories, basic homepage content, reports.
- AI natural-language search, behind an admin switch and usage limits, **only if** a provider is configured and costs are acceptable.

### 8.3 Out of scope for v1

Everything in section 5.2, plus: reviews and full real-time chat if they threaten the launch date, comparison view if needed, and property AI assistants.

### 8.4 Launch readiness gates

The MVP does not launch publicly until:

- [ ] A target number of **real, moderated listings** exist near ICFAI (suggested: at least 25 to 30 across 4 or more areas).
- [ ] Always-on paid backend hosting, database backups and error monitoring are in place (the free tier's sleep behaviour is not acceptable for a public launch).
- [ ] Transactional email has a verified sender and delivery status is reported honestly.
- [ ] Role, session and cross-owner access tests pass (an owner cannot reach another owner's data; a revoked session stops working immediately).
- [ ] Privacy policy, terms and a data-handling review for verification documents are complete under applicable Indian data-protection requirements.
- [ ] Web, Android and iOS core Tenant/Owner journeys pass; browser keyboard and native VoiceOver/TalkBack, safe areas, enlarged text, back navigation and network failure behaviour are checked.
- [ ] Web cookie/CSRF and Native Mobile SecureStore/bearer expiry and revocation tests pass against the same API; bundles and telemetry contain no secrets or session tokens.
- [ ] Android/iOS release signing, distribution accounts, platform privacy disclosures, app-link domains and store review/release steps are owned and tested. An unavailable local iOS simulator is recorded as pending validation, not a launch pass.
- [ ] In-app notifications work with push permission denied; any enabled push delivery has privacy-safe payloads and device testing.
- [ ] At least 10 owners have been onboarded in person and 10 students have completed a real search-to-visit-request test.

### 8.5 Suggested delivery sequence

1. **T-01 — completed:** monorepo, tooling and CI.
2. **T-02 — completed:** FastAPI foundation.
3. **T-03 — completed:** database foundation and first migration.
4. **T-04 — completed:** React Web foundation and shared design decisions.
5. **T-04M — planned:** Expo/React Native mobile foundation, with mocked integration boundaries only.
6. **T-05 — planned:** deployment/environment foundation for Web/API and native development, preview/staging and production configuration.
7. **T-06 onward:** shared auth and permissions, Web/Native Mobile auth and profiles, master data, Owner and Tenant journeys, visits and notifications; Web-only Admin workflows. Follow the Feature Ticket List dependencies, with mobile companion tickets reusing backend work.
8. **Optional enhancements, security testing and controlled launch:** keep AI and other Should-haves subordinate to the three-client core journeys and launch gates.

## 9. Functional requirements (key rules)

- **Listing model:** one physical property = one listing; sharing and pricing arrangements = room configurations. Used consistently in the database, owner dashboard and any future plan limits.
- **Moderation:** new listings never go public automatically. Approved listings stay live while an important-field revision awaits approval.
- **Location privacy:** full private address is stored separately from public address and map coordinates. Public view shows approximate or owner-chosen location within policy.
- **Distance:** shown as straight-line only and labelled so. Never described as road or walking distance.
- **Search:** AI converts text to structured filters; the backend validates them and queries the database. AI never writes SQL or changes records.
- **AI failure:** search, property pages, visit requests, listing edits and notifications must all continue if the AI provider is down.
- **Reviews (when enabled):** only after a tenant-confirmed completed visit or stay. Owners cannot grant review eligibility; legitimate negative reviews cannot be removed because an owner disagrees.
- **Ranking and promotion:** no silent paid boosts. Any sponsored placement is labelled; verification is independent of payment.
- **Access control:** every protected endpoint enforces identical role permissions and RLS context for both clients; route guards are only UX. Sessions remain random opaque tokens, stored as SHA-256 hashes in PostgreSQL: Web uses an HttpOnly Secure-in-production SameSite=Lax cookie plus Origin/CSRF checks; Native Mobile stores the raw token only in Expo SecureStore and sends it as an explicit HTTPS bearer credential. No JWT migration.
- **Notifications:** record created, queued, accepted by provider and failed as separate states. Never mark an email delivered because it was merely sent.
- **Admin configurability:** locations, institutions, amenities, categories and homepage content are editable without deployment. Security settings and secrets are not.

## 10. Success metrics

Targets below are **proposed starting hypotheses** to be agreed and adjusted after the first four to six weeks of real data. They are not guarantees.

### 10.1 North-star metric

**Visit requests that reach "Completed" per week** around ICFAI and Gangtok. This captures supply, discovery and trust in one number.

### 10.2 Supply health

| Metric                                             | Proposed launch target          |
| -------------------------------------------------- | ------------------------------- |
| Approved owners                                    | 10 or more at controlled launch |
| Live, moderated listings                           | 25 to 30 or more                |
| Listings with availability updated in last 14 days | 80% or more                     |
| Areas with at least 3 live listings                | 4 or more                       |
| Listing approval turnaround                        | Under 48 hours                  |

### 10.3 Demand and conversion

| Metric                         | What it tells us          | Proposed target                  |
| ------------------------------ | ------------------------- | -------------------------------- |
| Tenant registrations           | Reach                     | Track weekly growth              |
| Search to property view        | Quality of results        | 40% or more                      |
| Property view to visit request | Listing trust and quality | 8 to 12%                         |
| Visit request acceptance rate  | Owner engagement          | 70% or more                      |
| Visit completion rate          | Real-world follow-through | 50% or more of accepted requests |

### 10.4 Trust and operations

| Metric                                        | Proposed target           |
| --------------------------------------------- | ------------------------- |
| Owner median response time to visit requests  | Under 24 hours            |
| Listing accuracy (spot-check against reality) | 90% or more correct       |
| Report resolution time                        | Under 72 hours            |
| Reports per 100 listings                      | Track; investigate spikes |

### 10.5 AI health (only if AI search ships)

| Metric                                                     | Proposed target              |
| ---------------------------------------------------------- | ---------------------------- |
| Share of AI searches that return at least one valid result | 85% or more                  |
| AI cost per active user                                    | Within an agreed monthly cap |
| Thumbs-down or correction rate on AI results               | Track; review weekly         |

### 10.6 Qualitative signals

- Short interviews with the first 10 owners and 15 to 20 students after their first visit.
- Would students use it again next semester? Would owners keep updating availability without being chased?

### 10.7 Decision rule for v2

Move to subscriptions, featured placements or expansion only when supply stays fresh without manual chasing, visit completions grow for several consecutive weeks, and owners say the platform brings them tenants they would not otherwise have reached.

## 11. Risks and mitigations

| Risk                                                               | Impact                                                    | Mitigation                                                                                                                                   |
| ------------------------------------------------------------------ | --------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| **Cold-start supply:** too few listings at launch                  | Students see an empty marketplace and leave               | Onboard owners in person before public launch; do not launch publicly below the listing gate                                                 |
| **Stale availability**                                             | Wasted visits, lost trust                                 | Prompt owners to confirm availability; show "last updated"; demote stale listings                                                            |
| **Owners disengage / low digital literacy**                        | Poor data quality                                         | Mobile-first wizard, in-person onboarding, minimal required fields                                                                           |
| **Fake or inaccurate listings**                                    | Trust collapse                                            | Document review, moderation before publishing, report flow, spot checks                                                                      |
| **Off-platform deals** (parties exchange numbers and skip the app) | Hard to measure and monetise                              | Accept early; value comes from discovery and trust; measure visit requests, not payments                                                     |
| **Free-tier hosting cold starts**                                  | Poor first impression                                     | Friendly wake-up state for testing; paid always-on backend before public launch                                                              |
| **Email sender not accepted**                                      | Verification and reset fail                               | Verify a proper sender before enabling; honest delivery status                                                                               |
| **AI cost or errors**                                              | Budget overruns, wrong results                            | Strict limits, emergency off switch, structured-filter validation, AI optional                                                               |
| **Data protection for verification documents**                     | Legal and reputational harm                               | Private storage, minimal collection, access logging, privacy review before launch                                                            |
| **Native delivery and store review**                               | Android/iOS testing, signing and review can delay release | Prefer free/open-source tooling and free development tiers; budget unavoidable signing/store costs and validate both platforms before launch |
| **Lost devices, network gaps and permission denial**               | Session exposure or misleading stale state                | SecureStore, immediate server revocation, privacy-safe notifications, clear retries and no offline write queue                               |
| **Client drift**                                                   | Different status or permission behaviour across clients   | Shared API contracts and backend validation; gradual platform-neutral reuse and cross-client journey checks                                  |
| **Scope creep (admin and AI features)**                            | Delays the launch                                         | Hold the line on section 5.2; ship the Should-haves only after Must-haves pass testing                                                       |

## 12. Open questions and decisions needed

1. **Clients:** Decided: retain React + Vite + Tailwind CSS + React Router for Web; add React Native + Expo + TypeScript + Expo Router + NativeWind for Android/iOS. Admin stays Web-only. Web pages are client-rendered, so measure search visibility before considering pre-rendering, and use the FastAPI share-preview route so shared property links show a preview in WhatsApp.
2. **Verification definition:** exactly what earns a "Verified" badge (document check only, or also an in-person visit)? Needs a written, consistent standard.
3. **Reviews timing:** ship in v1 or the first fast-follow? Recommendation: fast-follow, with the eligibility rules designed now.
4. **Messaging scope:** full chat, or a visit-request message thread for v1? Recommendation: the thread.
5. **AI provider and monthly budget:** is Gemini's pricing and quota acceptable for expected volume?
6. **Email sender:** which authenticated sender will be used for Brevo given that a free Gmail address may not qualify?
7. **Owner acquisition plan:** who onboards the first 10 owners, and what do they get in return (free listing for a fixed period)?
8. **Subscription pricing:** the proposed ₹499 and ₹999 per month must be tested with real owners before any activation.
9. **Legal review:** Terms, Privacy Policy and a data-protection review for identity documents before public launch.

10. **Native map provider/library:** select an Expo-compatible option in T-26M after checking accessibility, platform support, attribution, privacy and development-tier costs. Geoapify geocoding stays behind FastAPI.
11. **Mobile release operations:** final Android package/iOS bundle identifiers, verified link domains, supported OS versions, signing/store account ownership and physical-device coverage are decided in T-04M/T-05 and checked at T-46.
12. **Push rollout:** decide whether push delivery is enabled at launch in T-31M; the shared in-app inbox and email remain required regardless of permission or provider configuration.
13. **Device management:** a user-facing session/device list and logout-all UI are a later enhancement; server-wide revocation for password reset, suspension and ban remains required now. Any extra session metadata uses a new migration.

## 13. Appendix

### 13.1 Proposed future owner plans (not in v1)

| Plan               | Price (proposed) | Intended scope                                                  |
| ------------------ | ---------------- | --------------------------------------------------------------- |
| Owner Starter      | ₹0               | 1 active listing, 1 room configuration, basic dashboard         |
| Owner Growth       | ₹499/month       | Up to 3 listings, AI descriptions, assistant, analytics         |
| Owner Professional | ₹999/month       | Up to 10 listings, advanced analytics, limited featured credits |

### 13.2 Visit status model

Pending → Accepted → Confirmed → Completed. Alternative paths: Rejected, Reschedule requested, Cancelled, No-show.

### 13.3 Technical stack (reference)

One FastAPI backend; React 19 + Vite Web with Tailwind; React Native + Expo Native Mobile (Android/iOS) with TypeScript, Expo Router, NativeWind, TanStack Query, React Hook Form, Zod, SecureStore, Lucide React Native, Expo Image and Sentry React Native; Expo Notifications when enabled and EAS Build when needed; Neon PostgreSQL (pgvector only where needed); Upstash Redis; Brevo; Geoapify backend geocoding, MapLibre GL JS on Web and a native-compatible map selected at T-26M; Cloudinary for images; private object storage for documents; configurable AI provider; Render hosting (upgrade to an always-on plan before public launch).
