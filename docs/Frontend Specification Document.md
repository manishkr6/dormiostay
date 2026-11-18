# DormioStay — Frontend Specification Document

_Version 1.1 | November 2026 | Design system + API and integration specification_

**Scope:** PART A — Shared design system; PART B — Web UX (the existing React/Vite specification); PART C — Native mobile UX (Android/iOS with Expo/React Native); PART D — API/integration behaviour. Tenant and Owner use both clients; Admin stays Web-only for v1. Companion documents: PRD, Technical Architecture, Security and Access. T-04 is the completed Web foundation; native requirements are planned for T-04M and later companion tickets, not changes to its acceptance history.

**Verify before coding:** third-party endpoint details below are accurate to the best of current knowledge but providers change them. Check each provider's current documentation when you implement.

---

# PART A — Shared design system

## A1. Design principles

1. **Trust first.** Calm, clean, photography-led. Verification and real information are visually prominent; promotional content is always labelled.
2. **Mobile first.** Most students and many owners use phones. Design the phone layout first, then enlarge.
3. **Premium but approachable.** Warm ivory backgrounds, deep indigo structure, one confident violet for actions, coral as a sparing accent.
4. **One visual system across clients.** Public/Tenant and Owner experiences on Web and Native Mobile share brand and status semantics; the Web-only Admin console keeps its denser controls. Each platform owns its components.
5. **Honest states.** Every screen has designed loading, empty, error and 'offline/waking up' states.

## A2. Colour palette

### Brand colours

| Token                 | Name          | Hex       | Use                                                   |
| --------------------- | ------------- | --------- | ----------------------------------------------------- |
| `--color-primary-900` | Deep Indigo   | `#312E81` | Headers, footer, strong headings, selected navigation |
| `--color-primary-600` | Royal Violet  | `#6D4AFF` | Primary buttons, links, focus accents, active filters |
| `--color-primary-100` | Soft Lavender | `#EDE9FE` | Soft backgrounds, selected chips, hover tints         |
| `--color-bg`          | Warm Ivory    | `#FAF9F6` | Main page background                                  |
| `--color-text`        | Charcoal      | `#171923` | Primary text                                          |
| `--color-text-muted`  | Slate         | `#64748B` | Secondary text, captions, placeholders                |
| `--color-accent`      | Coral         | `#F97360` | Small accents, promos, 'new' tags                     |
| `--color-highlight`   | Amber         | `#F59E0B` | Star ratings, highlights, warning backgrounds         |

### Supporting colours

| Token                   | Hex       | Use                                                        |
| ----------------------- | --------- | ---------------------------------------------------------- |
| `--color-surface`       | `#FFFFFF` | Cards, inputs, modals                                      |
| `--color-surface-muted` | `#F3F1EC` | Table headers, subtle panels                               |
| `--color-border`        | `#E5E2DA` | Dividers, card and input borders                           |
| `--color-border-strong` | `#CFCABD` | Input hover, stronger outlines                             |
| `--color-primary-700`   | `#5538D6` | Primary button pressed state                               |
| `--color-primary-800`   | `#43309F` | Dark violet text on lavender                               |
| `--color-success`       | `#16865B` | Verified, accepted, saved (never used as the brand colour) |
| `--color-success-bg`    | `#E4F5EC` | Success banners                                            |
| `--color-warning-text`  | `#92400E` | Warning text (amber is too light for text)                 |
| `--color-warning-bg`    | `#FEF3C7` | Warning banners                                            |
| `--color-error`         | `#DC2626` | Errors, destructive buttons                                |
| `--color-error-bg`      | `#FEE2E2` | Error banners                                              |
| `--color-info`          | `#2563EB` | Information                                                |
| `--color-info-bg`       | `#DBEAFE` | Info banners                                               |

### Colour rules

- **Body text** is Charcoal on Ivory or White. **White text** is allowed on Deep Indigo and Royal Violet.
- **Do not put white text on Coral or Amber** (contrast is too low); use Charcoal text on those.
- Amber and Coral are never used for small body text.
- Status is never conveyed by colour alone: always include an icon or label.
- Verify all pairs with a contrast checker; target at least 4.5:1 for normal text and 3:1 for large text and UI borders.
- Dark mode is **out of scope for v1**.

### Web CSS variable and Tailwind setup (existing contract)

```css
:root {
  --color-primary-900: #312e81;
  --color-primary-800: #43309f;
  --color-primary-700: #5538d6;
  --color-primary-600: #6d4aff;
  --color-primary-100: #ede9fe;
  --color-bg: #faf9f6;
  --color-surface: #ffffff;
  --color-surface-muted: #f3f1ec;
  --color-text: #171923;
  --color-text-muted: #64748b;
  --color-border: #e5e2da;
  --color-border-strong: #cfcabd;
  --color-accent: #f97360;
  --color-highlight: #f59e0b;
  --color-success: #16865b;
  --color-error: #dc2626;
  --color-info: #2563eb;
  --radius-sm: 8px;
  --radius-md: 12px;
  --radius-lg: 16px;
  --radius-full: 9999px;
  --shadow-card: 0 1px 2px rgba(23, 25, 35, 0.06), 0 4px 12px rgba(23, 25, 35, 0.06);
  --shadow-pop: 0 8px 30px rgba(23, 25, 35, 0.14);
}
```

Web maps these tokens into `tailwind.config.ts` (`colors.primary`, `colors.ink`, `colors.accent`, etc.). Native Mobile maps the same semantic values into NativeWind/native style tokens. No hard-coded hex values in either platform's components. The completed T-04 `apps/web/src/styles/tokens.css` remains the implementation baseline, including its accessible text-on-tint variants: success `#116644`, error `#B91C1C`, info `#1E40AF`. Preserve the brand/status swatches and test actual foreground/background pairs.

Only token values and other platform-neutral types/schemas/constants/formatters/query keys may gradually move to `packages/shared` when actual reuse exists. Web CSS shadows and native elevation/shadows require platform mappings; Web DOM/Radix/shadcn components are never reused directly in React Native. Do not extract or rewrite completed components just to create a shared package.

## A3. Typography

| Role              | Font                                                                  | Notes                                                                         |
| ----------------- | --------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| Headings          | **Playfair Display** (alternative: DM Serif Display), weights 600–700 | Gives the premium editorial feel. Use for page titles, hero, section headings |
| Body and UI       | **Inter**, weights 400, 500, 600                                      | Buttons, forms, tables, navigation, body copy                                 |
| Numbers in tables | Inter with tabular numbers                                            | Prices line up                                                                |

On Web, load via Google Fonts or self-hosted `@font-face` (in `public/fonts/` or `<link>` in `index.html`), with `font-display: swap` to avoid layout shift, and a fallback stack (Georgia, serif / system-ui, sans-serif).

| Style          | Mobile size / line height | Desktop size / line height | Font              |
| -------------- | ------------------------- | -------------------------- | ----------------- |
| Display (hero) | 34 / 40                   | 56 / 64                    | Playfair 700      |
| H1             | 28 / 36                   | 40 / 48                    | Playfair 700      |
| H2             | 24 / 32                   | 30 / 38                    | Playfair 600      |
| H3             | 20 / 28                   | 22 / 30                    | Inter 600         |
| H4             | 18 / 26                   | 18 / 26                    | Inter 600         |
| Body           | 16 / 24                   | 16 / 26                    | Inter 400         |
| Small          | 14 / 20                   | 14 / 20                    | Inter 400/500     |
| Caption        | 12 / 16                   | 12 / 16                    | Inter 500, slate  |
| Button         | 15 / 20                   | 15 / 20                    | Inter 600         |
| Price          | 20 / 28                   | 24 / 32                    | Inter 700, indigo |

On Web, body text is never smaller than 16px on phone inputs (prevents browser zoom). Native Mobile loads licensed Playfair Display and Inter assets through Expo font handling with a system fallback, uses equivalent hierarchy in native logical units and permits OS text scaling; do not assume a Google Fonts CSS link works natively. Maximum reading line length about 70 characters. Admin tables may use 14px.

## A4. Spacing, layout and responsiveness

The values below preserve the Web layout specification. Native Mobile applies the same spacing/radius hierarchy in logical units, with device safe areas and adaptive widths as specified in Part C; Web breakpoints and DOM effects are not a native implementation.

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

# PART B — Web UX

This part retains the existing responsive React 19 + Vite + TypeScript + Tailwind CSS UI, React Router layouts, Radix/shadcn conventions and T-04 API client. “Mobile” within Part B means a small-screen Web layout, not React Native. No native Admin counterpart is planned.

## B1. Component specifications

### Buttons

| Variant              | Look                                                                                           | Use                                         |
| -------------------- | ---------------------------------------------------------------------------------------------- | ------------------------------------------- |
| **Primary**          | Royal Violet fill, white text, 48px tall (mobile), radius 12, hover Violet 700, pressed darker | One per view: Search, Request Visit, Submit |
| **Secondary**        | White fill, 1px Indigo border, indigo text                                                     | Alternative actions                         |
| **Tertiary / ghost** | No border, violet text, lavender hover                                                         | Low-emphasis, 'Cancel'                      |
| **Destructive**      | Red fill, white text                                                                           | Suspend, Reject (always with confirmation)  |
| **Accent**           | Coral fill, Charcoal text                                                                      | Rare promos only                            |
| **Icon button**      | 44px square, transparent, lavender hover                                                       | Save (heart), close                         |

States for all: default, hover, pressed, focus (2px violet outline with 2px offset), disabled (50% opacity, no hover), loading (spinner replaces label, width unchanged, disabled). Sizes: small visual height 36px (admin tables only, retaining the completed T-04 minimum 44px interactive area), medium 44px, large 52px (hero CTA).

### Inputs and form controls

- Height 48px, radius 12, 1px border `--color-border`, white fill, 16px text, slate placeholder.
- Always a visible label above the field (never placeholder-only). Optional helper text below in slate; error text in red with an alert icon beneath the field.
- States: default, hover (stronger border), focus (2px violet ring), error (red border and message), disabled (muted fill), success (subtle green check where useful).
- Variants: text, email, password (show/hide toggle), phone (+91 prefix), number/currency (₹ prefix), textarea (min 4 rows, character counter), select, multi-select chips (amenities), checkbox, radio cards (room sharing type), switch, date and time picker, slider (budget with min/max thumbs), file upload dropzone (drag area, preview, remove, progress bar).
- Validate on blur and on submit; summarise errors at the top of long forms and move focus to the first error.

### Cards

| Card                          | Structure                                                                                                                                                                                                                                                                                                                                                                 |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **PropertyCard**              | 4:3 cover image with heart (save) top right; badges top left (Verified, Featured/Sponsored if applicable); below: name (H4, 1 line), area and distance ('1.2 km from ICFAI, straight-line'), amenity icons (max 4 + '+3'), room line ('From ₹4,500 / month'), availability tag ('3 beds left' / 'Currently full'), 'Updated 2 days ago' in caption. Whole card is a link. |
| **StatCard** (dashboards)     | Label, big number, small trend note, icon in lavender circle                                                                                                                                                                                                                                                                                                              |
| **RoomConfigCard**            | Sharing type, rent (INTEGER rupees, not NUMERIC), deposit (INTEGER rupees, not NUMERIC), beds available, extra charges, select/request button                                                                                                                                                                                                                             |
| **VisitCard**                 | Property thumbnail, date/time in IST, status badge, primary action by status (Cancel / Reschedule / Confirm)                                                                                                                                                                                                                                                              |
| **ListingStatusCard** (owner) | Status badge, last updated, next required action                                                                                                                                                                                                                                                                                                                          |

Cards: white, radius 16, 1px border, `--shadow-card`; hover lifts slightly on desktop. Skeleton card matches the final layout.

### Badges and chips

- **Verified** (green check icon + text, success colours): only for listings that passed admin verification; tooltip explains the standard. Never appears on unverified listings.
- **Featured / Sponsored** (amber outline with label): always visible when paid or promoted.
- Status badges: Pending (amber), Approved/Accepted/Confirmed (green), Rejected/Cancelled (red), Changes requested (blue), Draft (grey).
- Filter chips: visual height 36–40px with at least 44px interactive area, radius full, lavender fill when selected with check icon.

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

## B2. Key screen layouts

| Screen               | Layout notes                                                                                                                                                                                                                                                                                                                                                                                                      |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Homepage**         | Hero (Indigo to Violet gradient or photography) with headline, supporting text, large AI search box, two CTAs (Explore Stays, List Your Property). Then: browse by location cards, verified properties, budget-friendly, how it works (Search, Compare, Connect, Visit, Choose), owner call-to-action, footer.                                                                                                    |
| **Search results**   | Desktop: filters left or top, list left, map right (60/40). Mobile: list first, floating 'Map' button, filters in a bottom sheet, active filters as removable chips.                                                                                                                                                                                                                                              |
| **Property page**    | Client-side rendered via React Router with dynamic document title and JSON-LD structured data. Gallery, title and badges, sticky booking card on desktop (rent range, Request visit, Save); sticky bottom 'Request visit' bar on mobile. Sections: rooms and pricing, amenities, house rules, location map, how far from selected institution, report this listing, share button (copies the share-preview link). |
| **Tenant dashboard** | Overview cards, saved list, visits timeline, notifications.                                                                                                                                                                                                                                                                                                                                                       |
| **Owner wizard**     | One step per screen, large touch inputs, auto-saved draft, preview step mirrors the public property page.                                                                                                                                                                                                                                                                                                         |
| **Owner dashboard**  | Stat cards, 'needs attention' list (pending visit requests, outdated availability), property status cards.                                                                                                                                                                                                                                                                                                        |
| **Admin dashboard**  | Counts of pending hosts, pending listings, open reports; queues first, charts second.                                                                                                                                                                                                                                                                                                                             |

## B3. Accessibility and quality rules

- Keyboard reachable everywhere; visible focus ring; logical tab order; skip-to-content link.
- Modals and drawers trap focus and return it on close. Menus and tabs follow standard arrow-key behaviour (Radix provides this).
- Every image has meaningful alt text; decorative icons are hidden from screen readers.
- Form errors are announced; colour is never the only cue.
- Text can be enlarged to 200% without breaking layouts.
- Performance targets: Largest Contentful Paint under 2.5s on a mid-range phone over 4G for search and property pages; lazy-load images and the map; paginate results at 12 per page.
- Copy tone: plain, friendly, specific. Rupee amounts as '₹6,000 / month'; dates as '14 Nov, 6:00 pm IST'.

---

# PART C — Native mobile UX

## C1. Platform and navigation model

Native Mobile is a separate React Native + Expo + TypeScript app using Expo Router, NativeWind, TanStack Query, React Hook Form, Zod, Expo SecureStore, Lucide React Native, Expo Image and Sentry React Native. Add Expo Notifications with notification features and use EAS Build for Android/iOS builds when required. Preserve the shared brand, typography, spacing, radii, success/warning/error/info meanings and business rules; do not introduce a second visual brand or a WebView wrapper.

| Audience / shell | Navigation                                                           | Scope                                                                                                                                                    |
| ---------------- | -------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Public           | Native stack: Explore/discovery → results → property details         | Browse, filter and share without login; guest gated actions offer sign-in and a safe return destination                                                  |
| Auth             | Native stack for Tenant/Owner signup, login, verification and reset  | No native Admin login/MFA or role escalation                                                                                                             |
| Tenant           | Bottom tabs: **Explore, Saved, Visits, Notifications, Profile**      | Search/detail and visit subflows push onto the relevant stack; badge counts are announced accessibly                                                     |
| Owner            | Bottom tabs: **Overview, Properties, Visits, Availability, Account** | Application/status before approval; property wizard/management and uploads after applicable permission checks; inbox reachable from Overview and Account |
| Admin            | **Web-only control centre**                                          | Host review, listing moderation, reports investigation, content management, audit log and platform settings have no native screens                       |

Use labelled bottom tabs, one stack per journey and a clear header/back affordance. Preserve scroll/filter state on returning from details; do not put credentials, private messages or full documents in route parameters. Tabs and headers respect safe-area insets. T-04M builds navigation shells and a development component showcase with mock state; real screens and access guards belong to feature tickets. Hiding tabs never replaces backend permissions.

## C2. Layout, input and platform behaviour

- **Safe areas:** use an Expo-compatible safe-area provider; account for notches, status/navigation bars and the home indicator. Sticky CTAs, tab bars, toast and sheet footers include bottom insets; scroll padding keeps the final row visible.
- **Adaptive layout:** phone-first single-column content with shared 16-unit gutters, wider constrained content on tablets and landscape. Use measured available width rather than copying Web media queries. Scalable text can expand cards, labels and buttons without clipping.
- **Android back:** hardware/software/system Back dismisses the keyboard or active sheet/dialog first, then pops the current route; at the root, respect platform exit/background behaviour. Unsaved work gets a save/discard/cancel choice, not an accidental loss or a trapped navigation loop.
- **iOS swipe-back:** preserve native edge-swipe and header-back behaviour. Gallery/sheet gestures must not steal the screen-edge gesture. Guard unsaved work consistently with explicit back actions and provide a visible button alternative.
- **Keyboard avoidance:** forms scroll the focused field and error into view; use appropriate email/phone/numeric keyboard and next/done actions. Do not obscure inputs or submit controls with sticky UI. Password managers and paste work; show/hide keeps the cursor and accessible label correct. No sensitive input logging.
- **Bottom sheets:** snap/scroll within safe bounds, keep keyboard-aware action areas, label the sheet and provide a close button plus a non-gesture alternative. Move accessibility focus inside and restore it to the trigger or destination after dismissal; route navigation must not restore focus to a stale trigger.
- **Refresh/loading/network:** pull-to-refresh on lists with an accessible Refresh action; distinguish initial skeleton, refresh indicator, empty result and error. Preserve filter state on failure. Network status is advisory; show offline/retry messaging after actual failures, cancel irrelevant reads, avoid duplicate mutations, and never queue offline writes or silently report an unsaved draft as saved.
- **Touch feedback:** native pressed opacity/ripple or token-based background changes without shifting layout. Expose disabled/loading semantics and prevent duplicate submissions. Hover is optional for pointer devices; no action depends on hover.
- **Images and uploads:** Expo Image placeholders and appropriate public image sizes; accessible photo descriptions, pagination/virtualized lists and lazy maps. Use the system photo/document picker at the moment of upload with a clear purpose explanation and per-file progress/retry. Signed URLs come from FastAPI; private document previews are short-lived and never part of a persistent image cache.

## C3. Base native UI kit

All controls use shared tokens, native primitives and appropriate accessibility roles/states. Common states: default, pressed, focus where supported, disabled, loading, error and success where applicable. Form controls keep visible labels, helper/error/success descriptions and the user's input after errors. There are no DOM inputs, CSS-only focus traps or Radix primitives inside the native kit.

| Component       | Native specification                                                                                                                                                                                     |
| --------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Button**      | Primary, secondary, ghost, destructive, accent and icon variants matching Web semantics; stable width while loading, named action and native pressed feedback. Destructive actions require confirmation. |
| **Input**       | Labelled TextInput, suitable input mode/autofill, helper text, error/success text, secure password entry with show/hide; no placeholder-only labels.                                                     |
| **Textarea**    | Multiline TextInput, comfortable minimum height, character counter, keyboard-aware scrolling; default/error/disabled/success examples in the showcase.                                                   |
| **Select**      | Accessible native picker or selection sheet with selected value, search where useful, cancel/confirm and visible error/success descriptions; no HTML select.                                             |
| **Checkbox**    | Pressable labelled row with checkbox role/checked state and a large combined hit area; clear checked, unchecked and disabled states.                                                                     |
| **Switch**      | Native switch with label, current value and disabled state exposed; reserve for immediate binary settings, not final form submission.                                                                    |
| **Card**        | Property/room/visit/status/stat structures match Web data hierarchy; native layout and press semantics, separate save action without nested gesture conflicts.                                           |
| **Badge**       | Same status labels, icons and colours; never colour alone; static badges are not announced as buttons.                                                                                                   |
| **Modal**       | Native accessible dialog with title, safe-area/keyboard handling, contained screen-reader focus and explicit close/cancel; Android Back and focus restoration tested.                                    |
| **BottomSheet** | Filters, sort and confirmations; safe-area action bar, accessible title/close, scrollable content and non-swipe alternatives.                                                                            |
| **Toast**       | Nonblocking success/info/error feedback above keyboard/tab bar as appropriate; polite announcement, dismiss/retry action; never the only form error feedback.                                            |
| **Skeleton**    | Stable final-content geometry, hidden decorative placeholders and one loading announcement; disable shimmer with reduced motion.                                                                         |
| **EmptyState**  | Specific reason and useful next action, such as widening filters or exploring listings; no fake results.                                                                                                 |
| **ErrorState**  | Safe message, retry where safe and request reference when available; distinguish offline, unavailable, forbidden and session expiry without leaking hidden data.                                         |
| **Tabs**        | Accessible selected state and labelled touch targets; horizontal content tabs/segmented List–Map control, separate from bottom navigation.                                                               |

## C4. Native accessibility and quality

Minimum targets are **44 × 44pt on iOS and 48 × 48dp on Android**, satisfying the 44-or-greater baseline in native logical units; expand the hit area of small icons without overlapping neighbours. Retain the shared 8-unit target gap where practical. Support external keyboards with visible focus where the platform provides it; screen-reader semantics do not depend on a Web focus ring.

Test VoiceOver and TalkBack names, roles, values, selected/disabled states, sensible reading order, focus after navigation and announcements of validation errors. Hide decorative icons, describe meaningful photos, and pair status colours with text/icons. Move to an error summary/first invalid field on submit. Labels may wrap under large system text; do not clamp accessibility font scaling to fit a design. Maintain 4.5:1 normal text and 3:1 large text/meaningful UI contrast, using T-04 accessible text variants as needed.

Respect native reduced-motion preferences: remove nonessential movement/shimmer, retain immediate state feedback and do not make content depend on animation. Verify small phones, tablets/landscape, safe areas, keyboard open/closed, text enlargement, slow/offline/resumed connections and denied permissions on Android/iOS. Native responsiveness is evaluated on development/release builds and real journeys, not a Web Lighthouse score. Keep private app-switcher previews obscured as specified in Security section 2.2.2.

## C5. Sharing, links and notification permission UX

Share a property's canonical HTTPS link through the native share sheet; use the rich share-preview route when T-54 is available, without making Should-have previews a dependency for basic sharing. Verified Android App Links / iOS Universal Links open approved route patterns; Web remains the fallback when the app is absent. Unknown/unavailable listings show a safe not-found state. Validate route parameters, return destinations and session/role access; a link never confers permission. Verification/reset action tokens are single-use and never become session tokens or telemetry. See [Expo linking](https://docs.expo.dev/linking/overview/).

Notifications use the shared FastAPI inbox. Ask for push permission contextually after explaining its benefit, such as after a visit request, rather than blocking first launch. Provide “Not now”; denial keeps the inbox and email usable and exposes an OS-settings link without repeated prompts. Expo Notifications integrates permission and delivery registration when T-31M is implemented; T-04M does not register push tokens or send notifications. Use generic lock-screen text, remove registrations on account change/logout, and refetch authorised details after a notification tap. Provider acceptance is not delivery or reading. [Expo push overview](https://docs.expo.dev/push-notifications/overview/)

---

# PART D — API/integration behaviour

## D1. Internal API conventions (Web and Native Mobile to FastAPI)

| Item         | Rule                                                                                                                                                                                                                                                                                                        |
| ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Base path    | Web: same-origin `/api/v1/...` through the existing Vite proxy / Render rewrite. Native Mobile: configured HTTPS API origin plus `/api/v1/...`, using the same endpoints and response shapes.                                                                                                               |
| Format       | JSON, UTF-8, ISO 8601 UTC timestamps (display in IST)                                                                                                                                                                                                                                                       |
| Auth         | Web: opaque session cookie (`credentials: 'include'`), HttpOnly, Secure in production, SameSite=Lax. Native Mobile: opaque token persisted only in Expo SecureStore, explicitly sent as `Authorization: Bearer <session_token>`; no cookie auth. PostgreSQL stores SHA-256 hashes only.                     |
| CSRF         | Web writes send `X-CSRF-Token` from `/auth/csrf` and pass Origin checks, including the T-07 anonymous bootstrap. Validated cookie-free native bearer writes do not require browser CSRF; pre-auth native requests follow the explicit JSON/header policy in Security section 2.2. Reject mixed credentials. |
| Pagination   | `?page=1&page_size=12`; response `{items, total, page, page_size}`                                                                                                                                                                                                                                          |
| Success      | 200 / 201 with the object, 204 for no content                                                                                                                                                                                                                                                               |
| Error body   | `{ "error": { "code": "VALIDATION_ERROR", "message": "...", "fields": {"email": "..."}, "request_id": "abc123" } }`                                                                                                                                                                                         |
| Status codes | 400 bad request, 401 not signed in, 403 not allowed, 404 not found or hidden, 409 conflict, 422 validation, 429 rate-limited (with `Retry-After`), 500/503 server                                                                                                                                           |

Both clients preserve the standard `ApiError` shape, field validation, request ID and `Retry-After`, 204 handling, cancellation (including body reading), 65-second default request deadline and four-second waking-up notice from T-04. Do not automatically retry writes; read retry is disabled by default and may be enabled only deliberately with bounds. On 401 clear private state, return to sign-in and preserve only a safe intended route; on 403 explain the role/verification constraint. Never infer a successful mutation from a timeout.

The native API adapter takes relative resource paths against an allowlisted HTTPS base, forbids arbitrary host overrides and rejects cross-origin credential redirects. Signed upload/download URLs use a separate request path with no DormioStay Cookie or Authorization header. Query cache is memory-only for private data and cleared on logout/account change. SecureStore failures fail closed. T-04M tests adapters against mocks only; T-06/T-07 implement real auth and T-14M wires native login/logout.

**Main endpoint groups the clients use:**

| Group   | Examples                                                                                                                                                                                                            |
| ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Auth    | `POST /auth/signup`, `/auth/login`, `/auth/logout`, `/auth/verify-email`, `/auth/forgot-password`, `/auth/reset-password`, `GET /auth/me`                                                                           |
| Profile | Shared own-profile read/update contract defined in T-14P; no role/status/verification changes accepted from clients                                                                                                 |
| Public  | `GET /locations`, `/institutions`, `/amenities`, `/properties` (search), `/properties/{slug}`, `/homepage`                                                                                                          |
| Search  | `GET /properties?location=&institution=&max_rent=&sharing=&gender=&amenities=&sort=&page=`, `POST /search/ai` (body `{query}` → `{filters_applied, results}`)                                                       |
| Tenant  | `POST/DELETE /saved/{property_id}`, `POST /visits`, `PATCH /visits/{id}`, `GET /visits`, `GET /notifications`                                                                                                       |
| Owner   | `POST /owner/application`, `POST /owner/documents/presign`, `POST/PATCH /owner/properties`, `POST /owner/properties/{id}/images/sign`, `POST /owner/properties/{id}/submit`, `PATCH /owner/rooms/{id}/availability` |
| Admin   | `GET/PATCH /admin/hosts`, `/admin/listings`, `/admin/reports`, `/admin/locations`, `/admin/content`, `/admin/settings`, `GET /admin/audit`                                                                          |

## D2. Third-party services (summary)

| Service                            | Called from                                              | Purpose                                                                                             |
| ---------------------------------- | -------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| Geoapify                           | Backend (geocoding) and browser (map tiles)              | Address autocomplete, geocoding, map style and tiles                                                |
| MapLibre GL JS                     | Browser (library)                                        | Draws the map; not an API                                                                           |
| Cloudinary                         | Backend signs; Web or Native Mobile uploads              | Public property photos                                                                              |
| Cloudflare R2                      | Backend presigns; Web or Native Mobile uploads/downloads | Private verification documents                                                                      |
| Brevo                              | Backend worker                                           | Transactional emails and delivery events                                                            |
| Upstash Redis                      | Backend                                                  | Rate limits and short cache                                                                         |
| Gemini API                         | Backend only                                             | Turn search sentences into filters; draft descriptions                                              |
| Neon PostgreSQL                    | Backend only                                             | Primary database (connection, not an HTTP API)                                                      |
| Render                             | Platform                                                 | Hosting, health checks, deploy hooks                                                                |
| Sentry                             | Backend, Web and Sentry React Native                     | Redacted error reports; never credentials or private payloads                                       |
| Native map library/provider        | Native Mobile                                            | Selected at T-26M; shares backend geocoding and public coordinates, never imports MapLibre GL JS    |
| Expo Notifications / push delivery | Native permission/registration; backend dispatch         | Optional delivery over the shared inbox/event pipeline; provider credentials stay server/build-side |

## D3. Geoapify

**What it does:** turns text into addresses and coordinates and supplies the map style. **Key handling:** Web uses two keys: a server key (kept secret) and a browser map key restricted to your domains. Native map-provider configuration is selected separately in T-26M; it never reuses the server key.

| Call                                                | Request                                                                            | Data sent                                                                                                                                                   | Expected response                                                                                                                                                           |
| --------------------------------------------------- | ---------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Autocomplete** (owner wizard)                     | `GET https://api.geoapify.com/v1/geocode/autocomplete`                             | Query: `text` (what the owner typed), `filter=countrycode:in`, `bias=proximity:88.61,27.33` (near Gangtok; longitude first), `limit=5`, `lang=en`, `apiKey` | GeoJSON FeatureCollection. Each feature has `properties` (`formatted`, `address_line1`, `suburb`, `city`, `lat`, `lon`, `place_id`) and `geometry.coordinates` \[lon, lat\] |
| **Forward geocode** (admin locations, institutions) | `GET https://api.geoapify.com/v1/geocode/search`                                   | `text`, `filter`, `limit=1`, `apiKey`                                                                                                                       | Same GeoJSON shape; take the best feature                                                                                                                                   |
| **Reverse geocode** (when the owner drags the pin)  | `GET https://api.geoapify.com/v1/geocode/reverse`                                  | `lat`, `lon`, `apiKey`                                                                                                                                      | GeoJSON with the nearest address in `properties.formatted`                                                                                                                  |
| **Map style** (browser)                             | `GET https://maps.geoapify.com/v1/styles/osm-bright/style.json?apiKey=BROWSER_KEY` | Browser key                                                                                                                                                 | MapLibre style JSON; MapLibre then requests tiles itself                                                                                                                    |

**Frontend behaviour:** debounce autocomplete by 300ms, require at least 3 characters, cancel in-flight requests, cache results for the session. Both clients must call autocomplete through the backend (`GET /api/v1/geo/autocomplete?text=`) so the server key stays secret and results can be cached and rate-limited. **Errors:** 401 invalid key; 429 quota or rate limit; on any failure show the manual-pin fallback. **Attribution:** Web Geoapify maps show 'Powered by Geoapify' with OpenMapTiles and OpenStreetMap contributors credit. Native maps show the chosen provider/data attribution, including Geoapify where applicable. **Distance:** compute Haversine on the server; label it 'straight-line'.

## D4. Cloudinary (public property photos)

**Flow (signed direct upload):** Web or Native Mobile asks FastAPI for a signature; the backend checks ownership and image limits; the client uploads straight to Cloudinary without the DormioStay credential; FastAPI validates the returned reference against the authorised upload before saving it. Native uses a system picker rather than browser drag-and-drop.

| Step             | Request                                                                                | Data sent                                                                                                         | Expected response                                                                     |
| ---------------- | -------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| 1. Get signature | `POST /api/v1/owner/properties/{id}/images/sign` (our API)                             | Property id, file type, file size                                                                                 | `{cloud_name, api_key, timestamp, signature, folder, allowed_formats, max_file_size}` |
| 2. Upload        | `POST https://api.cloudinary.com/v1_1/{cloud_name}/image/upload` (multipart)           | `file`, `api_key`, `timestamp`, `signature`, `folder=dormiostay/properties/{property_id}`, same signed parameters | JSON with `public_id`, `secure_url`, `width`, `height`, `format`, `bytes`             |
| 3. Save          | `POST /api/v1/owner/properties/{id}/images` (our API)                                  | `public_id`, `secure_url`, alt text, order                                                                        | Saved image object                                                                    |
| 4. Delete        | Backend only: signed `POST https://api.cloudinary.com/v1_1/{cloud_name}/image/destroy` | `public_id`, timestamp, signature, api\_key                                                                       | `{ "result": "ok" }`                                                                  |

**Display URLs** use transformations: `https://res.cloudinary.com/{cloud_name}/image/upload/f_auto,q_auto,w_800,c_fill,ar_4:3/{public_id}`. Use thumbnails for cards (w\_400), 1200 for gallery. **Rules:** only JPG, PNG, WebP; maximum 8 MB; minimum 800px on the long side; at most 12 photos per property. **Errors:** show 'That file couldn't be uploaded' per file and let the owner retry that photo only.

## D5. Cloudflare R2 (private verification documents)

**Flow (presigned URLs):** nothing is public. The backend generates short-lived signed links using the S3-compatible API (endpoint `https://{account_id}.r2.cloudflarestorage.com`, region `auto`, bucket private).

| Step                | Request                                          | Data sent                                   | Expected response                                                            |
| ------------------- | ------------------------------------------------ | ------------------------------------------- | ---------------------------------------------------------------------------- |
| Request upload link | `POST /api/v1/owner/documents/presign` (our API) | `doc_type`, `content_type`, `size`          | `{upload_url, storage_key, expires_in}` (expiry about 300 seconds)           |
| Upload              | `PUT {upload_url}` (direct to R2)                | Raw file bytes with the same `Content-Type` | `200 OK` (empty body)                                                        |
| Confirm             | `POST /api/v1/owner/documents` (our API)         | `storage_key`, `doc_type`                   | Saved document record (status 'uploaded')                                    |
| Admin view          | `GET /api/v1/admin/documents/{id}/link`          | Document id                                 | `{url, expires_in}` signed GET link; the request is written to the audit log |

**Rules:** allowed types PDF, JPG, PNG; maximum 5 MB; storage key is random (never the user's filename); CORS on the bucket allows only your website origin for browser PUT; native signed uploads do not require opening browser CORS to wildcard origins; no public bucket access; links expire in minutes; log every admin view.

## D6. Brevo (transactional email)

**Where used:** only the backend email worker reads `email_outbox` and sends.

| Call                                                             | Request                                                                                                            | Data sent                                                                                                                  | Expected response                                                                  |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| **Send email**                                                   | `POST https://api.brevo.com/v3/smtp/email` with headers `api-key: BREVO_API_KEY`, `Content-Type: application/json` | `sender` {name, email} (verified sender), `to` \[{email, name}\], `subject`, `htmlContent`, `textContent`, optional `tags` | `201` with `{ "messageId": "..." }` → outbox status becomes `accepted_by_provider` |
| **Delivery events** (webhook into `POST /api/v1/webhooks/brevo`) | Brevo calls us                                                                                                     | Event type (for example delivered, bounce, blocked, spam), recipient email, message id, timestamp                          | We return `200` quickly and update the outbox row to `delivered` or `failed`       |

**Emails to build:** verify email, reset password, host application submitted/approved/rejected, listing submitted/approved/rejected/changes requested, visit requested/accepted/rejected/rescheduled, new message. Keep each short, with one clear button, plain-text fallback, and the contact address in the footer. **Errors:** 400 invalid payload (do not retry), 401 bad key (alert you), 429 or 5xx (retry with increasing delays, up to 5 attempts, then mark `failed`). Secure the webhook with a secret token in the URL or header and check it.

## D7. Upstash Redis

**Where used:** backend rate limiting and small caches (geocoding results, homepage data). Connect through the standard TLS Redis URL from the backend, or Upstash's REST API (`POST https://{endpoint}` with header `Authorization: Bearer {token}` and a JSON command array).

| Purpose          | Commands                                       | Key pattern                                 | Expected result                                                                 |
| ---------------- | ---------------------------------------------- | ------------------------------------------- | ------------------------------------------------------------------------------- |
| Count attempts   | `INCR key`, then `EXPIRE key 900` on first hit | `rl:login:ip:{ip}`, `rl:login:email:{hash}` | Integer count; block when it exceeds the limit; return `429` with `Retry-After` |
| Cache geocode    | `SET key value EX 86400` / `GET key`           | `geo:ac:{normalized_text}`                  | Cached JSON or null                                                             |
| Cache homepage   | `SET key value EX 300`                         | `home:v1`                                   | Cached JSON; invalidate on admin publish                                        |
| AI daily counter | `INCR` + `EXPIRE` to end of day                | `ai:user:{id}:{date}`                       | Integer                                                                         |

**Failure behaviour:** if Redis is unreachable, stricter in-process limits apply to login, signup, reset and AI endpoints; public browsing carries on; the incident is logged.

## D8. Gemini API (AI)

**Where used:** backend only, through a provider-agnostic wrapper. Model name comes from configuration.

| Feature               | Request                                                                                                                | Data sent                                                                                                                                                                                                                                                                                                  | Expected response                                                                                                          |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| **Search parsing**    | `POST https://generativelanguage.googleapis.com/v1beta/models/{AI_MODEL}:generateContent` with header `x-goog-api-key` | `systemInstruction` (rules: return only filters), `contents` with the user's sentence, `generationConfig` {`temperature: 0`, `responseMimeType: "application/json"`, `responseSchema` (accommodation type, gender, location text, institution text, max rent, sharing type, amenities), `maxOutputTokens`} | `candidates[0].content.parts[0].text` containing JSON filters; `usageMetadata` token counts (stored in `ai_usage_records`) |
| **Description draft** | Same endpoint                                                                                                          | System rules ('use only the facts provided; never invent'), then the owner's structured property data                                                                                                                                                                                                      | Draft text shown in an editable box labelled 'AI draft — review before publishing'                                         |

**Safety rules:** never send private data (phone numbers, private address, documents, other users' messages); validate every returned filter against real locations, amenities and sane ranges; treat user text and listing text as data, never as instructions; hard timeout of about 8 seconds; on any failure, return the normal filter form. **Errors:** 400 (bad request), 403 (key problem), 429 (quota), 503 (overloaded): all fall back to non-AI behaviour and are logged with cost data.

## D9. Neon, Render and Sentry

- **Neon PostgreSQL:** no HTTP calls. Backend connects with `DATABASE_URL` (pooled) via an async driver over TLS; migrations use the direct URL. Handle connection errors with a short retry then a friendly 503.
- **Render:** API health check `GET /healthz` returns `{ "status": "ok" }` (no database call) and `GET /readyz` checks database connectivity. The web app is a Render Static Site (build with npm run build, publish dist/) with rewrite rules sending /api/\* to the FastAPI service and all other paths to /index.html. Deploys run from GitHub; optional deploy hook URL triggers redeploys. Cron job runs `scripts/worker.py` every minute to send queued emails.
- **Sentry:** Web, React Native and backend SDKs initialised with DSN; attach the `request_id` and role (never emails or message text) to events; alert on error spikes.

## D10. Integration build checklist

- [ ] All keys are in environment variables; browser exposes only VITE\_GEOAPIFY\_MAP\_KEY (restricted to your domains) and VITE\_CLOUDINARY\_CLOUD\_NAME, plus VITE\_SITE\_URL and VITE\_SENTRY\_DSN.
- [ ] Native bundles expose only the public HTTPS API base, public Sentry DSN and approved public configuration. No database, Redis, Brevo, Cloudinary secret, R2 secret, Gemini or migration credentials; Expo/EAS injection does not make a bundled value private.
- [ ] Web cookie/CSRF and native SecureStore/bearer transports are tested for the same expiry, logout, ban and RLS rules.
- [ ] Every external call has a timeout, a retry rule where safe, and a designed fallback in the UI.
- [ ] Usage alerts at 70% and 90% for Geoapify, Cloudinary, Brevo and Gemini.
- [ ] Contract tests use mocked responses for each provider so tests never spend real quota.
- [ ] Required provider/data attributions are present on every map: preserve Geoapify/OpenStreetMap attribution on Web and satisfy the selected native provider terms.

## D11. Share-preview route (FastAPI, for WhatsApp and social links)

**What it does:** the property details page is client-side rendered via React Router with dynamic document title and JSON-LD structured data, so link previews need a small server route. It gives WhatsApp, Telegram, Facebook and X the title, description and cover photo as page tags, and sends real visitors on to the property page.

| Item           | Spec                                                                                                                                                                                                                                                                                                                                     |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Request        | `GET /api/v1/share/property/{slug}` (public, no login, through the existing `/api/*` rewrite)                                                                                                                                                                                                                                            |
| Success        | `200 text/html` with `<title>`, meta description, `og:title`, `og:description`, `og:type`, `og:url` (the canonical `/property/{slug}` address), `og:image` (cover photo, Cloudinary transformation 1200 × 630), `twitter:card`, a canonical link, and an instant redirect (`meta refresh` plus `location.replace`) to the canonical page |
| Not found      | `404 text/html` generic DormioStay preview, identical for unknown, draft, pending and suspended listings                                                                                                                                                                                                                                 |
| Headers        | `Cache-Control: public, max-age=300`; `X-Robots-Tag: noindex`, so Google indexes only the canonical page                                                                                                                                                                                                                                 |
| Never included | Phone number, private address, exact coordinates or owner details. Every value is HTML-escaped                                                                                                                                                                                                                                           |
| Rate limit     | Per IP, using the shared limiter                                                                                                                                                                                                                                                                                                         |
| Client use     | Web copies the link (`https://<site>/api/v1/share/property/{slug}`) and uses browser share where available; Native Mobile uses its native share sheet. Verified app links open the property route with a Web fallback; basic canonical-link sharing works before T-54.                                                                   |
| Sitemap        | `GET /api/v1/seo/sitemap.xml` lists approved properties and public pages; a Render rewrite exposes it at `/sitemap.xml`                                                                                                                                                                                                                  |

**Note:** WhatsApp caches previews, so a changed cover photo can take a while to show for links that were already shared.
