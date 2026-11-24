# DormioStay T-05 — Final Automated Neon Staging Role Provisioning Procedure

**Target Role:** `dormiostay_app`  
**Target Environment:** Neon Staging Branch (`br-weathered-dust-bim5fibs`)  
**Target Project:** `small-dew-81510029` (`DormioStay`, AWS Singapore `ap-southeast-1`)  
**Security Status:** GATE 1 INDEPENDENTLY APPROVED BY ASTRA — 245 OFFLINE TESTS PASSING (318 API TESTS TOTAL) — READY FOR USER GIT AUTHORIZATION & GATE 2 PREFLIGHT  
**Execution Authorization:** STRICTLY LOCAL TESTING & DOCUMENTATION ONLY — ZERO CLOUD MUTATIONS PERFORMED  

---

## 1. Executive Summary & Astra Blocker Remediations

Following independent security audits and adversarial self-review of `apps/api/scripts/provision_neon_staging_role.py`, all identified defects have been comprehensively resolved in local code and independently validated with **245 dedicated offline unit tests** (318 tests across the full API unit suite):

1. **Astra Fourth-SIGINT & Signal Boundary Remediations — Safe Routing & Shutdown Error Preservation:**  
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

4. **Neon API Provider Contract Validation — Required vs Optional Identity Fields:**  
   - Validates control plane responses against upstream Neon API v2 OpenAPI schema specifications:
     - **Role Response Schema:** `name` (string) and `branch_id` (string) are required fields (`project_id` is optional). Script strictly asserts `role["name"] == APP_ROLE` and non-empty string `role["branch_id"] == STAGING_BRANCH_ID` (rejecting missing, `None`, boolean, integer, empty string, production branch `br-holy-lake-bi0hc1lu`, or unrelated branch IDs). Validates `role.get("project_id") == PROJECT_ID` if present.
     - **Operation Response Schema:** `id` (string UUID) and `project_id` (string) are required fields (`branch_id` and `endpoint_id` are optional/nullable strings). Script asserts `_is_valid_uuid(op["id"])`, non-empty string `op["project_id"] == PROJECT_ID`, and if `branch_id` or `endpoint_id` are present, strictly validates them against the verified staging branch and endpoint IDs (rejecting production branch or wrong types).
   - Conforms strictly to provider behavior: accepts valid non-empty string passwords without arbitrary length assumptions. Every returned operation must complete with `status == "finished"`.

5. **Strict ISO UTC Timestamp & Secret-Contaminated Checkpoint History Rejection:**  
   - Retained field allowlist: Schema validation strictly enforces `ALLOWED_CHECKPOINT_KEYS: frozenset({"state", "timestamp", "project_id", "branch_id", "endpoint_host", "step_number", "details"})`. Any unexpected or missing keys cause immediate rejection with `ProvisioningError`.
   - Secret contamination rejection: Every retained field in historical records is checked for registered literal secrets and regex patterns via `redactor.contains_secret()`. If contamination is detected, validation fails closed with `ProvisioningError`.
   - Strict timestamp validation: Timestamp is checked against `ISO_UTC_TIMESTAMP_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$")` and parsed with `datetime.datetime.fromisoformat()`.
   - Forensic preservation: If checkpoint history on disk is corrupted, invalid, or contaminated, validation fails closed *before* any temporary file is created or written; the original file on disk is left untouched for operator forensics.

6. **PostgreSQL Database TEMPORARY Privilege Attribution Accuracy:**  
   - Exact attribution helper: `format_temporary_privilege_evidence(public_temp, app_temp, role_exists)` provides unambiguous classification:
     - Role absent: `"NOT VERIFIED (ROLE DOES NOT EXIST)"`.
     - PUBLIC=True, App=True: `"True (PUBLIC contributing grant; PENDING HUMAN ACCEPTANCE)"`.
     - PUBLIC=False, App=True: `"True (Effective privilege granted; source unverified; PENDING HUMAN ACCEPTANCE)"` (zero false claims of PUBLIC inheritance).
     - PUBLIC=False, App=False: `"False (No verified TEMPORARY permission)"` (zero false claims of PUBLIC inheritance).
     - PUBLIC=True, App=False: `"False (Effective privilege denied; PENDING HUMAN ACCEPTANCE)"`.
   - Strict boolean typing: Non-boolean types raise `PrivilegeVerificationError`.
   - Consistent evidence logging: Applied across dry-run reports, Step 5 pre-reset checks, Step 13 post-reset checks, and checkpoint details.
   - Risk decisions recorded: Bootstrap password DDL logging (Decision A) and inherited TEMPORARY privileges (Decision B) have been accepted by the project owner for STAGING ONLY (with live TEMPORARY verification confirmed in Phase 2 preflight). Gate 2 role provisioning execution remains pending separate user authorization.

---

## 2. Target Invariants & Production Safety Guard

| Resource Attribute | Verified Target Value | Guard Action |
|---|---|---|
| **Neon Project ID** | `small-dew-81510029` | Rejects if project ID != `small-dew-81510029` |
| **Neon Project Name** | `DormioStay` | Region: AWS Singapore (`ap-southeast-1`) |
| **Staging Branch ID** | `br-weathered-dust-bim5fibs` | Rejects if branch ID != `br-weathered-dust-bim5fibs` |
| **Staging Branch Name** | `staging` | Rejects if branch name != `staging` |
| **Staging Direct Compute Host** | `ep-cold-smoke-bir1s74t.c-5.ap-southeast-1.aws.neon.tech` | Verified endpoint host; port 5432 |
| **Staging Pooled Host** | `ep-cold-smoke-bir1s74t-pooler.c-5.ap-southeast-1.aws.neon.tech` | For future runtime backend (`DATABASE_URL`) |
| **Target Database** | `neondb` | Asserted on connection (`current_database()`) |
| **Target Admin Role** | `neondb_owner` | Asserted on connection (`current_user` AND `session_user`) |
| **Production Branch ID** | `br-holy-lake-bi0hc1lu` | **STRICTLY FORBIDDEN:** Fatal exit across all modes |
| **Production Branch Name**| `production` | **STRICTLY FORBIDDEN:** Fatal exit across all modes |

---

## 3. Platform Architecture & Trade-Offs

### 3.1 Neon Platform Constraints
* **Control Plane API (`POST /roles`) Superuser Issue:** Raw Neon Management API role creation unconditionally assigns `neon_superuser` membership, violating least privilege and triggering `verify_runtime_role()` fail-closed assertions in `apps/api/app/db/session.py`.
* **`PASSWORD NULL` Reset Blocker:** Calling Neon's `reset_password` API on a role created with `PASSWORD NULL` fails with `400 Bad Request`.
* **SCRAM-SHA-256 Verifier Injection:** Incompatible with `pg_gateway` connection router / pooler.
* **CLI Preset Grants Disallowed:** Neon CLI `--access read_write` issues blanket `DELETE` grants on future tables, breaking audit-log immutability.

### 3.2 Supported SDK Adaptation
As documented in `@neon/sdk/dist/neon/resources/role-access.js:31`:
> *"The API refuses to reset the password of a role that has none, so a login role gets `bootstrapPassword`... `enableLoginStatement` runs only after Neon has replaced it."*

The script adapts this mechanism: creates `NOLOGIN` with a temporary token, invokes Neon's control plane reset API, and enables `LOGIN` only after verification.

### 3.3 Bootstrap Password Logging Risk (Decision A: ACCEPTED FOR STAGING ONLY)
* **Risk Disclosure:** Executing `CREATE ROLE dormiostay_app NOLOGIN PASSWORD '...'` causes the statement text to enter PostgreSQL query logs (`log_statement = 'ddl'` or `log_statement = 'all'`).
* **Immutability of Provider Logs:** Neon API password reset updates `pg_authid` and proxy caches, but **cannot erase provider-side historical log lines**.
* **Mitigating Safeguards:**
  1. Role is created with `NOLOGIN`. Even if logs are inspected in real time, no session can be authenticated.
  2. Single-use 256-bit CSPRNG entropy token (`secrets.token_urlsafe(32)`), discarded from memory.
  3. Neon API replaces the password before `LOGIN` is ever enabled.
  4. `LOGIN` is enabled strictly post-reset. No session could ever authenticate using the bootstrap token.
  5. Zero secret disclosure in logs or reports.
  6. Fail-closed execution and automated emergency containment.
* **Decision Status:** ACCEPTED BY PROJECT OWNER FOR STAGING ONLY under the documented safeguards. This decision applies strictly to staging and does NOT authorize production operations or Gate 2 execution.

### 3.4 PostgreSQL `TEMPORARY` Privilege Clarification (Decision B: ACCEPTED FOR STAGING ONLY)
* **Direct Database Grant:** The automation explicitly executes `REVOKE ALL ON DATABASE neondb FROM dormiostay_app;` followed solely by `GRANT CONNECT ON DATABASE neondb TO dormiostay_app;`. It grants **zero direct `TEMPORARY` privileges** to `dormiostay_app`.
* **PUBLIC Baseline Privilege:** PostgreSQL standard default database privileges grant `TEMPORARY` (`TEMP`) to the pseudo-role `PUBLIC` (`=Tc/...`).
* **Effective Application-Role Capability:** Because all PostgreSQL roles inherit privileges granted to `PUBLIC`, `has_database_privilege('dormiostay_app', 'neondb', 'TEMPORARY')` evaluates to `True` whenever `PUBLIC` holds `TEMPORARY`.
* **Live Catalog State Boundary & Preflight Verification:** Verified during authorized read-only preflight: `pg_database.datacl` on staging `neondb` contains `=Tc/neondb_owner`, and `has_database_privilege('public', 'neondb', 'TEMPORARY')` returned `true`.
* **CTEs & Memory:** Ordinary Common Table Expressions (CTEs / `WITH` queries) and `ORDER BY` query sorting do **not** use the database `TEMPORARY` privilege; they operate in memory (`work_mem`) and disk work files (`temp_tablespaces`), requiring zero database privileges.
* **Decision Status:** ACCEPTED BY PROJECT OWNER FOR STAGING ONLY, SUBJECT TO LIVE VERIFICATION (verified active during Phase 2 preflight). Direct TEMPORARY grants are forbidden. Modifying or revoking PUBLIC database privileges requires separate approval. This decision applies strictly to staging and does NOT authorize production operations or Gate 2 execution.

---

## 4. Operation Modes & Command Entry Points

### 4.1 Dry Run Mode (Default — Strictly Read-Only)
Performs zero SQL mutations, zero password generation, zero API resets, and zero Keychain writes.
```bash
python apps/api/scripts/provision_neon_staging_role.py --dry-run
```

### 4.2 Execution Mode (Future Gate 2 Authorization)
Requires explicit opt-in and exact staging branch confirmation:
```bash
python apps/api/scripts/provision_neon_staging_role.py \
  --execute \
  --confirm-branch br-weathered-dust-bim5fibs
```

### 4.3 Emergency Containment Mode
Reverts `dormiostay_app` to `NOLOGIN` on verified staging endpoint:
```bash
python apps/api/scripts/provision_neon_staging_role.py \
  --contain-role \
  --confirm-branch br-weathered-dust-bim5fibs
```

---

## 5. macOS Keychain Service Specification

* **Service Name:** `dormiostay.neon.staging.dormiostay_app`
* **Account Name:** `dormiostay_app`
* **Implementation:** Audited native `Security.framework` via `ctypes` (`MacOSSecurityFrameworkAdapter`). Zero shell execution, zero CLI arguments. Deterministic item reference binding with duplicate detection.
* **Pinned Item Class:** `kSecGenericPasswordItemClass = 0x67656e70 = 1734700656`.
* **Pinned Scope Resolution:** Requires a valid explicit Keychain handle (`keychain is not None` and non-null) or acquires default Keychain via `SecKeychainCopyDefault()`. Non-zero status codes (including `errSecNoDefaultKeychain = -25307` and `errSecAuthFailed = -25293`), ctypes exceptions, or NULL reference pointers fail closed with `SecretStoreError`. Explicit NULL handles (`ctypes.c_void_p(0)`) are strictly rejected.
* **Operation Guard:** `_assert_valid_keychain()` enforces `self.keychain is not None` and non-null on all operations.
* **Deterministic Native Resource Cleanup:** Tracks handle ownership (`owns_keychain = True` when acquired via default copy). `close()` and `__del__()` invoke `CFRelease(self.keychain)` if and only if owned by the adapter. `SecretStore.close()` delegates cleanly to `self.adapter.close()`.
* **Readback Verification:** `verify_stored_password(expected_password=...)` performs constant-time byte comparison (`secrets.compare_digest`) against retrieved credential.
* **Overwrites:** Explicit opt-in flag `--allow-keychain-overwrite` required if credential already exists.

---

## 6. Operational Boundaries & Failure Handling

| Failure Scenario | Automated Recovery Action | Residual Boundary / Manual Action |
|---|---|---|
| **Slow or Infinite HTTP Response Streaming** | Absolute request deadline (`deadline = start_time + req_timeout`) and bounded socket timeouts per chunk abort and close socket | Raises `TargetVerificationError` with `TIMEOUT` |
| **Caller Cancellation During HTTP Request** | `_ActiveRequestHolder.interrupt()` actively shuts down and closes the underlying socket; worker settled via async polling; zero event loop stall | Raises `asyncio.CancelledError` (or `TargetVerificationError` with critical uncertainty if socket was never registered); zero orphaned tasks |
| **HTTP Response Exceeding 2MB** | Bounded chunk reading asserts `total_bytes <= max_response_bytes`; aborts immediately | Fails closed with `ProvisioningError` |
| **Emergency Containment Timeout (Overall > 15s)** | Shielded containment task cancelled, settlement awaited; records `CONTAINMENT_FAILED` | Raises `ContainmentFailureError` noting role login status is `UNVERIFIED/UNKNOWN` |
| **Post-LOGIN verification failure** | `attempt_auto_containment()` alters role to `NOLOGIN` via shielded task with bounded execution | If containment fails, raises `ContainmentFailureError` |
| **Cancellation during containment** | `asyncio.shield` + loop-await ensures inner task finishes; zero orphaned background tasks | Re-raises `CancelledError` only after verified security cleanup |
| **Hanging connection close during containment** | `_safe_close_or_terminate()` times out at 2s/3s and invokes `conn.terminate()` | Connection dropped; emergency sequence proceeds |
| **Cleanup error after containment SQL** | Isolated in `try...finally` with `BaseException` handling | Verified NOLOGIN outcome preserved |
| **Keychain Scope Resolution Failure** | `SecKeychainCopyDefault` error, exception, or NULL handle fails closed | Raises `SecretStoreError`; zero subsequent C API calls made |
| **Ambiguous Keychain entries** | `_check_ambiguous_matches()` detects duplicate items (`count > 1`) | Fails closed with `SecretStoreError` |
| **Checkpoint history corrupted or contaminated** | Strict allowlist schema validation and `contains_secret()` check on all historical records | Fails closed; preserves original file on disk for forensics |
| **CLI Double SIGINT During Worker Settlement** | `_run_with_bounded_sigint_handling` routes repeated SIGINTs (up to 3) as task cancellations; monotonic 300ms settlement window executes to completion | Exits 1 with critical uncertainty if worker remains active; exits 130 if worker settles; catastrophic escape valve on $\ge 4$ SIGINTs |
| **Catastrophic process kill (SIGKILL/power)** | N/A (process terminated) | Operator runs `ALTER ROLE dormiostay_app NOLOGIN;` in Neon SQL Editor |
