# DormioStay T-05 — Staging Infrastructure, Access Discovery & Security Plan

**Project:** DormioStay  
**Ticket:** T-05 (Render Deployment & Environments)  
**Verified Commit:** `0a576a3c3f5761a341eb87873b7e4e2e9569c05c` (`fix(staging): audit PostgreSQL sequence privileges in Gate 4`)
**Security Status:** GATE 1 INDEPENDENTLY APPROVED BY ASTRA; GATE 2 COMPLETED & VERIFIED; GATE 3 COMPLETED & VERIFIED; GATE 4 SEQUENCE-AWARE LIVE BASELINE VERIFIED COMPLIANT (`task-4511`, ZERO GRANTS, ZERO MUTATIONS, SNAPSHOT PRESERVED); GEMINI 4 HYDROGEN PROVISIONAL PASS; INDEPENDENT ASTRA APPROVAL PENDING; GATE 5 NOT STARTED

---

## 1. Executive Summary & Verification Matrix

| Area | Current Status | Cloud Action Pending |
|---|---|---|
| **Neon Access Discovery** | Project `DormioStay` (`small-dew-81510029`) confirmed in AWS Singapore; CLI authenticated; MCP configured | Fully configured & active |
| **Render Access Discovery** | `render.yaml` Blueprint verified; CLI authenticated (`tea-d70j5lia214c73eb466g`) | Gate 5 Service provisioning on Render in Singapore (NOT STARTED) |
| **Staging Database Branch** | Exists in Neon (`br-weathered-dust-bim5fibs`) with persistent lifecycle | Role provisioned (`dormiostay_app`), migration applied (`0001_foundation`), Gate 4 zero-grant security baseline verified compliant in live catalog |
| **Recovery Snapshot** | Verified recovery snapshot `snap-wandering-flower-bi9ojapb` (`pre-gate3-migration-baseline`, 32,047,104 bytes) active and preserved | Preserved for emergency rollback |
| **Application Role Privileges** | Gate 4 sequence-aware live read-only verification (`0a576a3`, `task-4511`): ZERO effective table/column privileges across all 12 tables; `0` sequences in non-system schemas; `0` unexpected relations/routines; `verify_catalog_privileges()` passed without exceptions; no live mutation required | Ready for Gate 5 deployment (Gemini 4 Hydrogen provisional pass; independent Astra approval PENDING) |
| **Local Provisioning & Guard Automation** | Gate 1 Approved by Astra (baseline); Gate 4 guard updated with relation inventory, sequence auditing (`'S'`), literal schema matching, `asyncpg` `relkind::text` cast, and genuine transaction tests (42 unit tests, 439 full API suite); strict Mypy clean | Sequence-aware live read-only verification PASSED (`task-4511`); independent Astra approval PENDING |
| **Remote Alembic Migrations** | Staging enablement implemented in `migration_guard.py`; Gate 3 executed and verified on staging (`0001_foundation`); production strictly blocked | Completed & verified |
| **Render Services** | `dormiostay-web` (Static) and `dormiostay-api` (`plan: free`) configured in `render.yaml` | Gate 5 Service provisioning on Render in Singapore (NOT STARTED) |
| **Web-to-API Proxy** | `/api/*` rewrite commented out / disabled | Active rewrite pending API domain ownership verification |
| **Production Protection** | Production migrations and grants blocked; zero production secrets or data touched | Production branch `br-holy-lake-bi0hc1lu` untouched |
| **Financial Cost** | $0.00 / month (100% Free-Tier compliant) | $0.00 / month |

---

## 2. Astra Review Alignment & Local Automation

Codex Astra independently audited the DormioStay Neon staging role provisioning automation and confirmed that production protections held, TLS and hostname checks were effective, and no cloud mutations occurred. Following subsequent adversarial inspection and self-review, all edge cases around synchronous HTTP response streaming, standard-library response wrapper socket extraction, teardown ordering (`sock.shutdown` before `resp.close`), and thread termination guarantees have been resolved.

### 2.1 Final Remediated Defect Categories & Safety Guarantees
Location: `apps/api/scripts/provision_neon_staging_role.py`

0. **Astra Fourth-SIGINT & Signal Boundary Remediations — Safe Routing & Shutdown Error Preservation:**
   - **Dedicated Signal-Routing Runner:** Replaces standard `asyncio.run()` with `_run_with_bounded_sigint_handling[T](coro)` across all CLI modes (`--dry-run`, `--contain-role`, and `--execute`).
   - **Complete Elimination of Unsafe Fourth-SIGINT Fallback:** In previous revisions, a 4th SIGINT invoked `orig_handler` which synchronously raised `KeyboardInterrupt`. When this interrupted the runner during settlement, `gather(return_exceptions=True)` during shutdown discarded the main task's pending `TargetVerificationError`, and the CLI incorrectly caught `KeyboardInterrupt` to exit 130 (`[!] Operation cancelled by user or signal.`) while the HTTP worker was still alive. The unsafe 4th-SIGINT fallback has been completely removed.
   - **Safe Repeated SIGINT Routing:** For any number of SIGINTs (1, 2, 3, 4, 5, or more) during settlement, the handler routes each signal directly to `main_task.cancel()` and wakes the loop thread-safely via `loop.call_soon_threadsafe(lambda: None)` without aborting the event loop or restarting deadlines.
   - **Monotonic Settlement Window Execution:** The monotonic 300ms worker settlement deadline executes to completion without event-loop stalls or premature process exit.
   - **Truthful CLI Uncertainty Reporting:** If the worker thread remains blocked after 300ms, `TargetVerificationError` with critical uncertainty (`Emergency cleanup cannot be guaranteed; thread remains active.`) propagates cleanly to `cli_entrypoint`, printing `[!] Provisioning error:` to stderr and exiting with code **1** (NOT 130).
   - **Clean Settlement Propagation:** If the worker settles cleanly within 300ms, `CancelledError` propagates to `cli_entrypoint`, printing `[!] Operation cancelled by user or signal.` and exiting with code **130**.
   - **Preservation of Shutdown Exceptions:** If `main_task` completes with a `ProvisioningError` (including `TargetVerificationError` or `ContainmentFailureError`), that terminal exception is captured in `main_task_exc` and re-raised out of the runner, taking absolute precedence over generic cancellation.
   - **Strictly Bounded Shutdown:** Auxiliary tasks are cancelled and awaited with `asyncio.wait(aux_tasks, timeout=0.3)`, and async generators are closed with `asyncio.wait_for(loop.shutdown_asyncgens(), timeout=0.3)`. Stubborn uncooperative background tasks cannot hang process shutdown indefinitely.
   - **Reliable Signal Handler Restoration:** The original signal handler is strictly restored in `finally:`, and event loop resources are closed reliably.
   - **Real Subprocess Verification Suite:** Validated with 13 dedicated Category 17 tests using handshake synchronization (`READY`): single, double, triple, 4-SIGINT, and 5-SIGINT tests on blocked workers (all exit 1 with critical uncertainty); 1, 2, and 4-SIGINT tests on settling workers (all exit 130); shutdown error preservation; stubborn auxiliary task bounded termination; and signal handler restoration.

1. **Astra Blocker 1 Remediations — HTTP Repeated Cancellation, Worker Lifecycle & Critical Diagnostics:**
   - **Non-Blocking Asynchronous Worker Settlement:** Synchronous `thread.join(timeout=1.0)` has been completely removed from the asyncio event loop thread. On caller cancellation or timeout, `NeonApiClient.async_request()` polls worker thread settlement asynchronously (`while thread.is_alive() and loop.time() < settle_deadline: await asyncio.sleep(0.005)` with a 300ms window), preventing any event-loop stalls and keeping 10ms heartbeat jitter strictly below 0.012s.
   - **Bounded Settlement Under Repeated Cancellation:** On repeated caller cancellations during the 300ms worker settlement window (e.g. 2nd cancellation 25ms after 1st), `NeonApiClient.async_request()` handles `CancelledError` inside the settlement loop without interrupting the non-blocking sleep sequence or resetting the monotonic deadline. Drains pending cancellation requests on the task (`curr_task.uncancel()`) so that `TargetVerificationError` with critical uncertainty (`Emergency cleanup cannot be guaranteed; thread remains active.`) propagates cleanly to callers and the CLI entry point exits with code 1 rather than masking unverified mutations as ordinary cancellations (exit 130).
   - **Worker Future Exception Retrieval & Suppression:** Added `fut.add_done_callback(_consume_future_exception)` and explicit worker-side cancellation check (`if cancel_event.is_set(): return`). If a cancelled worker finishes late, it suppresses setting future exceptions/results, completely eliminating unretrieved future exception warnings.
   - **Truthful Interruption Guarantees:** `_ActiveRequestHolder.interrupt()` tracks whether a socket was actually registered (`self.sock is not None`). If cancellation occurs before socket registration (e.g. synthetic pre-header opener or connection establishment blocking without a socket), `interruption_guaranteed` is accurately reported as `False`. The client fails closed with `TargetVerificationError` indicating critical uncertainty (`Emergency cleanup cannot be guaranteed; thread remains active.`), rather than falsely claiming guaranteed cancellation.
   - **Absolute HTTP Deadline & Sliced Socket Chunk Timeouts:** Bounded chunked reading (`chunk_size = 64KB`, `max_response_bytes = 2MB`) dynamically updates socket inactivity timeout via `sock.settimeout(min(0.1, max(0.001, deadline - time.monotonic())))`, ensuring kernel `select()` returns within 100ms on active socket reads and preventing infinite or slow chunked streaming attacks.
   - **Multi-Layer Socket Extraction & Teardown Ordering:** Standard library response wrappers (`urllib.response.addinfourl` -> `http.client.HTTPResponse` -> `_io.BufferedReader` -> `socket.SocketIO` -> `_sock`) are safely traversed by `_extract_socket()` using a bounded hierarchy check. On caller cancellation, teardown executes in strict sequence: (1) `sock.shutdown(SHUT_RDWR)`, (2) `sock.close()`, and (3) `resp.close()`.

2. **Astra Blocker 2 Remediations — Standalone `--contain-role` Monotonic Deadlines & Failure Diagnostics:**
   - **Unified Monotonic Deadline Enforcement:** Both `attempt_auto_containment()` and standalone `execute_containment_nologin()` enforce a single monotonic deadline (`deadline_t = time.monotonic() + containment_timeout`). All sub-steps compute remaining budget via `get_remaining() = deadline_t - time.monotonic()`.
   - **Zero SQL Statements After Deadline Expiry:** `execute_containment_nologin()` checks remaining budget before every step (metadata verification, connection, TLS check, `ALTER ROLE`, and readback). If the budget is exhausted (`rem <= 0.05`), it raises `TimeoutError` immediately with zero positive minimum substitutions, strictly guaranteeing zero SQL statements are initiated after expiration.
   - **Explicit Diagnostics on Readback Timeout or Failure:** If `verify_structural_restrictions` times out or fails, raises `ContainmentFailureError` stating that role login status is `UNVERIFIED/UNKNOWN` and provides manual administrator recovery instructions, never returning a bare `TimeoutError`.
   - **Preserved Verified NOLOGIN Success:** If readback confirms `rolcanlogin = False`, `nologin_verified = True` ensures containment is recorded as `CONTAINMENT_EXECUTED` even if subsequent connection close raises an exception or times out.
   - **Immediate Connection Termination Near Deadline:** In `finally:` cleanup, if remaining budget is exhausted (`rem_close <= 0.05`), the automation immediately invokes `conn.terminate()` instead of awaiting graceful `close()`, strictly guaranteeing zero overshoot beyond the deadline.
   - **Strict Production Branch Rejection Upfront:** Production branch IDs (`br-holy-lake-bi0hc1lu`) are rejected immediately across all modes before any execution or containment logic begins.
   - **Catastrophic Failure Boundary:** If uncatchable process termination occurs (`SIGKILL`, physical host failure, or complete network partitioning before containment finishes), local containment cannot complete. In this event, the operator must execute manual containment via the Neon Console SQL Editor or psql:
     ```sql
     ALTER ROLE dormiostay_app NOLOGIN;
     ```

3. **Fail-Closed Keychain Scoping, Resolution & Reference Lifecycle (Finding B):**
   - **Fail-Closed Scope Resolution:** `MacOSSecurityFrameworkAdapter.__init__` strictly validates its target Keychain handle. It accepts an explicit non-null `keychain` handle or queries `SecKeychainCopyDefault()`. If `SecKeychainCopyDefault()` returns any non-zero status code (e.g. `errSecNoDefaultKeychain = -25307` or `errSecAuthFailed = -25293`), raises a ctypes exception, or leaves a NULL pointer reference, the adapter immediately fails closed with `SecretStoreError`. Explicit NULL handles (`ctypes.c_void_p(0)`) are strictly rejected.
   - **Strict Operation Invariant:** `_assert_valid_keychain()` is asserted as the entry point of all methods (`entry_exists()`, `get_password()`, `store_password()`, `verify_stored_password()`, `_check_ambiguous_matches()`). If `self.keychain` is None or NULL, operations fail closed immediately before invoking any native Security.framework functions.
   - **Deterministic Native Reference Release:** The adapter tracks reference ownership (`self.owns_keychain = True` when acquired via default copy). `close()` and `__del__()` invoke `CFRelease(self.keychain)` if and only if `self.owns_keychain is True`, preventing handle leakage and memory corruption. `SecretStore.close()` cleanly delegates to `self.adapter.close()`.

### 2.2 PostgreSQL `TEMPORARY` Privilege Clarification (Decision B: ACCEPTED FOR STAGING ONLY)
* **Direct Database Grant:** The automation explicitly executes `REVOKE ALL ON DATABASE neondb FROM dormiostay_app;` followed solely by `GRANT CONNECT ON DATABASE neondb TO dormiostay_app;`. It grants **zero direct `TEMPORARY` privileges** to `dormiostay_app`.
* **PUBLIC Baseline Privilege:** PostgreSQL standard default database privileges grant `TEMPORARY` (`TEMP`) to the pseudo-role `PUBLIC` (`=Tc/...`).
* **Effective Application-Role Capability:** Because all PostgreSQL roles inherit privileges granted to `PUBLIC`, `has_database_privilege('dormiostay_app', 'neondb', 'TEMPORARY')` evaluates to `True` whenever `PUBLIC` holds `TEMPORARY`.
* **Live Catalog State Boundary & Preflight Verification:** Verified during authorized read-only preflight: `pg_database.datacl` on staging `neondb` contains `=Tc/neondb_owner`, and `has_database_privilege('public', 'neondb', 'TEMPORARY')` returned `true`.
* **CTEs & Memory:** Ordinary Common Table Expressions (CTEs / `WITH` queries) and `ORDER BY` query sorting do **not** use the database `TEMPORARY` privilege; they operate in memory (`work_mem`) and disk work files (`temp_tablespaces`), requiring zero database privileges.
* **Decision Status:** ACCEPTED BY PROJECT OWNER FOR STAGING ONLY, SUBJECT TO LIVE VERIFICATION (verified active during Phase 2 preflight). Direct TEMPORARY grants are forbidden. Modifying or revoking PUBLIC database privileges requires separate approval. This decision applies strictly to staging and does NOT authorize production operations or Gate 2 execution.

### 2.3 Bootstrap Password Log Risk (Decision A: ACCEPTED FOR STAGING ONLY)
* **Risk Disclosure:** Creating the role with a temporary bootstrap password records the DDL statement in PostgreSQL query logs.
* **Immutability of Provider Logs:** Neon API reset overwrites `pg_authid` and proxy caches, but cannot erase provider-side historical log lines.
* **Mitigating Safeguards:**
  1. Initial `NOLOGIN` role state ensures no session can authenticate even if logs are read.
  2. Single-use 256-bit CSPRNG entropy token, discarded from memory and never reused.
  3. Control plane password reset executes before `LOGIN` is ever enabled.
  4. Zero secret disclosure in logs or reports (enforced by `SecretRedactor`).
  5. Fail-closed execution and automated emergency containment (`ALTER ROLE dormiostay_app NOLOGIN`).
* **Decision Status:** ACCEPTED BY PROJECT OWNER FOR STAGING ONLY under the documented safeguards. This decision applies strictly to staging and does NOT authorize production operations or Gate 2 execution.

### 2.4 Neon Proxy TLS Architecture Correction & Client Transport Verification
* **Prior Dry-Run Failure Analysis:** The initial live staging dry-run aborted with `TargetVerificationError: TLS is not active on established connection. Insecure connection terminated.` because the script unconditionally asserted that PostgreSQL view `pg_stat_ssl WHERE pid = pg_backend_pid()` must return `ssl = true`.
* **Client-to-Proxy vs. Backend TLS Distinction:** In Neon's managed cloud architecture, external client connections terminate TLS at the Neon edge proxy (`pg_gateway`), which enforces strict TLS and decrypts traffic before routing to the backend PostgreSQL daemon inside the isolated compute container over an internal pod channel. Consequently, PostgreSQL's internal `pg_stat_ssl` catalog view for the backend PID reports `ssl = false`. Treating this internal backend hop as proof that the client-facing WAN connection lacks encryption was an architectural misattribution.
* **Cryptographic Guarantees Preserved:** Client-side connections continue to enforce a verified Python `SSLContext` with `check_hostname = True`, `verify_mode = ssl.CERT_REQUIRED`, trusted public CA validation, and SNI matching `EXPECTED_ENDPOINT_HOST` (`ep-cold-smoke-bir1s74t.c-5.ap-southeast-1.aws.neon.tech`). Python's OpenSSL layer cryptographically fails closed on untrusted certificates, expired certificates, or hostname mismatches. `_verify_client_tls(conn)` enforces that the active client transport is an authenticated TLS session.
* **What `pg_stat_ssl` Does and Does Not Establish:** `pg_stat_ssl` reflects the backend process's local connection status; it does not prove external proxy architecture or establish whether the client-facing connection over the internet is encrypted. In the corrected implementation, `pg_stat_ssl` is queried and recorded as non-secret diagnostic telemetry (`backend_ssl_active`), without treating `ssl = false` as a fatal error when client TLS is verified separately.
* **Gate 1 Scope Boundary:** Gate 1 was independently approved by Codex Astra for the baseline implementation. The subsequent TLS proxy correction is an operational refinement pending focused independent review before any live mutation.
* **Staging-Only Risk Acceptances A and B:** Maintained as documented (Risk A: Bootstrap password logging accepted for staging only under documented safeguards; Risk B: Inherited PUBLIC TEMPORARY accepted for staging only, live verified on staging).

---

## 3. Offline Test Suite

Location: `apps/api/tests/unit/test_provision_neon_staging_role.py`

* **Total Tests:** 278 passed (100% offline, 351 in full API suite).
* **Coverage:** Role promotion recovery lifecycle (`--promote-role`), verify-only preflight (`--verify-only`), bounded post-LOGIN step timeouts (`DEFAULT_PROMOTION_STEP_TIMEOUT = 5.0s`), timeout-triggered NOLOGIN containment and verified readback, conservative ambiguous `ALTER ROLE LOGIN` acknowledgement handling without automated retries, fail-closed containment failure preservation through final cleanup preventing secondary cancellation masking, CLI truth-telling reporting UNVERIFIED/UNKNOWN with exit code 1 on failed containment, cryptographic RFC 5802 SCRAM-SHA-256 verifier matching and edge cases, pre-promotion NOLOGIN and least-privilege assertions, post-promotion automatic fail-closed containment on app connection/auth/privilege failure, real subprocess OS signal handling and bounded SIGINT routing (eliminating unsafe 4th-SIGINT escape, enforcing 300ms settlement deadline across 1-5 SIGINTs, truth-telling exit code 1 with critical uncertainty diagnostic on blocked worker, exit code 130 on clean worker settlement, preservation of main task TargetVerificationError across shutdown, bounded 0.3s auxiliary task cancellation and async generator shutdown, and sub-12ms heartbeat responsiveness), standard-library `urllib.response.addinfourl` socket extraction across nested response wrappers, teardown ordering with socket shutdown before response close, verified worker thread termination with critical uncertainty detection, containment readback failure rejection preserving NOLOGIN requirement, event loop responsiveness under cancellation with non-blocking async worker polling (<0.012s heartbeat jitter), repeated caller cancellation handling without interrupting settlement or resetting monotonic deadlines, task uncancel draining ensuring critical uncertainty diagnostics propagate to callers, CLI critical uncertainty handling with exit code 1, standalone `--contain-role` monotonic deadline enforcement with zero SQL initiated after budget expiration, readback timeout handling reporting UNVERIFIED/UNKNOWN login status, verified NOLOGIN success preservation across cleanup failures, unretrieved future exception prevention, accurate interruption status reporting, bounded shielded emergency containment with unified monotonic deadline (<=15.05s) and fallback budget exhaustion prevention, immediate connection termination on deadline expiry, absolute HTTP request deadlines with active socket interruption, bounded socket chunk read timeouts, HTTP response byte limit enforcement, cancellation recovery during cleanup and reconnection, provider contract validation for required role `branch_id` and operation `project_id` and optional strings, native Keychain `kSecGenericPasswordItemClass = 0x67656e70` and pinned scope with duplicate detection, fail-closed Keychain scope resolution and deterministic `CFRelease` lifecycle, strict ISO UTC timestamp and secret contamination rejection with forensic file preservation, accurate TEMPORARY privilege attribution without false PUBLIC inheritance claims, database connector host/port/database/SSL verification, target verification and TLS identity checks, non-secret backend pg_stat_ssl diagnostic telemetry with client TLS verified separately, representative in-memory TLS handshake rejection of untrusted certificates and hostname mismatches, CLI execution lifecycle (missing API key, partial dry run, full dry run, missing admin password, unconfirmed branch rejection, production branch rejection across all modes, execution dispatch, containment dispatch, promotion dispatch), fail-closed auto-containment and emergency fresh reconnection, containment failure critical errors with UNVERIFIED/UNKNOWN login state, disk failure resilience during containment, Security.framework C API boundary testing with single item_ref binding, ambiguous Keychain match detection, secret retrieval verification, overwrite controls, secret redaction with uppercase, lowercase, and mixed-case percent encoding, diagnostic error mappings, operation polling and timeout handling with strict UUID and nullable field validation, initial connection closure before fresh reconnection, cancellation safety during connection establishment and cleanup, excessive privilege rejections (flags, memberships, ownership of db, nsp, rel, proc, typ with strict boolean/zero-int typing), baseline privileges checks, effective privilege assertions, symlink rejections, atomic checkpoint replace, checkpoint schema key validation, secret scrubbing, and control plane redirect rejection.
* **Scope Limitation:** Verifies logic and error handling with mock objects; does not prove live Neon cloud execution.

---

## 4. Operational Instructions

### Dry-Run Preflight Inspection (Safe — Default)
```bash
python apps/api/scripts/provision_neon_staging_role.py --dry-run
```

### Future Staging Provisioning Execution (Gate 2)
```bash
python apps/api/scripts/provision_neon_staging_role.py \
  --execute \
  --confirm-branch br-weathered-dust-bim5fibs
```

### Emergency Containment (Enforce NOLOGIN)
```bash
python apps/api/scripts/provision_neon_staging_role.py \
  --contain-role \
  --confirm-branch br-weathered-dust-bim5fibs
```

### Staging Role Promotion Recovery (Gate 2 Reconciliation & Safe Promotion)
When `dormiostay_app` already exists with `NOLOGIN`, restricted attributes applied, and password stored in Keychain, standard `--execute` halts. The safe `--promote-role` recovery mode completes Gate 2:
```bash
# Verify-only preflight: asserts all 7 pre-conditions without database mutations
python apps/api/scripts/provision_neon_staging_role.py \
  --promote-role \
  --verify-only \
  --confirm-branch br-weathered-dust-bim5fibs

# Authorized promotion execution: enables LOGIN, tests live app auth, re-audits privileges
python apps/api/scripts/provision_neon_staging_role.py \
  --promote-role \
  --confirm-branch br-weathered-dust-bim5fibs
```

---

## 5. Human Approval Gates

1. **Gate 1 — Technical Security Review:** APPROVED by Codex Astra (independent audit). Local implementation, 245-test offline suite, Keychain adapter, privilege assertions, and CLI signal runner verified.
2. **Gate 2 — Staging Role Provisioning Execution:** COMPLETED & VERIFIED. Restricted role `dormiostay_app` created, least privilege asserted, password reset via Neon API, stored in macOS Keychain, and promoted with verified authentication over TLS.
3. **Gate 3 — Staging Schema Migration Execution (COMPLETED & VERIFIED):**
   - **Status:** EXECUTED & LIVE VERIFIED on authorized staging branch `br-weathered-dust-bim5fibs`. Applied Alembic revision `0001_foundation`, successfully provisioning all 11 application tables and `alembic_version`.
   - **Baseline Recovery Snapshot:** Verified recovery snapshot `snap-wandering-flower-bi9ojapb` (`pre-gate3-migration-baseline`) created prior to migration.
   - **Implementation:** `apps/api/app/db/migration_guard.py` enforcing strict preflight identity binding, authentic TLS without redirects, and fail-closed safety.

4. **Gate 4 — Staging Table Grants & Privilege Hardening (UPDATED LIVE BASELINE VERIFIED COMPLIANT; GEMINI 4 HYDROGEN PROVISIONAL PASS; INDEPENDENT ASTRA APPROVAL PENDING):**
   - **Verification Decisions:**
     - Historical baseline (`3a87d4e2...`, `2026-11-24T17:54:25Z`): `GATE 4 LIVE BASELINE — VERIFIED COMPLIANT`.
     - Updated baseline (`173f6a69...`, `2026-11-25T01:29:54Z` / `06:59:54 IST`, `task-4237`): **`GATE 4 UPDATED LIVE BASELINE — VERIFIED COMPLIANT`**. Complete committed `verify_catalog_privileges()` executed without exceptions.
   - **Review & Approval Status:** Gemini 4 Hydrogen returned `PROVISIONAL PASS — READY FOR ASTRA INDEPENDENT APPROVAL`. Independent Codex Astra security approval remains explicitly **PENDING**.

   - **A. Live Catalog Checks Actually Performed (Neon Staging Baseline):**
     - **Target Invariants:** Neon project `small-dew-81510029`, branch `br-weathered-dust-bim5fibs` (staging), endpoint `ep-cold-smoke-bir1s74t.c-5.ap-southeast-1.aws.neon.tech`, database `neondb`, admin `neondb_owner`, app role `dormiostay_app`.
     - **Preserved Recovery Snapshot:** `snap-wandering-flower-bi9ojapb` (`pre-gate3-migration-baseline`, 32,047,104 bytes, created 2026-11-24T16:22:26Z) verified active and preserved.
     - **Protected Production Defense:** Production branch `br-holy-lake-bi0hc1lu` strictly rejected and untouched.
     - **Connection Isolation:** Established over verified TLS (`sslmode=verify-full`). Read-only transaction strictly enforced (`SET default_transaction_read_only = on; SET TRANSACTION READ ONLY; SHOW transaction_read_only -> on`).
     - **12 Governed Tables Verified:** All 11 application tables (`amenities`, `locations`, `platform_settings`, `property_categories`, `institutions`, `users`, `user_profiles`, `user_sessions`, `email_verification_tokens`, `password_reset_tokens`, `audit_logs`) and `alembic_version` verified present and owned by `neondb_owner`.
     - **Table & Column Permissions:** Zero table privileges (0/7) and zero effective column privileges (0/4 via `has_any_column_privilege`) observed for `dormiostay_app` and `PUBLIC` on all 12 tables. `audit_logs.action` SELECT evaluated to `False`.
     - **Raw & Default ACLs:** Clean catalogs; zero grant options; default ACLs clean with zero leaks.
     - **Role Boundaries:** `rolcanlogin = True`, zero elevated attributes, zero memberships in `pg_auth_members`, zero objects owned across relations, schemas, routines, and types.
     - **Database & Schema Grants:** `CONNECT` on `neondb` and `USAGE` on schema `public` justified and verified; 0 `CREATE` privileges.
     - **Zero Live Mutations Executed:** No live `GRANT` or `REVOKE` command was necessary or executed to satisfy the zero-grant baseline.

   - **B. Newly Identified Security Coverage Gaps (Codex Astra Review):**
     - **Gap 1 (Unexpected View Access):** Astra demonstrated that an unexpected administrator-owned view (such as `public.users_export`) could expose governed table data through the view owner's privileges if accessible to `dormiostay_app` or `PUBLIC` via explicit grants, altered default privileges, or inherited role permissions. (Clarifying that PostgreSQL does not automatically grant `PUBLIC SELECT` on newly created views; explicit or inherited permissions create the vulnerability).
     - **Gap 2 (Wildcard Schema-Prefix Matching):** Astra found that `nspname NOT LIKE 'pg_%'` incorrectly excluded non-system schemas such as `pgextra` because `_` acted as a single-character wildcard.
     - **Gap 3 (Transaction Testing Accuracy):** Astra confirmed that previous transaction unit tests used simulated `AsyncMock` transaction objects rather than genuine SQLAlchemy transaction machinery, and integration tests were unverified.

   - **C. Local Corrections & Regression Test Evidence:**
     - **Fix 1 (Relation Inventory & View Governance):**
       - Implemented comprehensive relation inventory across non-system schemas querying `pg_class` where `c.relkind IN ('r', 'v', 'm', 'f', 'p')`.
       - Asserts the exact approved foundation baseline in `public` (12 tables of kind `'r'`). Fails closed on missing tables, unexpected relkinds, or empty/null catalog results.
       - Audits every unexpected relation (tables, ordinary views `'v'`, materialized views `'m'`, foreign tables `'f'`, partitioned tables `'p'`) across non-system schemas for all 7 table privileges and all 4 column privileges against both `dormiostay_app` and `PUBLIC`. Any accessible unexpected relation raises `RuntimeError`.
       - Added regression tests: rejection of unexpected admin-owned view `public.users_export` (accessible directly or via `PUBLIC`), rejection of unexpected materialized views, rejection of column-level privilege leaks, and fail-closed validation on missing tables, bad relkinds, and empty catalog results.
     - **Fix 2 (Literal Schema-Prefix Matching & Routine Audit):**
       - Replaced wildcard-sensitive matching with exact literal prefix comparison `left(nspname, 3) <> 'pg_' AND nspname != 'information_schema'` across non-system schema inventory, routine audits, and table ownership checks.
       - Preserves exclusions for genuine PostgreSQL system namespaces (`pg_catalog`, `pg_toast`, etc.) and `information_schema` while correctly including `pgextra` and other boundary schemas.
       - Audits routines in non-system schemas: enforces allowlist on `SECURITY DEFINER` routines, and verifies that routines in unexpected non-system schemas (e.g. `pgextra`) cannot be executed by `dormiostay_app` or `PUBLIC`.
       - Added regression tests: verifying `pgextra` schema USAGE rejection, and verifying accessible routine rejection in unexpected schemas for `dormiostay_app` and `PUBLIC`.
     - **Fix 3 (Accurate Transaction Test Evidence & Verification Tiers):**
       - Clearly documented the 5 verification tiers to prevent conflation of simulated mocks, real engines, and live targets:
         1. **Real SQLAlchemy transaction testing using SQLite:** `test_genuine_sqlalchemy_transaction_machinery_offline` exercises genuine SQLAlchemy 2.0 transaction machinery offline via SQLite in-memory, reproducing the autobegin collision (`InvalidRequestError`), proving the single `begin()` boundary, and verifying automatic rollback on error.
         2. **Simulated Python unit tests:** `StatefulTransactionConnection` is a simulated unit mock verifying runner control flow, preflight ordering, and fail-closed branching in Python memory.
         3. **Earlier Neon live read-only verification:** Live read-only baseline on 2026-11-24 verified zero table and column privileges on all 12 governed tables on staging branch `br-weathered-dust-bim5fibs` without live mutations.
         4. **New security checks not yet verified against Neon:** The new relation inventory, view auditing, and literal schema checks in `grant_guard.py` have been verified locally via unit tests, but have **NOT** yet been executed against live Neon staging.
         5. **PostgreSQL integration tests not performed:** Live PostgreSQL integration tests against a real PostgreSQL instance remain unexecuted in local offline testing due to the absence of a disposable local PostgreSQL container.
      - **Fix 4 (asyncpg PostgreSQL Internal "char" Array Codec Compatibility):**
        - Live read-only verification revealed that `c.relkind = ANY(:monitored_kinds)` triggered `asyncpg.exceptions.DataError` because PostgreSQL typed `$1` as `"char"[]`, which asyncpg expects as `bytes`, rejecting Python `str`.
        - Narrowly resolved in `grant_guard.py` by casting `c.relkind::text = ANY(CAST(:monitored_kinds AS text[]))` and selecting `c.relkind::text AS relkind`. Forces PostgreSQL to infer `text[]` (OID 1009), which asyncpg encodes cleanly from `list[str]`.
        - Added regression test `test_relation_inventory_query_syntax_and_parameter_binding` verifying explicit text casting, string array parameters, and clean PostgreSQL dialect compilation.
      - **Fix 5 (PostgreSQL Sequence `relkind = 'S'` Privilege Governance — Astra Finding):**
        - Codex Astra identified that `MONITORED_RELATION_KINDS` excluded `'S'`, omitting administrator-owned sequences accessible by `dormiostay_app` or `PUBLIC`.
        - Added `'S'` to `MONITORED_RELATION_KINDS = ("r", "v", "m", "f", "p", "S")` (preserving `c.relkind::text = ANY(CAST(:monitored_kinds AS text[]))`) and audited every sequence in non-system schemas using `has_sequence_privilege(:role, :seq, :priv)` across `("USAGE", "SELECT", "UPDATE")` for both `dormiostay_app` and `PUBLIC`.
        - Distinguishes sequence existence from effective runtime access (unprivileged owned/identity/serial sequences pass cleanly; accessible or malformed responses fail closed).
        - Added 5 regression tests covering `MONITORED_RELATION_KINDS`, `UPDATE` grant rejection, `PUBLIC` `USAGE` rejection, unprivileged owned sequence pass, and malformed return fail-closed behavior.
      - **Test Suite Results:**
        - 42 passed Gate 4 focused regression tests.
        - 439 passed full API unit test suite.
        - Ruff lint clean (0 errors); Ruff format clean (35 files formatted); strict Mypy clean (`grant_guard.py` and `test_gate4_grant_guard.py`).

   - **D. Live Verification History, Governance & Approval Boundaries:**
     - **Gate 4 Sequence-Aware Live Staging Verification (`2026-11-25T13:31:50Z` / `19:01:50 IST`, `task-4511` on commit `0a576a3c3f5761a341eb87873b7e4e2e9569c05c`):** **PASSED** (`GATE 4 SEQUENCE-AWARE LIVE BASELINE — VERIFIED COMPLIANT`, recorded in `gate4_sequence_live_baseline_report.json`).
       - Complete committed `verify_catalog_privileges()` executed to completion without exceptions in an enforced `READ ONLY` transaction (`transaction_read_only = on`) over certificate- and hostname-verified TLS (`sslmode=verify-full`, `PGSSLROOTCERT=/etc/ssl/cert.pem`).
       - Neon staging target independently verified: project `small-dew-81510029`, branch `br-weathered-dust-bim5fibs`, endpoint `ep-cold-smoke-bir1s74t`, host `ep-cold-smoke-bir1s74t.c-5.ap-southeast-1.aws.neon.tech`, database `neondb`, admin `neondb_owner`, application role `dormiostay_app`.
       - All 12 governed tables (`11` application tables + `alembic_version`, `relkind = 'r'`, owner `neondb_owner`) verified at Alembic revision `0001_foundation` with strictly **zero effective application-table privileges** (all 7 privileges: `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`, `REFERENCES`, `TRIGGER` returning `False` for both `dormiostay_app` and `PUBLIC`) and zero column privileges (including `audit_logs.action` `SELECT`).
       - `MONITORED_RELATION_KINDS = ("r", "v", "m", "f", "p", "S")` executed via corrected text array query. Exactly **zero sequences** (`relkind = 'S'`) were found in non-system schemas (foundation schema uses UUID primary keys across all 11 tables). **Explicit limitation:** Because zero sequences exist in the live staging database, live sequence privilege checks (`has_sequence_privilege`) were not exercised against an existing live sequence; live verification confirmed catalog inventory filtering and zero-grant assertion on 0 live sequences.
       - Positive and negative sequence privilege grant scenarios (rejection of sequence `UPDATE` granted to `dormiostay_app`, rejection of sequence `USAGE` granted via `PUBLIC`, clean pass for unprivileged owned sequences, and fail-closed handling of malformed returns) were verified via the **5 simulated in-memory unit regression tests** in `test_gate4_grant_guard.py` (which test Python control flow and SQL branching against mock connection objects), distinct from the **live PostgreSQL catalog execution** on Neon staging.
       - Zero raw ACL leakage, zero default ACL leakage, zero elevated role memberships, zero objects owned by `dormiostay_app`, `CONNECT` on `neondb` = `True`, `USAGE` on `public` = `True`, `CREATE` = `False`, and zero `SECURITY DEFINER` routines in non-system schemas. Total 330 security assertions evaluated with 0 violations.
       - No live `GRANT` or `REVOKE` operations were necessary or executed; staging database is natively compliant with zero-table-grant and zero-sequence-grant policies.
       - Recovery snapshot `snap-wandering-flower-bi9ojapb` (`pre-gate3-migration-baseline`, 32,047,104 bytes) verified active and preserved. Production branch `br-holy-lake-bi0hc1lu` untouched.
     - **Gemini 4 Hydrogen Provisional Review:** Returned **`PROVISIONAL PASS — SEQUENCE FINDING RESOLVED`** on `0a576a3c3f5761a341eb87873b7e4e2e9569c05c` (technical audit; does not replace independent Astra certification).
     - **Independent Codex Astra Approval:** Explicitly **PENDING** (re-review submission ready with live sequence verification evidence).
     - **No Live Mutations:** Zero `GRANT`, `REVOKE`, pushes, or merges authorized.
5. **Gate 5 — Backend Render Service Deployment (NOT STARTED):**
   - **Status:** **NOT STARTED**.
   - Service provisioning on Render in Singapore region (`dormiostay-api` Web Service on `plan: free`, `dormiostay-web` Static Site), environment variable binding (`DATABASE_URL` pooled `dormiostay_app` URL with `?sslmode=verify-full` only, exact `FRONTEND_ORIGIN`), and smoke testing remain deferred pending independent Codex Astra Gate 4 approval and explicit project owner authorization. Zero Render resources have been provisioned.
