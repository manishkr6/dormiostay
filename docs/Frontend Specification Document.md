# DormioStay — Frontend Specification Document

*Version 1.0 | November 2026 | Design system + API and integration specification*

**Scope:** Part A defines how DormioStay looks and behaves (colours, type, spacing, components, screens). Part B defines every external service the app calls, what is sent, and what comes back. Companion documents: PRD, Technical Architecture, Security and Access.

**Verify before coding:** third-party endpoint details below are accurate to the best of current knowledge but providers change them. Check each provider's current documentation when you implement.

---

# PART A — DESIGN SYSTEM

## A1. Design principles

1. **Trust first.** Calm, clean, photography-led. Verification and real information are visually prominent; promotional content is always labelled.
2. **Mobile first.** Most students and many owners use phones. Design the phone layout first, then enlarge.
3. **Premium but approachable.** Warm ivory backgrounds, deep indigo structure, one confident violet for actions, coral as a sparing accent.
4. **One system, three experiences.** Public/tenant site, owner dashboard and admin console share components but differ in navigation and density.
5. **Honest states.** Every screen has designed loading, empty, error and 'offline/waking up' states.

## A2. Colour palette

### Brand colours

| Token | Name | Hex | Use |
| --- | --- | --- | --- |
| `--color-primary-900` | Deep Indigo | `#312E81` | Headers, footer, strong headings, selected navigation |
| `--color-primary-600` | Royal Violet | `#6D4AFF` | Primary buttons, links, focus accents, active filters |
| `--color-primary-100` | Soft Lavender | `#EDE9FE` | Soft backgrounds, selected chips, hover tints |
| `--color-bg` | Warm Ivory | `#FAF9F6` | Main page background |
| `--color-text` | Charcoal | `#171923` | Primary text |
| `--color-text-muted` | Slate | `#64748B` | Secondary text, captions, placeholders |
| `--color-accent` | Coral | `#F97360` | Small accents, promos, 'new' tags |
| `--color-highlight` | Amber | `#F59E0B` | Star ratings, highlights, warning backgrounds |

### Supporting colours

| Token | Hex | Use |
| --- | --- | --- |
| `--color-surface` | `#FFFFFF` | Cards, inputs, modals |
| `--color-surface-muted` | `#F3F1EC` | Table headers, subtle panels |
| `--color-border` | `#E5E2DA` | Dividers, card and input borders |
| `--color-border-strong` | `#CFCABD` | Input hover, stronger outlines |
| `--color-primary-700` | `#5538D6` | Primary button pressed state |
| `--color-primary-800` | `#43309F` | Dark violet text on lavender |
| `--color-success` | `#16865B` | Verified, accepted, saved (never used as the brand colour) |
| `--color-success-bg` | `#E4F5EC` | Success banners |
| `--color-warning-text` | `#92400E` | Warning text (amber is too light for text) |
| `--color-warning-bg` | `#FEF3C7` | Warning banners |
| `--color-error` | `#DC2626` | Errors, destructive buttons |
| `--color-error-bg` | `#FEE2E2` | Error banners |
| `--color-info` | `#2563EB` | Information |
| `--color-info-bg` | `#DBEAFE` | Info banners |

### Colour rules

- **Body text** is Charcoal on Ivory or White. **White text** is allowed on Deep Indigo and Royal Violet.
- **Do not put white text on Coral or Amber** (contrast is too low); use Charcoal text on those.
- Amber and Coral are never used for small body text.
- Status is never conveyed by colour alone: always include an icon or label.
- Verify all pairs with a contrast checker; target at least 4.5:1 for normal text and 3:1 for large text and UI borders.
- Dark mode is **out of scope for v1**.

### CSS variable and Tailwind setup

```css
:root {
  --color-primary-900: #312E81; --color-primary-800: #43309F;
  --color-primary-700: #5538D6; --color-primary-600: #6D4AFF;
  --color-primary-100: #EDE9FE;
  --color-bg: #FAF9F6; --color-surface: #FFFFFF; --color-surface-muted: #F3F1EC;
  --color-text: #171923; --color-text-muted: #64748B;
  --color-border: #E5E2DA; --color-border-strong: #CFCABD;
  --color-accent: #F97360; --color-highlight: #F59E0B;
  --color-success: #16865B; --color-error: #DC2626; --color-info: #2563EB;
  --radius-sm: 8px; --radius-md: 12px; --radius-lg: 16px; --radius-full: 9999px;
  --shadow-card: 0 1px 2px rgba(23,25,35,.06), 0 4px 12px rgba(23,25,35,.06);
  --shadow-pop: 0 8px 30px rgba(23,25,35,.14);
}
```

Map these tokens into `tailwind.config.ts` (`colors.primary`, `colors.ink`, `colors.accent`, etc.). No hard-coded hex values in components.

## A3. Typography

| Role | Font | Notes |
| --- | --- | --- |
| Headings | **Playfair Display** (alternative: DM Serif Display), weights 600–700 | Gives the premium editorial feel. Use for page titles, hero, section headings |
| Body and UI | **Inter**, weights 400, 500, 600 | Buttons, forms, tables, navigation, body copy |
| Numbers in tables | Inter with tabular numbers | Prices line up |

Load via Google Fonts or self-hosted `@font-face` (in `public/fonts/` or `<link>` in `index.html`), with `font-display: swap` to avoid layout shift, and a fallback stack (Georgia, serif / system-ui, sans-serif).

| Style | Mobile size / line height | Desktop size / line height | Font |
| --- | --- | --- | --- |
| Display (hero) | 34 / 40 | 56 / 64 | Playfair 700 |
| H1 | 28 / 36 | 40 / 48 | Playfair 700 |
| H2 | 24 / 32 | 30 / 38 | Playfair 600 |
| H3 | 20 / 28 | 22 / 30 | Inter 600 |
| H4 | 18 / 26 | 18 / 26 | Inter 600 |
| Body | 16 / 24 | 16 / 26 | Inter 400 |
| Small | 14 / 20 | 14 / 20 | Inter 400/500 |
| Caption | 12 / 16 | 12 / 16 | Inter 500, slate |
| Button | 15 / 20 | 15 / 20 | Inter 600 |
| Price | 20 / 28 | 24 / 32 | Inter 700, indigo |

Body text is never smaller than 16px on mobile inputs (prevents mobile zoom). Maximum reading line length about 70 characters. Admin tables may use 14px.

## A4. Spacing, layout and responsiveness

- **Spacing scale (4px base):** 4, 8, 12, 16, 20, 24, 32, 40, 48, 64, 80. Page sections use 48 (mobile) to 80 (desktop) vertical spacing; card padding 16 (mobile) to 20 (desktop).
- **Breakpoints:** mobile up to 639px; tablet 640–1023px; desktop 1024–1439px; wide 1440px and up.
- **Containers:** public site max width 1200px, centred, side padding 16 (mobile) / 24 (tablet) / 32 (desktop). Admin console is fluid with a fixed sidebar (260px).
- **Grid:** 4 columns on mobile, 8 on tablet, 12 on desktop, 24px gutters on desktop and 16px on mobile.
- **Property grid:** 1 column mobile, 2 tablet, 3 desktop (4 on wide with filters collapsed).
- **Touch targets:** at least 44 × 44px for all tappable items; 8px minimum gap between them.
- **Corner radius:** inputs and buttons 12px, cards 16px, chips and avatars full-round, modals 20px top corners on mobile.
- **Elevation:** cards use `--shadow-card`; popovers and modals use `--shadow-pop`; avoid heavy shadows.
- **Motion:** 150–250ms ease-out for hover, expand and sheet transitions. Respect `prefers-reduced-motion` by removing movement.
- **Icons:** Lucide icons, 20px default, 1.75px stroke, always paired with text for important actions.
- **Imagery:** property cards use 4:3 photos; hero and gallery 16:9; always include alt text and a blurred placeholder while loading.

## A5. Component specifications

### Buttons

| Variant | Look | Use |
| --- | --- | --- |
| **Primary** | Royal Violet fill, white text, 48px tall (mobile), radius 12, hover Violet 700, pressed darker | One per view: Search, Request Visit, Submit |
| **Secondary** | White fill, 1px Indigo border, indigo text | Alternative actions |
| **Tertiary / ghost** | No border, violet text, lavender hover | Low-emphasis, 'Cancel' |
| **Destructive** | Red fill, white text | Suspend, Reject (always with confirmation) |
| **Accent** | Coral fill, Charcoal text | Rare promos only |
| **Icon button** | 44px square, transparent, lavender hover | Save (heart), close |

States for all: default, hover, pressed, focus (2px violet outline with 2px offset), disabled (50% opacity, no hover), loading (spinner replaces label, width unchanged, disabled). Sizes: small 36px (admin tables only), medium 44px, large 52px (hero CTA).

### Inputs and form controls

- Height 48px, radius 12, 1px border `--color-border`, white fill, 16px text, slate placeholder.
- Always a visible label above the field (never placeholder-only). Optional helper text below in slate; error text in red with an alert icon beneath the field.
- States: default, hover (stronger border), focus (2px violet ring), error (red border and message), disabled (muted fill), success (subtle green check where useful).
- Variants: text, email, password (show/hide toggle), phone (+91 prefix), number/currency (₹ prefix), textarea (min 4 rows, character counter), select, multi-select chips (amenities), checkbox, radio cards (room sharing type), switch, date and time picker, slider (budget with min/max thumbs), file upload dropzone (drag area, preview, remove, progress bar).
- Validate on blur and on submit; summarise errors at the top of long forms and move focus to the first error.

### Cards

| Card | Structure |
| --- | --- |
| **PropertyCard** | 4:3 cover image with heart (save) top right; badges top left (Verified, Featured/Sponsored if applicable); below: name (H4, 1 line), area and distance ('1.2 km from ICFAI, straight-line'), amenity icons (max 4 + '+3'), room line ('From ₹4,500 / month'), availability tag ('3 beds left' / 'Currently full'), 'Updated 2 days ago' in caption. Whole card is a link. |
| **StatCard** (dashboards) | Label, big number, small trend note, icon in lavender circle |
| **RoomConfigCard** | Sharing type, rent (INTEGER rupees, not NUMERIC), deposit (INTEGER rupees, not NUMERIC), beds available, extra charges, select/request button |
| **VisitCard** | Property thumbnail, date/time in IST, status badge, primary action by status (Cancel / Reschedule / Confirm) |
| **ListingStatusCard** (owner) | Status badge, last updated, next required action |

Cards: white, radius 16, 1px border, `--shadow-card`; hover lifts slightly on desktop. Skeleton card matches the final layout.

### Badges and chips

- **Verified** (green check icon + text, success colours): only for listings that passed admin verification; tooltip explains the standard. Never appears on unverified listings.
- **Featured / Sponsored** (amber outline with label): always visible when paid or promoted.
- Status badges: Pending (amber), Approved/Accepted/Confirmed (green), Rejected/Cancelled (red), Changes requested (blue), Draft (grey).
- Filter chips: 36–40px, radius full, lavender fill when selected with check icon.

### Modal, drawer and bottom sheet

- **Modal (desktop):** centred, max width 520px (forms 640px), radius 20, dim overlay, focus trapped, Esc and close button dismiss. Title, body, footer actions (primary right).
- **Bottom sheet (mobile):** slides from bottom, grab handle, max height 90vh, scrolls inside, sticky action bar. Used for filters, visit request, sort, confirmations.
- **Detail drawer (admin):** right side, 480–640px, for reviewing a host or listing without leaving the table.
- **Confirmation dialog:** required for destructive actions; states exactly what will happen; destructive button names the action ('Suspend listing'). Admin actions that need a reason include a required reason box.

### Navigation

- **Public header:** logo, Explore PGs, Locations, How it works, For owners, Help; right side Login / Sign up (or avatar menu). Collapses to a hamburger drawer on mobile. Sticky with subtle shadow on scroll.
- **Mobile bottom bar (logged-in tenants):** Explore, Saved, Visits, Messages, Profile.
- **Owner dashboard:** left sidebar on desktop, bottom bar on mobile (Overview, Properties, Visits, Availability, Account).
- **Admin console:** fixed left sidebar (Dashboard, Hosts, Listings, Reports, Locations, Content, Settings, Audit log), top bar with search and profile.
- Footer: indigo background, links to About, Contact, Help, Terms, Privacy, Community and Listing guidelines, dormiostay@gmail.com, map attribution note where relevant.

### Other components

- **Toast:** bottom centre (mobile) / top right (desktop), auto-dismiss after 5 seconds, icon + message, success/info/error, errors with a retry action when applicable. Never used as the only error feedback for forms.
- **Tabs and segmented control:** list/map toggle, dashboard tabs.
- **Data table (admin):** sticky header, sortable columns, row selection, pagination (25 rows default), filter bar, status badges, row actions menu, responsive collapse to cards on small screens.
- **Stepper (owner wizard):** 7 steps, progress bar on mobile ('Step 3 of 7'), save draft automatically on each step, back and next buttons sticky at the bottom.
- **Map components:** MapLibre canvas with property pins (violet), selected pin enlarged, cluster circles with counts, popup mini-card, attribution text bottom right, 'Search this area' button, draggable pin for owners.
- **Photo gallery:** one large image with a 4-photo grid on desktop; swipeable carousel with count on mobile; full-screen lightbox.
- **Skeletons, empty and error states:** every list has a skeleton while loading, an illustrated empty state with a next action ('No PGs match. Try a nearby area.'), and an error state with a Retry button.
- **Waking-up banner:** shown when the API takes more than 4 seconds: 'Waking things up… this can take up to a minute.'

## A6. Key screen layouts

| Screen | Layout notes |
| --- | --- |
| **Homepage** | Hero (Indigo to Violet gradient or photography) with headline, supporting text, large AI search box, two CTAs (Explore Stays, List Your Property). Then: browse by location cards, verified properties, budget-friendly, how it works (Search, Compare, Connect, Visit, Choose), owner call-to-action, footer. |
| **Search results** | Desktop: filters left or top, list left, map right (60/40). Mobile: list first, floating 'Map' button, filters in a bottom sheet, active filters as removable chips. |
| **Property page** | Client-side rendered via React Router with dynamic document title and JSON-LD structured data. Gallery, title and badges, sticky booking card on desktop (rent range, Request visit, Save); sticky bottom 'Request visit' bar on mobile. Sections: rooms and pricing, amenities, house rules, location map, how far from selected institution, report this listing, share button (copies the share-preview link). |
| **Tenant dashboard** | Overview cards, saved list, visits timeline, notifications. |
| **Owner wizard** | One step per screen, large touch inputs, auto-saved draft, preview step mirrors the public property page. |
| **Owner dashboard** | Stat cards, 'needs attention' list (pending visit requests, outdated availability), property status cards. |
| **Admin dashboard** | Counts of pending hosts, pending listings, open reports; queues first, charts second. |

## A7. Accessibility and quality rules

- Keyboard reachable everywhere; visible focus ring; logical tab order; skip-to-content link.
- Modals and drawers trap focus and return it on close. Menus and tabs follow standard arrow-key behaviour (Radix provides this).
- Every image has meaningful alt text; decorative icons are hidden from screen readers.
- Form errors are announced; colour is never the only cue.
- Text can be enlarged to 200% without breaking layouts.
- Performance targets: Largest Contentful Paint under 2.5s on a mid-range phone over 4G for search and property pages; lazy-load images and the map; paginate results at 12 per page.
- Copy tone: plain, friendly, specific. Rupee amounts as '₹6,000 / month'; dates as '14 Nov, 6:00 pm IST'.

---

# PART B — API AND INTEGRATION SPECIFICATION

## B1. Internal API conventions (frontend to FastAPI)

| Item | Rule |
| --- | --- |
| Base path | `/api/v1`, called as same-origin `/api/v1/...` from the browser (Vite dev proxy (vite.config.ts) and Render Static Site rewrite rules) |
| Format | JSON, UTF-8, ISO 8601 UTC timestamps (display in IST) |
| Auth | Session cookie sent automatically (`credentials: 'include'`) |
| CSRF | Every POST, PATCH, PUT, DELETE sends header `X-CSRF-Token` obtained from `GET /api/v1/auth/csrf` |
| Pagination | `?page=1&page_size=12`; response `{items, total, page, page_size}` |
| Success | 200 / 201 with the object, 204 for no content |
| Error body | `{ "error": { "code": "VALIDATION_ERROR", "message": "...", "fields": {"email": "..."}, "request_id": "abc123" } }` |
| Status codes | 400 bad request, 401 not signed in, 403 not allowed, 404 not found or hidden, 409 conflict, 422 validation, 429 rate-limited (with `Retry-After`), 500/503 server |

**Main endpoint groups the frontend uses:**

| Group | Examples |
| --- | --- |
| Auth | `POST /auth/signup`, `/auth/login`, `/auth/logout`, `/auth/verify-email`, `/auth/forgot-password`, `/auth/reset-password`, `GET /auth/me` |
| Public | `GET /locations`, `/institutions`, `/amenities`, `/properties` (search), `/properties/{slug}`, `/homepage` |
| Search | `GET /properties?location=&institution=&max_rent=&sharing=&gender=&amenities=&sort=&page=`, `POST /search/ai` (body `{query}` → `{filters_applied, results}`) |
| Tenant | `POST/DELETE /saved/{property_id}`, `POST /visits`, `PATCH /visits/{id}`, `GET /visits`, `GET /notifications` |
| Owner | `POST /owner/application`, `POST /owner/documents/presign`, `POST/PATCH /owner/properties`, `POST /owner/properties/{id}/images/sign`, `POST /owner/properties/{id}/submit`, `PATCH /owner/rooms/{id}/availability` |
| Admin | `GET/PATCH /admin/hosts`, `/admin/listings`, `/admin/reports`, `/admin/locations`, `/admin/content`, `/admin/settings`, `GET /admin/audit` |

## B2. Third-party services (summary)

| Service | Called from | Purpose |
| --- | --- | --- |
| Geoapify | Backend (geocoding) and browser (map tiles) | Address autocomplete, geocoding, map style and tiles |
| MapLibre GL JS | Browser (library) | Draws the map; not an API |
| Cloudinary | Backend signs; browser uploads | Public property photos |
| Cloudflare R2 | Backend presigns; browser uploads/downloads | Private verification documents |
| Brevo | Backend worker | Transactional emails and delivery events |
| Upstash Redis | Backend | Rate limits and short cache |
| Gemini API | Backend only | Turn search sentences into filters; draft descriptions |
| Neon PostgreSQL | Backend only | Primary database (connection, not an HTTP API) |
| Render | Platform | Hosting, health checks, deploy hooks |
| Sentry | Backend and browser | Error reports |

## B3. Geoapify

**What it does:** turns text into addresses and coordinates and supplies the map style. **Key handling:** use two keys: a server key (kept secret) and a browser map key restricted to your domains.

| Call | Request | Data sent | Expected response |
| --- | --- | --- | --- |
| **Autocomplete** (owner wizard) | `GET https://api.geoapify.com/v1/geocode/autocomplete` | Query: `text` (what the owner typed), `filter=countrycode:in`, `bias=proximity:88.61,27.33` (near Gangtok; longitude first), `limit=5`, `lang=en`, `apiKey` | GeoJSON FeatureCollection. Each feature has `properties` (`formatted`, `address_line1`, `suburb`, `city`, `lat`, `lon`, `place_id`) and `geometry.coordinates` \[lon, lat\] |
| **Forward geocode** (admin locations, institutions) | `GET https://api.geoapify.com/v1/geocode/search` | `text`, `filter`, `limit=1`, `apiKey` | Same GeoJSON shape; take the best feature |
| **Reverse geocode** (when the owner drags the pin) | `GET https://api.geoapify.com/v1/geocode/reverse` | `lat`, `lon`, `apiKey` | GeoJSON with the nearest address in `properties.formatted` |
| **Map style** (browser) | `GET https://maps.geoapify.com/v1/styles/osm-bright/style.json?apiKey=BROWSER_KEY` | Browser key | MapLibre style JSON; MapLibre then requests tiles itself |

**Frontend behaviour:** debounce autocomplete by 300ms, require at least 3 characters, cancel in-flight requests, cache results for the session. Prefer calling autocomplete through the backend (`GET /api/v1/geo/autocomplete?text=`) so the server key stays secret and results can be cached and rate-limited. **Errors:** 401 invalid key; 429 quota or rate limit; on any failure show the manual-pin fallback. **Attribution:** show 'Powered by Geoapify' with OpenMapTiles and OpenStreetMap contributors credit on every map. **Distance:** compute Haversine on the server; label it 'straight-line'.

## B4. Cloudinary (public property photos)

**Flow (signed direct upload):** the browser asks the backend for a signature; the backend checks the person owns the property and the image count is within limits; the browser uploads straight to Cloudinary; the backend stores the returned reference.

| Step | Request | Data sent | Expected response |
| --- | --- | --- | --- |
| 1. Get signature | `POST /api/v1/owner/properties/{id}/images/sign` (our API) | Property id, file type, file size | `{cloud_name, api_key, timestamp, signature, folder, allowed_formats, max_file_size}` |
| 2. Upload | `POST https://api.cloudinary.com/v1_1/{cloud_name}/image/upload` (multipart) | `file`, `api_key`, `timestamp`, `signature`, `folder=dormiostay/properties/{property_id}`, same signed parameters | JSON with `public_id`, `secure_url`, `width`, `height`, `format`, `bytes` |
| 3. Save | `POST /api/v1/owner/properties/{id}/images` (our API) | `public_id`, `secure_url`, alt text, order | Saved image object |
| 4. Delete | Backend only: signed `POST https://api.cloudinary.com/v1_1/{cloud_name}/image/destroy` | `public_id`, timestamp, signature, api\_key | `{ "result": "ok" }` |

**Display URLs** use transformations: `https://res.cloudinary.com/{cloud_name}/image/upload/f_auto,q_auto,w_800,c_fill,ar_4:3/{public_id}`. Use thumbnails for cards (w\_400), 1200 for gallery. **Rules:** only JPG, PNG, WebP; maximum 8 MB; minimum 800px on the long side; at most 12 photos per property. **Errors:** show 'That file couldn't be uploaded' per file and let the owner retry that photo only.

## B5. Cloudflare R2 (private verification documents)

**Flow (presigned URLs):** nothing is public. The backend generates short-lived signed links using the S3-compatible API (endpoint `https://{account_id}.r2.cloudflarestorage.com`, region `auto`, bucket private).

| Step | Request | Data sent | Expected response |
| --- | --- | --- | --- |
| Request upload link | `POST /api/v1/owner/documents/presign` (our API) | `doc_type`, `content_type`, `size` | `{upload_url, storage_key, expires_in}` (expiry about 300 seconds) |
| Upload | `PUT {upload_url}` (direct to R2) | Raw file bytes with the same `Content-Type` | `200 OK` (empty body) |
| Confirm | `POST /api/v1/owner/documents` (our API) | `storage_key`, `doc_type` | Saved document record (status 'uploaded') |
| Admin view | `GET /api/v1/admin/documents/{id}/link` | Document id | `{url, expires_in}` signed GET link; the request is written to the audit log |

**Rules:** allowed types PDF, JPG, PNG; maximum 5 MB; storage key is random (never the user's filename); CORS on the bucket allows only your website origin for PUT; no public bucket access; links expire in minutes; log every admin view.

## B6. Brevo (transactional email)

**Where used:** only the backend email worker reads `email_outbox` and sends.

| Call | Request | Data sent | Expected response |
| --- | --- | --- | --- |
| **Send email** | `POST https://api.brevo.com/v3/smtp/email` with headers `api-key: BREVO_API_KEY`, `Content-Type: application/json` | `sender` {name, email} (verified sender), `to` \[{email, name}\], `subject`, `htmlContent`, `textContent`, optional `tags` | `201` with `{ "messageId": "..." }` → outbox status becomes `accepted_by_provider` |
| **Delivery events** (webhook into `POST /api/v1/webhooks/brevo`) | Brevo calls us | Event type (for example delivered, bounce, blocked, spam), recipient email, message id, timestamp | We return `200` quickly and update the outbox row to `delivered` or `failed` |

**Emails to build:** verify email, reset password, host application submitted/approved/rejected, listing submitted/approved/rejected/changes requested, visit requested/accepted/rejected/rescheduled, new message. Keep each short, with one clear button, plain-text fallback, and the contact address in the footer. **Errors:** 400 invalid payload (do not retry), 401 bad key (alert you), 429 or 5xx (retry with increasing delays, up to 5 attempts, then mark `failed`). Secure the webhook with a secret token in the URL or header and check it.

## B7. Upstash Redis

**Where used:** backend rate limiting and small caches (geocoding results, homepage data). Connect through the standard TLS Redis URL from the backend, or Upstash's REST API (`POST https://{endpoint}` with header `Authorization: Bearer {token}` and a JSON command array).

| Purpose | Commands | Key pattern | Expected result |
| --- | --- | --- | --- |
| Count attempts | `INCR key`, then `EXPIRE key 900` on first hit | `rl:login:ip:{ip}`, `rl:login:email:{hash}` | Integer count; block when it exceeds the limit; return `429` with `Retry-After` |
| Cache geocode | `SET key value EX 86400` / `GET key` | `geo:ac:{normalized_text}` | Cached JSON or null |
| Cache homepage | `SET key value EX 300` | `home:v1` | Cached JSON; invalidate on admin publish |
| AI daily counter | `INCR` + `EXPIRE` to end of day | `ai:user:{id}:{date}` | Integer |

**Failure behaviour:** if Redis is unreachable, stricter in-process limits apply to login, signup, reset and AI endpoints; public browsing carries on; the incident is logged.

## B8. Gemini API (AI)

**Where used:** backend only, through a provider-agnostic wrapper. Model name comes from configuration.

| Feature | Request | Data sent | Expected response |
| --- | --- | --- | --- |
| **Search parsing** | `POST https://generativelanguage.googleapis.com/v1beta/models/{AI_MODEL}:generateContent` with header `x-goog-api-key` | `systemInstruction` (rules: return only filters), `contents` with the user's sentence, `generationConfig` {`temperature: 0`, `responseMimeType: "application/json"`, `responseSchema` (accommodation type, gender, location text, institution text, max rent, sharing type, amenities), `maxOutputTokens`} | `candidates[0].content.parts[0].text` containing JSON filters; `usageMetadata` token counts (stored in `ai_usage_records`) |
| **Description draft** | Same endpoint | System rules ('use only the facts provided; never invent'), then the owner's structured property data | Draft text shown in an editable box labelled 'AI draft — review before publishing' |

**Safety rules:** never send private data (phone numbers, private address, documents, other users' messages); validate every returned filter against real locations, amenities and sane ranges; treat user text and listing text as data, never as instructions; hard timeout of about 8 seconds; on any failure, return the normal filter form. **Errors:** 400 (bad request), 403 (key problem), 429 (quota), 503 (overloaded): all fall back to non-AI behaviour and are logged with cost data.

## B9. Neon, Render and Sentry

- **Neon PostgreSQL:** no HTTP calls. Backend connects with `DATABASE_URL` (pooled) via an async driver over TLS; migrations use the direct URL. Handle connection errors with a short retry then a friendly 503.
- **Render:** API health check `GET /healthz` returns `{ "status": "ok" }` (no database call) and `GET /readyz` checks database connectivity. The web app is a Render Static Site (build with npm run build, publish dist/) with rewrite rules sending /api/\* to the FastAPI service and all other paths to /index.html. Deploys run from GitHub; optional deploy hook URL triggers redeploys. Cron job runs `scripts/worker.py` every minute to send queued emails.
- **Sentry:** browser and backend SDKs initialised with DSN; attach the `request_id` and role (never emails or message text) to events; alert on error spikes.

## B10. Integration build checklist

- [ ] All keys are in environment variables; browser exposes only VITE\_GEOAPIFY\_MAP\_KEY (restricted to your domains) and VITE\_CLOUDINARY\_CLOUD\_NAME, plus VITE\_SITE\_URL and VITE\_SENTRY\_DSN.
- [ ] Every external call has a timeout, a retry rule where safe, and a designed fallback in the UI.
- [ ] Usage alerts at 70% and 90% for Geoapify, Cloudinary, Brevo and Gemini.
- [ ] Contract tests use mocked responses for each provider so tests never spend real quota.
- [ ] Required attributions (Geoapify, OpenStreetMap) are on every map.

## B11. Share-preview route (FastAPI, for WhatsApp and social links)

**What it does:** the property details page is client-side rendered via React Router with dynamic document title and JSON-LD structured data, so link previews need a small server route. It gives WhatsApp, Telegram, Facebook and X the title, description and cover photo as page tags, and sends real visitors on to the property page.

| Item | Spec |
| --- | --- |
| Request | `GET /api/v1/share/property/{slug}` (public, no login, through the existing `/api/*` rewrite) |
| Success | `200 text/html` with `<title>`, meta description, `og:title`, `og:description`, `og:type`, `og:url` (the canonical `/property/{slug}` address), `og:image` (cover photo, Cloudinary transformation 1200 × 630), `twitter:card`, a canonical link, and an instant redirect (`meta refresh` plus `location.replace`) to the canonical page |
| Not found | `404 text/html` generic DormioStay preview, identical for unknown, draft, pending and suspended listings |
| Headers | `Cache-Control: public, max-age=300`; `X-Robots-Tag: noindex`, so Google indexes only the canonical page |
| Never included | Phone number, private address, exact coordinates or owner details. Every value is HTML-escaped |
| Rate limit | Per IP, using the shared limiter |
| Frontend use | The Share button on the property page and owner dashboard copies the link (`https://<site>/api/v1/share/property/{slug}`) and opens the phone's share sheet where available |
| Sitemap | `GET /api/v1/seo/sitemap.xml` lists approved properties and public pages; a Render rewrite exposes it at `/sitemap.xml` |

**Note:** WhatsApp caches previews, so a changed cover photo can take a while to show for links that were already shared.
