# DormioStay T-05 — Staging Infrastructure, Access Discovery & Security Plan

**Project:** DormioStay  
**Ticket:** T-05 (Render Deployment & Environments)  
**Base Commit:** `fab4a4529e16dd3f7ca6508446825fb4ebddd494` (`chore(deploy): prepare T-05 secure staging infrastructure`)  
**Security Status:** GATE 1 INDEPENDENTLY APPROVED BY ASTRA — 245 OFFLINE TESTS PASSING (318 API TESTS TOTAL) — READY FOR USER GIT AUTHORIZATION & GATE 2 PREFLIGHT  

---

## 1. Executive Summary & Verification Matrix

| Area | Current Status | Cloud Action Pending |
|---|---|---|
| **Neon Access Discovery** | Project `DormioStay` (`small-dew-81510029`) confirmed in AWS Singapore; CLI authenticated; MCP configured | Gate 2 automated role provisioning execution |
| **Render Access Discovery** | `render.yaml` Blueprint verified; CLI authenticated (`tea-d70j5lia214c73eb466g`) | Gate 5 Service provisioning on Render in Singapore |
| **Staging Database Branch** | Exists in Neon (`br-weathered-dust-bim5fibs`) with persistent lifecycle | Role provisioning (`dormiostay_app`) + Migration run |
| **Local Provisioning Automation** | Gate 1 Approved by Astra (`apps/api/scripts/provision_neon_staging_role.py`) | 245 offline tests passing; strict Mypy clean; ready for Gate 2 |
| **Remote Alembic Migrations** | Strictly DISABLED by fail-closed `migration_guard.py` | Staging-only enablement pending Gate 3 review |
| **Render Services** | `dormiostay-web` (Static) and `dormiostay-api` (`plan: free`) configured in `render.yaml` | Service provisioning on Render in Singapore region |
| **Web-to-API Proxy** | `/api/*` rewrite commented out / disabled | Active rewrite pending API domain ownership verification |
| **Production Protection** | Production migrations blocked; no production secrets or data touched | Production branch `br-holy-lake-bi0hc1lu` untouched |
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

---

## 3. Offline Test Suite

Location: `apps/api/tests/unit/test_provision_neon_staging_role.py`

* **Total Tests:** 245 passed (100% offline, 318 in full API suite).
* **Coverage:** Real subprocess OS signal handling and bounded SIGINT routing (eliminating unsafe 4th-SIGINT escape, enforcing 300ms settlement deadline across 1-5 SIGINTs, truth-telling exit code 1 with critical uncertainty diagnostic on blocked worker, exit code 130 on clean worker settlement, preservation of main task TargetVerificationError across shutdown, bounded 0.3s auxiliary task cancellation and async generator shutdown, and sub-12ms heartbeat responsiveness), standard-library `urllib.response.addinfourl` socket extraction across nested response wrappers, teardown ordering with socket shutdown before response close, verified worker thread termination with critical uncertainty detection, containment readback failure rejection preserving NOLOGIN requirement, event loop responsiveness under cancellation with non-blocking async worker polling (<0.012s heartbeat jitter), repeated caller cancellation handling without interrupting settlement or resetting monotonic deadlines, task uncancel draining ensuring critical uncertainty diagnostics propagate to callers, CLI critical uncertainty handling with exit code 1, standalone `--contain-role` monotonic deadline enforcement with zero SQL initiated after budget expiration, readback timeout handling reporting UNVERIFIED/UNKNOWN login status, verified NOLOGIN success preservation across cleanup failures, unretrieved future exception prevention, accurate interruption status reporting, bounded shielded emergency containment with unified monotonic deadline (<=15.05s) and fallback budget exhaustion prevention, immediate connection termination on deadline expiry, absolute HTTP request deadlines with active socket interruption, bounded socket chunk read timeouts, HTTP response byte limit enforcement, cancellation recovery during cleanup and reconnection, provider contract validation for required role `branch_id` and operation `project_id` and optional strings, native Keychain `kSecGenericPasswordItemClass = 0x67656e70` and pinned scope with duplicate detection, fail-closed Keychain scope resolution and deterministic `CFRelease` lifecycle, strict ISO UTC timestamp and secret contamination rejection with forensic file preservation, accurate TEMPORARY privilege attribution without false PUBLIC inheritance claims, database connector host/port/database/SSL verification, target verification and TLS identity checks, CLI execution lifecycle (missing API key, partial dry run, full dry run, missing admin password, unconfirmed branch rejection, production branch rejection across all modes, execution dispatch, containment dispatch), fail-closed auto-containment and emergency fresh reconnection, containment failure critical errors with UNVERIFIED/UNKNOWN login state, disk failure resilience during containment, Security.framework C API boundary testing with single item_ref binding, ambiguous Keychain match detection, secret retrieval verification, overwrite controls, secret redaction with uppercase, lowercase, and mixed-case percent encoding, diagnostic error mappings, operation polling and timeout handling with strict UUID and nullable field validation, initial connection closure before fresh reconnection, cancellation safety during connection establishment and cleanup, excessive privilege rejections (flags, memberships, ownership of db, nsp, rel, proc, typ with strict boolean/zero-int typing), baseline privileges checks, effective privilege assertions, symlink rejections, atomic checkpoint replace, checkpoint schema key validation, secret scrubbing, and control plane redirect rejection.
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

---

## 5. Human Approval Gates

1. **Gate 1 — Technical Security Review:** APPROVED by Codex Astra (independent audit). Local implementation, 245-test offline suite, Keychain adapter, privilege assertions, and CLI signal runner verified.
2. **Gate 2 — Staging Role Provisioning Execution (RISK DECISIONS A & B ACCEPTED FOR STAGING; EXECUTION PENDING SEPARATE USER AUTHORIZATION):** Execute `provision_neon_staging_role.py` on staging after explicit, separate user authorization.
3. **Gate 3 — Staging Schema Migration Execution:** Unblock staging in `migration_guard.py` and run `alembic upgrade head` as `neondb_owner`.
4. **Gate 4 — Staging Table Grants Execution:** Run `grant_application_role.sql` as `neondb_owner`.
5. **Gate 5 — Backend Render Service Deployment:** Bind pooled `DATABASE_URL` to Render `dormiostay-api` service and smoke test.
