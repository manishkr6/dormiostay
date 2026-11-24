"""Automated least-privilege provisioning and verification for DormioStay Neon staging role.

Target Role: dormiostay_app
Target Database: neondb
Target Staging Branch: br-weathered-dust-bim5fibs
Protected Production Branch: br-holy-lake-bi0hc1lu (STRICTLY FORBIDDEN)

Operation Modes:
  1. Dry Run (Default): Strictly read-only target validation and privilege verification.
  2. Execution: Explicit opt-in (--execute --confirm-branch br-weathered-dust-bim5fibs).
  3. Containment: Emergency containment (--contain-role
     --confirm-branch br-weathered-dust-bim5fibs).
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import ctypes.util
import datetime
import enum
import fcntl
import json
import os
import re
import secrets
import signal
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Coroutine
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

import asyncpg  # type: ignore[import-untyped]

# Target Configuration Invariants
PROJECT_ID = "small-dew-81510029"
PROJECT_NAME = "DormioStay"
PROJECT_REGION = "ap-southeast-1"
STAGING_BRANCH_ID = "br-weathered-dust-bim5fibs"
STAGING_BRANCH_NAME = "staging"
PRODUCTION_BRANCH_ID = "br-holy-lake-bi0hc1lu"
PRODUCTION_BRANCH_NAME = "production"
EXPECTED_ENDPOINT_HOST = "ep-cold-smoke-bir1s74t.c-5.ap-southeast-1.aws.neon.tech"
EXPECTED_ENDPOINT_PORT = 5432
EXPECTED_DATABASE = "neondb"
ADMIN_ROLE = "neondb_owner"
APP_ROLE = "dormiostay_app"

KEYCHAIN_SERVICE = "dormiostay.neon.staging.dormiostay_app"
KEYCHAIN_ACCOUNT = "dormiostay_app"
DEFAULT_CHECKPOINT_FILE = ".neon_staging_provisioning_checkpoint.json"

# Network & Containment Deadlines
DEFAULT_HTTP_TIMEOUT: float = 10.0
DEFAULT_MAX_RESPONSE_BYTES: int = 2 * 1024 * 1024  # 2MB
DEFAULT_CHUNK_SIZE: int = 64 * 1024  # 64KB
CONTAINMENT_OVERALL_DEADLINE: float = 15.0

# macOS Security.framework OSStatus error codes & constants
ERR_SEC_SUCCESS = 0
ERR_SEC_ITEM_NOT_FOUND = -25300
ERR_SEC_AUTH_FAILED = -25293
ERR_SEC_DUPLICATE_ITEM = -25299
ERR_SEC_INTERACTION_NOT_ALLOWED = -25308
ERR_SEC_PARAM = -50
# 'genp' = 0x67656e70 = 1734700656
# (FourCharCode for kSecGenericPasswordItemClass in SecKeychainItem.h)
K_SEC_GENERIC_PASSWORD_ITEM_CLASS = 0x67656E70


class ProvisioningMode(str, enum.Enum):
    DRY_RUN = "dry-run"
    EXECUTE = "execute"
    CONTAIN = "contain"


class ProvisioningState(str, enum.Enum):
    INITIALIZED = "INITIALIZED"
    METADATA_VERIFIED = "METADATA_VERIFIED"
    BASELINE_VERIFIED = "BASELINE_VERIFIED"
    ROLE_CREATED_NOLOGIN = "ROLE_CREATED_NOLOGIN"
    STRUCTURAL_CHECK_PASSED = "STRUCTURAL_CHECK_PASSED"
    PASSWORD_RESET_SUBMITTED = "PASSWORD_RESET_SUBMITTED"
    OPERATIONS_POLLED_FINISHED = "OPERATIONS_POLLED_FINISHED"
    PASSWORD_RESET_SUCCEEDED = "PASSWORD_RESET_SUCCEEDED"
    SECRET_STORED = "SECRET_STORED"
    SECRET_VERIFIED = "SECRET_VERIFIED"
    ADMIN_RECONNECTED = "ADMIN_RECONNECTED"
    TARGET_REVERIFIED_PRE_LOGIN = "TARGET_REVERIFIED_PRE_LOGIN"
    PRIVILEGES_VERIFIED = "PRIVILEGES_VERIFIED"
    LOGIN_ENABLED = "LOGIN_ENABLED"
    AUTH_VERIFIED = "AUTH_VERIFIED"
    DRY_RUN_COMPLETED = "DRY_RUN_COMPLETED"
    COMPLETED = "COMPLETED"
    CONTAINMENT_EXECUTED = "CONTAINMENT_EXECUTED"
    CONTAINMENT_FAILED = "CONTAINMENT_FAILED"
    FAILED = "FAILED"


ALLOWED_CHECKPOINT_KEYS: frozenset[str] = frozenset(
    {
        "state",
        "timestamp",
        "project_id",
        "branch_id",
        "endpoint_host",
        "step_number",
        "details",
    }
)

ISO_UTC_TIMESTAMP_REGEX = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$"
)


def _is_valid_uuid(val: Any) -> bool:
    """Validate string representation of UUID."""
    if not isinstance(val, str):
        return False
    try:
        uuid_obj = uuid.UUID(val)
        return str(uuid_obj).lower() == val.lower()
    except (ValueError, AttributeError):
        return False


async def _safe_close_or_terminate(conn: Any, timeout: float = 3.0) -> None:
    """Safely close connection within timeout; fallback to terminate() to prevent hangs."""
    if conn is None:
        return
    term_fn = getattr(conn, "terminate", None)
    close_fn = getattr(conn, "close", None)
    close_succeeded = False
    if callable(close_fn):
        try:
            close_coro = close_fn()
            if asyncio.iscoroutine(close_coro):
                await asyncio.wait_for(close_coro, timeout=timeout)
                close_succeeded = True
            else:
                close_succeeded = True
        except (TimeoutError, Exception):
            close_succeeded = False
        except asyncio.CancelledError:
            close_succeeded = False
            if callable(term_fn):
                try:
                    res = term_fn()
                    if asyncio.iscoroutine(res):
                        await asyncio.wait_for(res, timeout=1.0)
                except Exception:
                    pass
            raise

    if not close_succeeded and callable(term_fn):
        try:
            res = term_fn()
            if asyncio.iscoroutine(res):
                await asyncio.wait_for(res, timeout=1.0)
        except Exception:
            pass


class SecretRedactor:
    """Manages secret registration and scrubbing across all output paths."""

    def __init__(self) -> None:
        self._secrets: set[str] = set()
        self._patterns: list[re.Pattern[str]] = []

    def _build_percent_pattern(self, quoted: str) -> re.Pattern[str] | None:
        parts: list[str] = []
        i = 0
        has_escapes = False
        while i < len(quoted):
            if (
                quoted[i] == "%"
                and i + 2 < len(quoted)
                and all(c in "0123456789abcdefABCDEF" for c in quoted[i + 1 : i + 3])
            ):
                has_escapes = True
                h1 = quoted[i + 1]
                h2 = quoted[i + 2]
                p1 = f"[{h1.lower()}{h1.upper()}]" if h1.isalpha() else h1
                p2 = f"[{h2.lower()}{h2.upper()}]" if h2.isalpha() else h2
                parts.append(f"%{p1}{p2}")
                i += 3
            else:
                parts.append(re.escape(quoted[i]))
                i += 1
        if has_escapes:
            try:
                return re.compile("".join(parts))
            except re.error:
                return None
        return None

    def register(self, secret: str | None) -> None:
        if secret and len(secret) >= 4:
            self._secrets.add(secret)
            try:
                for q in (
                    urllib.parse.quote(secret),
                    urllib.parse.quote_plus(secret),
                    urllib.parse.quote(secret, safe=""),
                ):
                    if q != secret:
                        self._secrets.add(q)
                        pat = self._build_percent_pattern(q)
                        if pat is not None:
                            self._patterns.append(pat)
            except Exception:
                pass

    def contains_secret(self, text: str) -> bool:
        """Check if any registered secret or pattern appears in text."""
        if not text:
            return False
        for secret in self._secrets:
            if secret in text:
                return True
        for pat in self._patterns:
            if pat.search(text):
                return True
        return False

    def redact(self, text: str) -> str:
        if not text:
            return ""
        for pat in self._patterns:
            text = pat.sub("[REDACTED]", text)
        for secret in sorted(self._secrets, key=len, reverse=True):
            text = text.replace(secret, "[REDACTED]")
        # Regex patterns for connection strings, tokens, and clauses
        text = re.sub(
            r"postgres(ql)?:\/\/[^:\s]+:[^@\s]+@([^\/\s:]+)",
            r"postgresql://***:***@\2",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"(Bearer\s+)[A-Za-z0-9_\-\.]{10,}", r"\1***", text)
        text = re.sub(r"(npg_[A-Za-z0-9_\-]{8,})", r"***", text)
        text = re.sub(r"(PASSWORD\s+')[^']+(')", r"\1[REDACTED]\2", text, flags=re.IGNORECASE)
        text = re.sub(r'("password":\s*")[^"]+(")', r"\1[REDACTED]\2", text, flags=re.IGNORECASE)
        text = re.sub(r"(token=)[A-Za-z0-9_\-\.]{10,}", r"\1[REDACTED]", text, flags=re.IGNORECASE)
        return text


GLOBAL_REDACTOR = SecretRedactor()


def _sanitize_text(text: str) -> str:
    """Convenience scrubber using the global redactor."""
    return GLOBAL_REDACTOR.redact(text)


def _is_exact_bool(val: Any, expected: bool) -> bool:
    """Assert value is strictly boolean type and matches expected bool value."""
    return type(val) is bool and val is expected


def _is_exact_zero_int(val: Any) -> bool:
    """Assert value is strictly integer type (not bool) and equals zero."""
    return type(val) is int and not isinstance(val, bool) and val == 0


def _verify_client_tls(conn: Any) -> bool:
    """Verify that client connection actively uses TLS.

    Inspects asyncpg protocol and transport internals, failing closed if no active
    TLS session is verified.
    """
    is_ssl_fn = getattr(conn, "is_ssl", None)
    if callable(is_ssl_fn):
        return bool(is_ssl_fn() is True)

    protocol = getattr(conn, "_protocol", None)
    if protocol is not None and getattr(protocol, "is_ssl", False) is True:
        return True

    transport = getattr(conn, "_transport", None)
    if transport is not None and hasattr(transport, "get_extra_info"):
        ssl_obj = transport.get_extra_info("ssl_object")
        if ssl_obj is not None:
            return True

    return False


class ProvisioningError(Exception):
    """Base exception for provisioning failures. Sanitizes message to avoid credential leaks."""

    def __init__(self, message: str, sanitized_details: str | None = None) -> None:
        clean_msg = _sanitize_text(message)
        super().__init__(clean_msg)
        self.sanitized_details = _sanitize_text(sanitized_details) if sanitized_details else None


class TargetVerificationError(ProvisioningError):
    """Raised when remote target identity, TLS, or branch guard checks fail."""


class PrivilegeVerificationError(ProvisioningError):
    """Raised when PostgreSQL privilege verification assertions fail."""


class SecretStoreError(ProvisioningError):
    """Raised when storing or reading credentials in macOS Keychain fails."""


class ContainmentFailureError(ProvisioningError):
    """Raised when emergency containment fails to restore NOLOGIN."""


class OperationTimeoutError(ProvisioningError):
    """Raised when polling a Neon control plane operation times out."""


def map_db_error(exc: Exception) -> str:
    """Map database driver exception to fixed allowlisted diagnostic message without query text."""
    sqlstate = getattr(exc, "sqlstate", None)
    if sqlstate == "42501":
        return (
            "INSUFFICIENT_PRIVILEGE (SQLSTATE 42501): Database operation denied by privilege check."
        )
    if sqlstate == "28P01":
        return "INVALID_PASSWORD (SQLSTATE 28P01): Password authentication failed."
    if sqlstate == "28000":
        return "INVALID_AUTHORIZATION (SQLSTATE 28000): Invalid role specification."
    if sqlstate == "3D000":
        return "INVALID_DATABASE (SQLSTATE 3D000): Target database does not exist."
    if sqlstate == "42601":
        return "SYNTAX_ERROR (SQLSTATE 42601): Database query syntax error."
    if sqlstate == "42710":
        return "DUPLICATE_OBJECT (SQLSTATE 42710): Role or object already exists."
    if isinstance(exc, (ConnectionRefusedError, TimeoutError, OSError)):
        return "CONNECTION_FAILED: Unable to establish network connection to database host."
    return f"DATABASE_OPERATION_FAILED ({type(exc).__name__}): Operation could not be completed."


def map_http_error(code: int) -> str:
    """Map HTTP error to fixed allowlisted diagnostic string without dumping raw response bodies."""
    if code == 401:
        return "HTTP 401 Unauthorized: Neon API key invalid or expired."
    if code == 403:
        return "HTTP 403 Forbidden: Insufficient permissions for requested Neon resource."
    if code == 404:
        return "HTTP 404 Not Found: Specified Neon project, branch, or role does not exist."
    if code == 422:
        return "HTTP 422 Unprocessable Entity: Invalid parameters in Neon request."
    if code == 429:
        return "HTTP 429 Too Many Requests: Neon API rate limit reached."
    if code >= 500:
        return f"HTTP {code} Server Error: Neon control plane temporary service failure."
    return f"HTTP {code}: Neon API request failed."


@dataclass(frozen=True)
class CheckpointRecord:
    state: ProvisioningState
    timestamp: str
    project_id: str
    branch_id: str
    endpoint_host: str
    step_number: int
    details: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CheckpointManager:
    """Manages durable, atomic, strictly non-secret checkpoints to support recovery."""

    def __init__(
        self,
        filepath: str | Path = DEFAULT_CHECKPOINT_FILE,
        redactor: SecretRedactor | None = None,
    ) -> None:
        self.filepath = Path(filepath).absolute()
        self.lock_filepath = self.filepath.parent / f".{self.filepath.name}.lock"
        self.redactor = redactor or GLOBAL_REDACTOR

    def _verify_path_security(self) -> None:
        """Verify no symlinks in path/ancestors; check dir ownership and permissions."""
        curr: Path | None = self.filepath
        while curr is not None:
            if curr.is_symlink() or os.path.islink(curr):
                raise TargetVerificationError(f"Unsafe symlink in checkpoint path: '{curr}'")
            if curr.parent == curr:
                break
            curr = curr.parent

        parent_dir = self.filepath.parent
        if not parent_dir.exists():
            parent_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                os.chmod(parent_dir, 0o700)
            except OSError:
                pass
            st = parent_dir.stat()
            if (st.st_mode & 0o077) != 0:
                msg = (
                    f"Unsafe permissions on checkpoint directory '{parent_dir}': "
                    f"{oct(st.st_mode & 0o777)}. Must be 0700."
                )
                raise TargetVerificationError(msg)
        else:
            st = parent_dir.stat()
            if st.st_uid != os.getuid():
                msg = (
                    f"Checkpoint directory '{parent_dir}' owner mismatch: "
                    f"expected UID {os.getuid()}, got {st.st_uid}"
                )
                raise TargetVerificationError(msg)
            if (st.st_mode & 0o077) != 0:
                try:
                    os.chmod(parent_dir, 0o700)
                    st = parent_dir.stat()
                except OSError:
                    pass
                if (st.st_mode & 0o077) != 0:
                    msg = (
                        f"Unsafe permissions on checkpoint directory '{parent_dir}': "
                        f"{oct(st.st_mode & 0o777)}. Must be 0700."
                    )
                    raise TargetVerificationError(msg)

    def _validate_record_dict(self, item: Any) -> None:
        if not isinstance(item, dict):
            raise ProvisioningError(
                f"Checkpoint file '{self.filepath}' history corrupted (record not dict)."
            )
        extra_keys = set(item.keys()) - ALLOWED_CHECKPOINT_KEYS
        if extra_keys:
            raise ProvisioningError(
                f"Checkpoint file '{self.filepath}' history corrupted "
                f"(unexpected fields: {sorted(extra_keys)})."
            )
        missing_keys = ALLOWED_CHECKPOINT_KEYS - set(item.keys())
        if missing_keys:
            raise ProvisioningError(
                f"Checkpoint file '{self.filepath}' history corrupted "
                f"(missing fields: {sorted(missing_keys)})."
            )

        # Enforce zero secret contamination across all retained fields
        for k in ALLOWED_CHECKPOINT_KEYS:
            val = item.get(k)
            if self.redactor.contains_secret(str(val)):
                raise ProvisioningError(
                    f"Checkpoint file '{self.filepath}' history corrupted "
                    f"(secret credential detected in field '{k}')."
                )

        try:
            st = item.get("state")
            if not isinstance(st, str) or st not in ProvisioningState.__members__:
                raise ValueError("Invalid state")
            ts = item.get("timestamp")
            if not isinstance(ts, str) or not ts:
                raise ValueError("Invalid timestamp")
            if not ISO_UTC_TIMESTAMP_REGEX.match(ts):
                raise ValueError(f"Invalid timestamp format '{ts}'. Must be ISO 8601 UTC.")
            try:
                datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                raise ValueError(f"Invalid ISO datetime syntax in timestamp: '{ts}'") from None

            proj = item.get("project_id")
            if proj != PROJECT_ID:
                raise ValueError("Invalid project_id")
            branch = item.get("branch_id")
            if branch != STAGING_BRANCH_ID:
                raise ValueError("Invalid branch_id")
            ep = item.get("endpoint_host")
            if ep != EXPECTED_ENDPOINT_HOST:
                raise ValueError("Invalid endpoint_host")
            step = item.get("step_number")
            if type(step) is not int or isinstance(step, bool) or step < 0:
                raise ValueError("Invalid step_number")
            det = item.get("details")
            if not isinstance(det, str):
                raise ValueError("Invalid details")
        except Exception as schema_err:
            raise ProvisioningError(
                f"Checkpoint file '{self.filepath}' history corrupted "
                f"(invalid record schema: {schema_err})."
            ) from None

    def record(
        self,
        state: ProvisioningState,
        step_number: int,
        details: str,
        project_id: str = PROJECT_ID,
        branch_id: str = STAGING_BRANCH_ID,
        endpoint_host: str = EXPECTED_ENDPOINT_HOST,
    ) -> CheckpointRecord:
        self._verify_path_security()
        parent_dir = self.filepath.parent

        record = CheckpointRecord(
            state=state,
            timestamp=datetime.datetime.now(datetime.UTC).isoformat(),
            project_id=project_id,
            branch_id=branch_id,
            endpoint_host=endpoint_host,
            step_number=step_number,
            details=self.redactor.redact(details),
        )

        lock_fd = os.open(
            self.lock_filepath,
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
            0o600,
        )
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                data: list[dict[str, Any]] = []
                if self.filepath.exists():
                    self._verify_path_security()
                    try:
                        with open(self.filepath, encoding="utf-8") as f:
                            content = f.read()
                        loaded = json.loads(content)
                        if not isinstance(loaded, list):
                            msg = (
                                f"Checkpoint file '{self.filepath}' history corrupted: "
                                "expected list."
                            )
                            raise ProvisioningError(msg)
                        for item in loaded:
                            self._validate_record_dict(item)
                            item["details"] = self.redactor.redact(item["details"])
                        data = loaded
                    except json.JSONDecodeError:
                        msg = f"Checkpoint file '{self.filepath}' history corrupted (invalid JSON)."
                        raise ProvisioningError(msg) from None

                rec_json = json.dumps(record.to_dict())
                scrubbed_rec_json = self.redactor.redact(rec_json)
                safe_dict = json.loads(scrubbed_rec_json)
                data.append(safe_dict)

                serialized = json.dumps(data, indent=2)
                tmp_filename = f".tmp.{os.getpid()}.{secrets.token_hex(6)}"
                tmp_path = parent_dir / tmp_filename

                tmp_fd = os.open(
                    tmp_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                )
                try:
                    with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmp_f:
                        tmp_f.write(serialized)
                        tmp_f.flush()
                        os.fsync(tmp_f.fileno())

                    os.replace(tmp_path, self.filepath)
                    try:
                        os.chmod(self.filepath, 0o600)
                    except OSError:
                        pass

                    try:
                        dir_fd = os.open(parent_dir, os.O_RDONLY)
                        try:
                            os.fsync(dir_fd)
                        finally:
                            os.close(dir_fd)
                    except OSError as dir_err:
                        raise ProvisioningError(
                            f"Checkpoint directory fsync failed: {type(dir_err).__name__}"
                        ) from None
                except BaseException as write_err:
                    if tmp_path.exists():
                        try:
                            tmp_path.unlink()
                        except OSError:
                            pass
                    if isinstance(
                        write_err,
                        (ProvisioningError, KeyboardInterrupt, asyncio.CancelledError),
                    ):
                        raise
                    raise ProvisioningError(
                        f"Checkpoint persistence failed: {type(write_err).__name__}"
                    ) from None
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)

        return record

    def get_latest_checkpoint(self) -> CheckpointRecord | None:
        self._verify_path_security()
        if not self.filepath.exists():
            return None

        lock_fd = os.open(
            self.lock_filepath,
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
            0o600,
        )
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_SH)
            try:
                self._verify_path_security()
                with open(self.filepath, encoding="utf-8") as f:
                    content = f.read()
                data = json.loads(content)
                if not isinstance(data, list):
                    msg = f"Checkpoint file '{self.filepath}' history corrupted: expected list."
                    raise ProvisioningError(msg)
                for item in data:
                    self._validate_record_dict(item)
                    item["details"] = self.redactor.redact(item["details"])
                if data:
                    last = data[-1]
                    return CheckpointRecord(
                        state=ProvisioningState(last["state"]),
                        timestamp=last["timestamp"],
                        project_id=last["project_id"],
                        branch_id=last["branch_id"],
                        endpoint_host=last["endpoint_host"],
                        step_number=last["step_number"],
                        details=last["details"],
                    )
            except json.JSONDecodeError:
                msg = f"Checkpoint file '{self.filepath}' history corrupted (invalid JSON)."
                raise ProvisioningError(msg) from None
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)
        return None


class StrictNoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Strict HTTP redirect handler that rejects all redirects to prevent credential forwarding."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        raise TargetVerificationError(f"HTTP Redirect ({code}) forbidden by security policy.")


class NeonApiProtocol(Protocol):
    """Protocol for interacting with the Neon Control Plane API."""

    def get_project(self, project_id: str) -> dict[str, Any]: ...

    def list_branches(self, project_id: str) -> list[dict[str, Any]]: ...

    def list_endpoints(self, project_id: str) -> list[dict[str, Any]]: ...

    def reset_role_password(
        self, project_id: str, branch_id: str, role_name: str
    ) -> dict[str, Any]: ...

    def get_operation(self, project_id: str, operation_id: str) -> dict[str, Any]: ...


def _is_socket_like(obj: Any) -> bool:
    """Validate whether an object conforms to a socket interface without running arbitrary code."""
    if obj is None:
        return False
    if isinstance(obj, (socket.socket, ssl.SSLSocket)):
        return True
    return (
        hasattr(obj, "shutdown")
        and callable(getattr(obj, "shutdown", None))
        and hasattr(obj, "close")
        and callable(getattr(obj, "close", None))
        and (hasattr(obj, "fileno") or hasattr(obj, "settimeout"))
    )


class _ActiveRequestHolder:
    """Thread-safe holder for interruptible HTTP operations with explicit resource ownership."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sock: Any = None
        self.resp: Any = None
        self.interrupted: bool = False
        self.socket_identified: bool = False
        self.owns_socket: bool = True
        self.owns_resp: bool = True
        self.shutdown_performed: bool = False
        self.socket_closed: bool = False
        self.resp_closed: bool = False
        self.interruption_guaranteed: bool = True

    def register(self, resp: Any = None, sock: Any = None) -> None:
        """Register active response wrapper and underlying socket."""
        with self.lock:
            self.resp = resp
            self.sock = sock
            self.socket_identified = sock is not None
            if resp is not None and sock is None:
                self.interruption_guaranteed = False
            if self.interrupted:
                self._terminate_locked()

    def interrupt(self) -> bool:
        """Interrupt active request by shutting down socket before closing response wrapper.

        Returns True if socket interruption was initiated; False if socket was missing
        and interruption cannot be guaranteed.
        """
        with self.lock:
            self.interrupted = True
            return self._terminate_locked()

    def _terminate_locked(self) -> bool:
        """Execute teardown: socket shutdown first, socket close second, response close third.

        Handles socket shutdown, socket close, and response close failures independently
        to prevent double-close or connection-reset errors from masking cleanup.
        """
        guaranteed = False

        # 1. Shut down socket FIRST to force kernel to unblock any thread waiting in recv()
        if self.sock is not None and self.owns_socket:
            if not self.shutdown_performed:
                try:
                    self.sock.shutdown(socket.SHUT_RDWR)
                    self.shutdown_performed = True
                except (OSError, ValueError):
                    pass
                except Exception:
                    pass

            if not self.socket_closed:
                try:
                    self.sock.close()
                    self.socket_closed = True
                except (OSError, ValueError):
                    pass
                except Exception:
                    pass
            guaranteed = True
            self.interruption_guaranteed = True
        else:
            guaranteed = False
            self.interruption_guaranteed = False

        # 2. Close response wrapper second
        if self.resp is not None and self.owns_resp and not self.resp_closed:
            try:
                self.resp.close()
                self.resp_closed = True
            except (OSError, ValueError):
                pass
            except Exception:
                pass

        return guaranteed


def _extract_socket(resp: Any) -> Any:
    """Extract underlying socket descriptor from urllib/http response if accessible.

    Resolves the socket through supported standard-library and wrapper paths:
    - response.sock / response._sock
    - response.fp (e.g. http.client.HTTPResponse)
    - HTTPResponse.fp (e.g. _io.BufferedReader)
    - BufferedReader.raw (e.g. socket.SocketIO)
    - SocketIO._sock (socket.socket / ssl.SSLSocket)

    Does not assume identical nesting across all Python versions or test mocks.
    Returns the socket candidate if identified, or None if no socket can be verified.
    """
    if resp is None:
        return None

    if _is_socket_like(resp):
        return resp

    # Check direct attributes on response
    for attr in ("sock", "_sock"):
        cand = getattr(resp, attr, None)
        if _is_socket_like(cand):
            return cand

    # Bounded traversal across known wrapper attributes: fp, raw
    curr: Any = resp
    for _ in range(5):
        fp = getattr(curr, "fp", None)
        if fp is not None:
            for attr in ("sock", "_sock"):
                cand = getattr(fp, attr, None)
                if _is_socket_like(cand):
                    return cand

            raw = getattr(fp, "raw", None)
            if raw is not None:
                for attr in ("_sock", "sock"):
                    cand = getattr(raw, attr, None)
                    if _is_socket_like(cand):
                        return cand

            curr = fp
            continue

        raw = getattr(curr, "raw", None)
        if raw is not None:
            for attr in ("_sock", "sock"):
                cand = getattr(raw, attr, None)
                if _is_socket_like(cand):
                    return cand
            curr = raw
            continue

        break

    return None


class NeonApiClient:
    """Production implementation of Neon Control Plane API with strict TLS and no redirects."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://console.neon.tech/api/v2",
        redactor: SecretRedactor | None = None,
        timeout: float = DEFAULT_HTTP_TIMEOUT,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> None:
        if not api_key:
            raise TargetVerificationError("NEON_API_KEY is required for control plane verification")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.redactor = redactor or GLOBAL_REDACTOR
        self.redactor.register(api_key)
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.chunk_size = chunk_size

        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme.lower() != "https":
            raise TargetVerificationError(
                f"Neon API base URL must use HTTPS. Insecure scheme '{parsed.scheme}' rejected."
            )

        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = True
        self.ssl_context.verify_mode = ssl.CERT_REQUIRED

        self.opener = urllib.request.build_opener(
            StrictNoRedirectHandler, urllib.request.HTTPSHandler(context=self.ssl_context)
        )
        self.last_worker_thread: threading.Thread | None = None

    def _execute_http_sync(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        timeout: float | None = None,
        cancel_event: threading.Event | None = None,
        holder: _ActiveRequestHolder | None = None,
    ) -> dict[str, Any]:
        req_timeout = timeout if timeout is not None else self.timeout
        start_time = time.monotonic()
        deadline = start_time + req_timeout

        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "User-Agent": "DormioStay-Provisioning/1.0",
        }
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(url, data=data, headers=headers, method=method)

        if cancel_event is not None and cancel_event.is_set():
            raise TargetVerificationError("Neon API request aborted by cancellation.")

        rem_connect = max(0.001, deadline - time.monotonic())
        if rem_connect <= 0.001 and (time.monotonic() >= deadline):
            raise TargetVerificationError(
                f"Neon API request failed: TIMEOUT: Request exceeded deadline "
                f"before connect ({req_timeout}s)."
            )

        try:
            resp = self.opener.open(req, timeout=rem_connect)
        except urllib.error.HTTPError as e:
            err_msg = map_http_error(e.code)
            raise TargetVerificationError(f"Neon API request failed: {err_msg}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            if isinstance(e, TimeoutError) or "timed out" in str(e).lower():
                raise TargetVerificationError(
                    f"Neon API request failed: TIMEOUT: "
                    f"Connection or headers timed out ({req_timeout}s)."
                ) from None
            raise TargetVerificationError(
                "Neon API connection failed: NETWORK_ERROR (URLError): "
                "Unable to reach Neon control plane."
            ) from None
        except Exception:
            if cancel_event is not None and cancel_event.is_set():
                raise TargetVerificationError(
                    "Neon API request interrupted by cancellation."
                ) from None
            raise TargetVerificationError(
                "Neon API request failed: UNEXPECTED_NETWORK_ERROR: "
                "Communication with Neon control plane failed."
            ) from None

        sock = _extract_socket(resp)
        if holder is not None:
            holder.register(resp=resp, sock=sock)

        try:
            chunks: list[bytes] = []
            total_bytes = 0
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise TargetVerificationError("Neon API request interrupted by cancellation.")

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TargetVerificationError(
                        f"Neon API request failed: TIMEOUT: Response body read exceeded absolute "
                        f"deadline of {req_timeout}s."
                    )

                if sock is not None and hasattr(sock, "settimeout"):
                    try:
                        # Slice socket read timeout to at most 0.1s (or remaining deadline)
                        # so that cancel_event and deadlines are evaluated promptly without
                        # blocking indefinitely in kernel recv/select.
                        sock.settimeout(min(0.1, max(0.001, remaining)))
                    except Exception:
                        pass

                read_size = min(self.chunk_size, self.max_response_bytes - total_bytes + 1)
                try:
                    chunk = resp.read(read_size)
                except TimeoutError:
                    if cancel_event is not None and cancel_event.is_set():
                        raise TargetVerificationError(
                            "Neon API request interrupted by cancellation."
                        ) from None
                    if time.monotonic() >= deadline:
                        elapsed = time.monotonic() - start_time
                        raise TargetVerificationError(
                            f"Neon API request failed: TIMEOUT: "
                            f"Response read timed out after {elapsed:.2f}s."
                        ) from None
                    continue
                except (OSError, ValueError):
                    if cancel_event is not None and cancel_event.is_set():
                        raise TargetVerificationError(
                            "Neon API request interrupted by cancellation."
                        ) from None
                    raise TargetVerificationError(
                        "Neon API request failed: UNEXPECTED_NETWORK_ERROR: "
                        "Communication with Neon control plane failed."
                    ) from None

                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > self.max_response_bytes:
                    raise ProvisioningError(
                        f"Neon API response exceeded maximum allowed size of "
                        f"{self.max_response_bytes} bytes."
                    )
                chunks.append(chunk)

            raw_body = b"".join(chunks).decode("utf-8")
            try:
                parsed_json = json.loads(raw_body)
            except Exception:
                raise ProvisioningError("Neon API returned non-JSON response") from None
            if not isinstance(parsed_json, dict):
                raise ProvisioningError("Neon API returned non-object JSON response")
            return dict(parsed_json)
        finally:
            if sock is not None:
                try:
                    sock.close()
                except (OSError, ValueError):
                    pass
                except Exception:
                    pass
            try:
                resp.close()
            except (OSError, ValueError):
                pass
            except Exception:
                pass

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Synchronous HTTP request with absolute deadline and bounded reads.

        Fails closed if invoked directly on a running asyncio event loop to prevent
        blocking network I/O from halting the loop.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            raise TargetVerificationError(
                "Blocking synchronous HTTP request cannot run directly on the asyncio event loop. "
                "Use async_request or await async methods."
            )

        return self._execute_http_sync(method=method, path=path, payload=payload, timeout=timeout)

    async def async_request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Asynchronous HTTP request executed in an interruptible thread with an absolute deadline.

        If cancelled or timed out, actively shuts down and closes the underlying socket and
        joins the worker thread, ensuring zero orphaned tasks or connections.
        """
        req_timeout = timeout if timeout is not None else self.timeout
        holder = _ActiveRequestHolder()
        cancel_event = threading.Event()
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()

        # Prevent unretrieved future exception warnings if worker completes after cancellation
        def _consume_future_exception(f: asyncio.Future[Any]) -> None:
            if not f.cancelled():
                try:
                    f.exception()
                except BaseException:
                    pass

        fut.add_done_callback(_consume_future_exception)

        def _worker() -> None:
            try:
                res = self._execute_http_sync(
                    method=method,
                    path=path,
                    payload=payload,
                    timeout=req_timeout,
                    cancel_event=cancel_event,
                    holder=holder,
                )
                if not cancel_event.is_set() and not fut.done():

                    def _set_res(r: dict[str, Any] = res) -> None:
                        if not fut.done():
                            fut.set_result(r)

                    loop.call_soon_threadsafe(_set_res)
            except BaseException as exc:
                if not cancel_event.is_set() and not fut.done():

                    def _set_exc(err: BaseException = exc) -> None:
                        if not fut.done():
                            fut.set_exception(err)

                    loop.call_soon_threadsafe(_set_exc)

        thread = threading.Thread(target=_worker, name="NeonApiClient-Worker", daemon=True)
        self.last_worker_thread = thread
        thread.start()

        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout=req_timeout)
        except (asyncio.CancelledError, TimeoutError) as err:
            cancel_event.set()
            interruption_guaranteed = holder.interrupt()

            # Settle worker completion without blocking the event loop.
            # Do NOT call thread.join() synchronously on the event loop,
            # which would freeze concurrent coroutines (e.g. 10ms heartbeats).
            # Give interruptible I/O a bounded non-blocking window (up to 300ms in 5ms slices)
            # to exit cleanly as soon as the socket slice unblocks.
            settle_deadline = loop.time() + 0.3
            caller_cancelled = isinstance(err, asyncio.CancelledError)

            while thread.is_alive() and loop.time() < settle_deadline:
                try:
                    await asyncio.sleep(0.005)
                except (asyncio.CancelledError, KeyboardInterrupt):
                    caller_cancelled = True

            # If worker thread remains alive, report critical uncertainty truthfully.
            if thread.is_alive():
                prefix = (
                    f"Neon API request failed: TIMEOUT: Worker thread {thread.name} did not "
                    f"terminate after deadline ({req_timeout}s) exceeded"
                    if isinstance(err, TimeoutError) and not caller_cancelled
                    else (
                        f"Neon API request cancellation failed: Worker thread {thread.name} "
                        f"did not terminate after cancellation"
                    )
                )
                curr_task = asyncio.current_task()
                if curr_task is not None and hasattr(curr_task, "uncancel"):
                    while curr_task.cancelling() > 0:
                        curr_task.uncancel()
                raise TargetVerificationError(
                    f"{prefix} (interruption_guaranteed={interruption_guaranteed}); "
                    "response state unknown. Manual verification required before retrying. "
                    "Critical uncertainty: Emergency cleanup cannot be guaranteed; "
                    "thread remains active."
                ) from None

            if caller_cancelled:
                raise asyncio.CancelledError() from None
            raise TargetVerificationError(
                f"Neon API request failed: TIMEOUT: Absolute deadline of {req_timeout}s exceeded."
            ) from None
        finally:
            if not fut.done():
                fut.cancel()

    def get_project(self, project_id: str) -> dict[str, Any]:
        return self._request("GET", f"/projects/{project_id}")

    async def async_get_project(
        self, project_id: str, timeout: float | None = None
    ) -> dict[str, Any]:
        return await self.async_request("GET", f"/projects/{project_id}", timeout=timeout)

    def list_branches(self, project_id: str) -> list[dict[str, Any]]:
        res = self._request("GET", f"/projects/{project_id}/branches")
        branches = res.get("branches", [])
        if not isinstance(branches, list):
            return []
        return [b for b in branches if isinstance(b, dict)]

    async def async_list_branches(
        self, project_id: str, timeout: float | None = None
    ) -> list[dict[str, Any]]:
        res = await self.async_request("GET", f"/projects/{project_id}/branches", timeout=timeout)
        branches = res.get("branches", [])
        if not isinstance(branches, list):
            return []
        return [b for b in branches if isinstance(b, dict)]

    def list_endpoints(self, project_id: str) -> list[dict[str, Any]]:
        res = self._request("GET", f"/projects/{project_id}/endpoints")
        endpoints = res.get("endpoints", [])
        if not isinstance(endpoints, list):
            return []
        return [ep for ep in endpoints if isinstance(ep, dict)]

    async def async_list_endpoints(
        self, project_id: str, timeout: float | None = None
    ) -> list[dict[str, Any]]:
        res = await self.async_request("GET", f"/projects/{project_id}/endpoints", timeout=timeout)
        endpoints = res.get("endpoints", [])
        if not isinstance(endpoints, list):
            return []
        return [ep for ep in endpoints if isinstance(ep, dict)]

    def reset_role_password(
        self, project_id: str, branch_id: str, role_name: str
    ) -> dict[str, Any]:
        path = f"/projects/{project_id}/branches/{branch_id}/roles/{role_name}/reset_password"
        return self._request("POST", path)

    async def async_reset_role_password(
        self, project_id: str, branch_id: str, role_name: str, timeout: float | None = None
    ) -> dict[str, Any]:
        path = f"/projects/{project_id}/branches/{branch_id}/roles/{role_name}/reset_password"
        return await self.async_request("POST", path, timeout=timeout)

    def get_operation(self, project_id: str, operation_id: str) -> dict[str, Any]:
        return self._request("GET", f"/projects/{project_id}/operations/{operation_id}")

    async def async_get_operation(
        self, project_id: str, operation_id: str, timeout: float | None = None
    ) -> dict[str, Any]:
        return await self.async_request(
            "GET", f"/projects/{project_id}/operations/{operation_id}", timeout=timeout
        )


class DatabaseConnectorProtocol(Protocol):
    """Protocol for establishing TLS-verified PostgreSQL connections."""

    async def connect(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        ssl_context: ssl.SSLContext | None = None,
    ) -> Any: ...


class DatabaseConnector:
    """Production database connector enforcing TLS certificate and hostname verification."""

    def __init__(self, redactor: SecretRedactor | None = None) -> None:
        self.redactor = redactor or GLOBAL_REDACTOR
        self.last_backend_ssl_active: bool | None = None

    def build_ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx

    async def connect(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        ssl_context: ssl.SSLContext | None = None,
    ) -> Any:
        if host != EXPECTED_ENDPOINT_HOST:
            msg = (
                f"Target host '{host}' rejected. "
                f"Must match expected endpoint host '{EXPECTED_ENDPOINT_HOST}'."
            )
            raise TargetVerificationError(msg)
        if port != EXPECTED_ENDPOINT_PORT:
            msg = (
                f"Target port '{port}' rejected. "
                f"Must match expected endpoint port '{EXPECTED_ENDPOINT_PORT}'."
            )
            raise TargetVerificationError(msg)
        if database != EXPECTED_DATABASE:
            msg = (
                f"Target database '{database}' rejected. "
                f"Must match expected database '{EXPECTED_DATABASE}'."
            )
            raise TargetVerificationError(msg)

        if ssl_context is None:
            ssl_context = self.build_ssl_context()
        elif not ssl_context.check_hostname or ssl_context.verify_mode != ssl.CERT_REQUIRED:
            raise TargetVerificationError(
                "Insecure SSL context rejected: check_hostname and CERT_REQUIRED required."
            )

        self.redactor.register(password)

        try:
            conn = await asyncpg.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                database=database,
                ssl=ssl_context,
                server_settings={"application_name": "dormiostay_provisioning"},
                timeout=15.0,
            )
        except Exception as e:
            err_msg = map_db_error(e)
            raise TargetVerificationError(f"Database connection failed: {err_msg}") from None

        try:
            row = await conn.fetchrow(
                "SELECT current_user, session_user, current_database(), "
                "(SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()) AS ssl_active;"
            )
            if not row:
                raise TargetVerificationError(
                    "Post-connection verification query returned empty row."
                )
            c_user, s_user, c_db, ssl_active = row[0], row[1], row[2], row[3]
            if c_user != user or s_user != user:
                raise TargetVerificationError(
                    f"User identity mismatch: current_user='{c_user}', "
                    f"session_user='{s_user}', expected '{user}'"
                )
            if c_db != database:
                raise TargetVerificationError(
                    f"Database identity mismatch: connected to '{c_db}', expected '{database}'"
                )
            if not _verify_client_tls(conn):
                raise TargetVerificationError(
                    "Client connection reports TLS is not active. Insecure connection terminated."
                )
            self.last_backend_ssl_active = (
                bool(ssl_active) if isinstance(ssl_active, bool) else None
            )
            return conn
        except BaseException as verify_err:
            await _safe_close_or_terminate(conn, timeout=3.0)
            if isinstance(verify_err, TargetVerificationError):
                raise
            if isinstance(verify_err, (asyncio.CancelledError, KeyboardInterrupt)):
                raise
            if isinstance(verify_err, Exception):
                err_msg = map_db_error(verify_err)
                raise TargetVerificationError(
                    f"Post-connection verification failed: {err_msg}"
                ) from None
            raise


class SecretStoreProtocol(Protocol):
    """Protocol for secure local credential storage."""

    def entry_exists(self) -> bool: ...

    def store_password(self, password: str, allow_overwrite: bool = False) -> str: ...

    def get_password(self) -> str: ...

    def verify_stored_password(self, expected_password: str | None = None) -> bool: ...


class SecKeychainAttribute(ctypes.Structure):
    _fields_ = [
        ("tag", ctypes.c_uint32),
        ("length", ctypes.c_uint32),
        ("data", ctypes.c_void_p),
    ]


class SecKeychainAttributeList(ctypes.Structure):
    _fields_ = [
        ("count", ctypes.c_uint32),
        ("attr", ctypes.POINTER(SecKeychainAttribute)),
    ]


class MacOSSecurityFrameworkAdapter:
    """Audited native macOS Security.framework interface via ctypes (zero CLI args, zero shell)."""

    def __init__(
        self,
        service: str,
        account: str,
        redactor: SecretRedactor | None = None,
        sec_lib: Any | None = None,
        cf_lib: Any | None = None,
        keychain: Any | None = None,
    ) -> None:
        self.service = service
        self.account = account
        self.redactor = redactor or GLOBAL_REDACTOR
        self.owns_keychain = False
        self.keychain = None

        if sec_lib is not None and cf_lib is not None:
            self.sec = sec_lib
            self.cf = cf_lib
        else:
            sec_path = ctypes.util.find_library("Security")
            cf_path = ctypes.util.find_library("CoreFoundation")
            if not sec_path or not cf_path:
                msg = "macOS Security/CoreFoundation libraries not found on system."
                raise SecretStoreError(msg)

            self.sec = ctypes.cdll.LoadLibrary(sec_path)
            self.cf = ctypes.cdll.LoadLibrary(cf_path)
        self._setup_function_signatures()

        if keychain is not None:
            val = getattr(keychain, "value", keychain)
            if not val:
                raise SecretStoreError("Invalid explicit null Keychain handle provided.")
            self.keychain = keychain
            self.owns_keychain = False
        else:
            if not hasattr(self.sec, "SecKeychainCopyDefault"):
                raise SecretStoreError("SecKeychainCopyDefault not available on Security library.")
            kc_ref = ctypes.c_void_p()
            try:
                status = self.sec.SecKeychainCopyDefault(ctypes.byref(kc_ref))
            except Exception as e:
                raise SecretStoreError(f"SecKeychainCopyDefault invocation failed: {e}") from None

            if status != ERR_SEC_SUCCESS:
                raise SecretStoreError(f"SecKeychainCopyDefault failed with status code {status}.")
            if not kc_ref.value:
                raise SecretStoreError("SecKeychainCopyDefault returned NULL Keychain reference.")
            self.keychain = kc_ref
            self.owns_keychain = True

    def _assert_valid_keychain(self) -> None:
        if self.keychain is None:
            raise SecretStoreError("Unpinned or missing Keychain scope. Operation denied.")
        val = getattr(self.keychain, "value", self.keychain)
        if not val:
            raise SecretStoreError("Invalid null Keychain reference. Operation denied.")

    def close(self) -> None:
        """Release owned native Keychain reference if acquired by adapter."""
        if self.owns_keychain and self.keychain is not None:
            val = getattr(self.keychain, "value", self.keychain)
            if val and hasattr(self, "cf") and hasattr(self.cf, "CFRelease"):
                try:
                    self.cf.CFRelease(self.keychain)
                except Exception:
                    pass
            self.keychain = None
            self.owns_keychain = False

    def __del__(self) -> None:
        self.close()

    def _setup_function_signatures(self) -> None:
        try:
            if hasattr(self.sec, "SecKeychainCopyDefault"):
                self.sec.SecKeychainCopyDefault.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
                self.sec.SecKeychainCopyDefault.restype = ctypes.c_int32

            self.sec.SecKeychainAddGenericPassword.argtypes = [
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.c_char_p,
                ctypes.c_uint32,
                ctypes.c_char_p,
                ctypes.c_uint32,
                ctypes.c_char_p,
                ctypes.POINTER(ctypes.c_void_p),
            ]
            self.sec.SecKeychainAddGenericPassword.restype = ctypes.c_int32

            self.sec.SecKeychainFindGenericPassword.argtypes = [
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.c_char_p,
                ctypes.c_uint32,
                ctypes.c_char_p,
                ctypes.POINTER(ctypes.c_uint32),
                ctypes.POINTER(ctypes.c_void_p),
                ctypes.POINTER(ctypes.c_void_p),
            ]
            self.sec.SecKeychainFindGenericPassword.restype = ctypes.c_int32

            self.sec.SecKeychainItemFreeContent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            self.sec.SecKeychainItemFreeContent.restype = ctypes.c_int32

            self.sec.SecKeychainItemModifyContent.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.c_char_p,
            ]
            self.sec.SecKeychainItemModifyContent.restype = ctypes.c_int32

            self.sec.SecKeychainItemCopyContent.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_uint32),
                ctypes.POINTER(ctypes.c_void_p),
            ]
            self.sec.SecKeychainItemCopyContent.restype = ctypes.c_int32

            self.cf.CFRelease.argtypes = [ctypes.c_void_p]
            self.cf.CFRelease.restype = None

            if hasattr(self.sec, "SecKeychainSearchCreateFromAttributes"):
                self.sec.SecKeychainSearchCreateFromAttributes.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_uint32,
                    ctypes.POINTER(SecKeychainAttributeList),
                    ctypes.POINTER(ctypes.c_void_p),
                ]
                self.sec.SecKeychainSearchCreateFromAttributes.restype = ctypes.c_int32

            if hasattr(self.sec, "SecKeychainSearchCopyNext"):
                self.sec.SecKeychainSearchCopyNext.argtypes = [
                    ctypes.c_void_p,
                    ctypes.POINTER(ctypes.c_void_p),
                ]
                self.sec.SecKeychainSearchCopyNext.restype = ctypes.c_int32
        except (AttributeError, TypeError):
            pass

    def _check_ambiguous_matches(self) -> None:
        """Reject ambiguous matches if multiple generic password items match service/account.

        Uses Apple Security.framework kSecGenericPasswordItemClass ('genp' = 0x67656e70)
        and validates native search-creation and iteration status, failing closed on all
        errors other than errSecItemNotFound.
        """
        self._assert_valid_keychain()
        if not (
            hasattr(self.sec, "SecKeychainSearchCreateFromAttributes")
            and hasattr(self.sec, "SecKeychainSearchCopyNext")
        ):
            return
        s_bytes = self.service.encode("utf-8")
        a_bytes = self.account.encode("utf-8")
        attrs = (SecKeychainAttribute * 2)(
            SecKeychainAttribute(
                0x73766365, len(s_bytes), ctypes.cast(ctypes.c_char_p(s_bytes), ctypes.c_void_p)
            ),
            SecKeychainAttribute(
                0x61636374, len(a_bytes), ctypes.cast(ctypes.c_char_p(a_bytes), ctypes.c_void_p)
            ),
        )
        attr_list = SecKeychainAttributeList(2, attrs)
        search_ref = ctypes.c_void_p()
        try:
            status = self.sec.SecKeychainSearchCreateFromAttributes(
                self.keychain,
                K_SEC_GENERIC_PASSWORD_ITEM_CLASS,
                ctypes.byref(attr_list),
                ctypes.byref(search_ref),
            )
            if status == ERR_SEC_ITEM_NOT_FOUND:
                return
            if status != ERR_SEC_SUCCESS:
                raise SecretStoreError(
                    f"SecKeychainSearchCreateFromAttributes failed with status code {status}."
                )
            if not search_ref.value:
                raise SecretStoreError(
                    "SecKeychainSearchCreateFromAttributes returned NULL search reference."
                )
            count = 0
            while True:
                item_ref = ctypes.c_void_p()
                next_status = self.sec.SecKeychainSearchCopyNext(search_ref, ctypes.byref(item_ref))
                if next_status == ERR_SEC_SUCCESS:
                    if not item_ref.value:
                        raise SecretStoreError(
                            "SecKeychainSearchCopyNext returned NULL item reference."
                        )
                    count += 1
                    self.cf.CFRelease(item_ref)
                    if count > 1:
                        raise SecretStoreError(
                            f"Ambiguous Keychain match: multiple items found for "
                            f"service '{self.service}' and account '{self.account}'."
                        )
                elif next_status == ERR_SEC_ITEM_NOT_FOUND:
                    break
                else:
                    raise SecretStoreError(
                        f"SecKeychainSearchCopyNext failed with status code {next_status}."
                    )
        finally:
            if search_ref.value:
                self.cf.CFRelease(search_ref)

    def entry_exists(self) -> bool:
        self._assert_valid_keychain()
        self._check_ambiguous_matches()
        s_bytes = self.service.encode("utf-8")
        a_bytes = self.account.encode("utf-8")
        pass_len = ctypes.c_uint32()
        pass_data = ctypes.c_void_p()
        item_ref = ctypes.c_void_p()
        try:
            status = self.sec.SecKeychainFindGenericPassword(
                self.keychain,
                ctypes.c_uint32(len(s_bytes)),
                s_bytes,
                ctypes.c_uint32(len(a_bytes)),
                a_bytes,
                ctypes.byref(pass_len),
                ctypes.byref(pass_data),
                ctypes.byref(item_ref),
            )
            if status == ERR_SEC_SUCCESS:
                if pass_data.value:
                    self.sec.SecKeychainItemFreeContent(None, pass_data)
                if item_ref.value:
                    self.cf.CFRelease(item_ref)
                return True
            if status == ERR_SEC_ITEM_NOT_FOUND:
                return False
            if status in (ERR_SEC_AUTH_FAILED, ERR_SEC_INTERACTION_NOT_ALLOWED):
                raise SecretStoreError("Keychain access denied or Keychain locked.")
            raise SecretStoreError(f"Keychain check failed: status {status}")
        except SecretStoreError:
            raise
        except Exception as e:
            raise SecretStoreError(f"Keychain check failed: {type(e).__name__}") from None

    def get_password(self) -> str:
        self._assert_valid_keychain()
        self._check_ambiguous_matches()
        s_bytes = self.service.encode("utf-8")
        a_bytes = self.account.encode("utf-8")
        pass_len = ctypes.c_uint32()
        pass_data = ctypes.c_void_p()
        item_ref = ctypes.c_void_p()

        status = self.sec.SecKeychainFindGenericPassword(
            self.keychain,
            ctypes.c_uint32(len(s_bytes)),
            s_bytes,
            ctypes.c_uint32(len(a_bytes)),
            a_bytes,
            ctypes.byref(pass_len),
            ctypes.byref(pass_data),
            ctypes.byref(item_ref),
        )

        if status == ERR_SEC_ITEM_NOT_FOUND:
            raise SecretStoreError(f"Keychain entry '{self.service}' ({self.account}) not found.")
        if status in (ERR_SEC_AUTH_FAILED, ERR_SEC_INTERACTION_NOT_ALLOWED):
            raise SecretStoreError("Keychain access denied or Keychain locked.")
        if status != ERR_SEC_SUCCESS:
            msg = f"SecKeychainFindGenericPassword failed with status code {status}."
            raise SecretStoreError(msg)

        try:
            if not pass_data.value or pass_len.value == 0:
                raise SecretStoreError("Retrieved empty password buffer from Keychain.")
            raw_bytes = ctypes.string_at(pass_data.value, pass_len.value)
            pwd = raw_bytes.decode("utf-8")
            self.redactor.register(pwd)
            return pwd
        finally:
            if pass_data.value:
                self.sec.SecKeychainItemFreeContent(None, pass_data)
            if item_ref.value:
                self.cf.CFRelease(item_ref)

    def store_password(self, password: str, allow_overwrite: bool = False) -> str:
        if not password:
            raise SecretStoreError("Cannot store empty password in Keychain.")

        self._assert_valid_keychain()
        self._check_ambiguous_matches()
        self.redactor.register(password)
        s_bytes = self.service.encode("utf-8")
        a_bytes = self.account.encode("utf-8")
        p_bytes = password.encode("utf-8")

        item_ref = ctypes.c_void_p()
        pass_len = ctypes.c_uint32()
        pass_data = ctypes.c_void_p()

        status = self.sec.SecKeychainFindGenericPassword(
            self.keychain,
            ctypes.c_uint32(len(s_bytes)),
            s_bytes,
            ctypes.c_uint32(len(a_bytes)),
            a_bytes,
            ctypes.byref(pass_len),
            ctypes.byref(pass_data),
            ctypes.byref(item_ref),
        )

        if status == ERR_SEC_SUCCESS:
            if not allow_overwrite:
                if pass_data.value:
                    self.sec.SecKeychainItemFreeContent(None, pass_data)
                if item_ref.value:
                    self.cf.CFRelease(item_ref)
                raise SecretStoreError(
                    f"Keychain entry '{self.service}' ({self.account}) already exists. "
                    "Explicit approval required (--allow-keychain-overwrite)."
                )

            try:
                if pass_data.value:
                    self.sec.SecKeychainItemFreeContent(None, pass_data)
                    pass_data = ctypes.c_void_p()

                mod_status = self.sec.SecKeychainItemModifyContent(
                    item_ref, None, ctypes.c_uint32(len(p_bytes)), p_bytes
                )
                if mod_status != ERR_SEC_SUCCESS:
                    raise SecretStoreError(
                        f"SecKeychainItemModifyContent failed with status {mod_status}"
                    )

                read_len = ctypes.c_uint32()
                read_data = ctypes.c_void_p()
                copy_status = self.sec.SecKeychainItemCopyContent(
                    item_ref, None, None, ctypes.byref(read_len), ctypes.byref(read_data)
                )
                if copy_status != ERR_SEC_SUCCESS:
                    raise SecretStoreError(
                        f"SecKeychainItemCopyContent failed with status {copy_status}"
                    )
                try:
                    if not read_data.value or read_len.value == 0:
                        raise SecretStoreError("Retrieved empty password buffer from Keychain.")
                    raw_bytes = ctypes.string_at(read_data.value, read_len.value)
                    retrieved = raw_bytes.decode("utf-8")
                    self.redactor.register(retrieved)
                    if not secrets.compare_digest(retrieved.encode("utf-8"), p_bytes):
                        raise SecretStoreError(
                            "Keychain readback verification failed: password bytes mismatch."
                        )
                    return retrieved
                finally:
                    if read_data.value:
                        self.sec.SecKeychainItemFreeContent(None, read_data)
            finally:
                if item_ref.value:
                    self.cf.CFRelease(item_ref)

        elif status == ERR_SEC_ITEM_NOT_FOUND:
            status = self.sec.SecKeychainAddGenericPassword(
                self.keychain,
                ctypes.c_uint32(len(s_bytes)),
                s_bytes,
                ctypes.c_uint32(len(a_bytes)),
                a_bytes,
                ctypes.c_uint32(len(p_bytes)),
                p_bytes,
                ctypes.byref(item_ref),
            )
            if status == ERR_SEC_DUPLICATE_ITEM:
                raise SecretStoreError(
                    f"Keychain entry '{self.service}' already exists (errSecDuplicateItem)."
                )
            if status != ERR_SEC_SUCCESS:
                raise SecretStoreError(f"SecKeychainAddGenericPassword failed with status {status}")

            try:
                if not item_ref.value:
                    raise SecretStoreError("SecKeychainAddGenericPassword did not return item_ref.")
                read_len = ctypes.c_uint32()
                read_data = ctypes.c_void_p()
                copy_status = self.sec.SecKeychainItemCopyContent(
                    item_ref, None, None, ctypes.byref(read_len), ctypes.byref(read_data)
                )
                if copy_status != ERR_SEC_SUCCESS:
                    raise SecretStoreError(
                        f"SecKeychainItemCopyContent failed with status {copy_status}"
                    )
                try:
                    if not read_data.value or read_len.value == 0:
                        raise SecretStoreError("Retrieved empty password buffer from Keychain.")
                    raw_bytes = ctypes.string_at(read_data.value, read_len.value)
                    retrieved = raw_bytes.decode("utf-8")
                    self.redactor.register(retrieved)
                    if not secrets.compare_digest(retrieved.encode("utf-8"), p_bytes):
                        raise SecretStoreError(
                            "Keychain readback verification failed: password bytes mismatch."
                        )
                    return retrieved
                finally:
                    if read_data.value:
                        self.sec.SecKeychainItemFreeContent(None, read_data)
            finally:
                if item_ref.value:
                    self.cf.CFRelease(item_ref)

        elif status in (ERR_SEC_AUTH_FAILED, ERR_SEC_INTERACTION_NOT_ALLOWED):
            raise SecretStoreError("Keychain access denied or Keychain locked.")
        else:
            raise SecretStoreError(f"SecKeychainFindGenericPassword failed with status {status}")

    def verify_stored_password(self, expected_password: str | None = None) -> bool:
        self._assert_valid_keychain()
        self._check_ambiguous_matches()
        if not self.entry_exists():
            return False
        retrieved = self.get_password()
        if expected_password is not None:
            if not secrets.compare_digest(
                retrieved.encode("utf-8"), expected_password.encode("utf-8")
            ):
                raise SecretStoreError(
                    "Keychain durable verification failed: retrieved password mismatch."
                )
        return True


class SecretStore:
    """Public SecretStore delegating strictly to the verified adapter."""

    def __init__(
        self,
        service: str = KEYCHAIN_SERVICE,
        account: str = KEYCHAIN_ACCOUNT,
        adapter: SecretStoreProtocol | None = None,
        keychain: Any | None = None,
    ) -> None:
        self.service = service
        self.account = account
        self.adapter = adapter or MacOSSecurityFrameworkAdapter(service, account, keychain=keychain)

    def entry_exists(self) -> bool:
        return self.adapter.entry_exists()

    def store_password(self, password: str, allow_overwrite: bool = False) -> str:
        if self.entry_exists() and not allow_overwrite:
            raise SecretStoreError(
                f"Credential entry already exists in Keychain for service '{self.service}' "
                f"and account '{self.account}'. Use allow_overwrite=True to replace."
            )
        return self.adapter.store_password(password, allow_overwrite=allow_overwrite)

    def get_password(self) -> str:
        return self.adapter.get_password()

    def verify_stored_password(self, expected_password: str | None = None) -> bool:
        return self.adapter.verify_stored_password(expected_password=expected_password)

    def close(self) -> None:
        """Release underlying adapter resources if supported."""
        adapter = getattr(self, "adapter", None)
        if adapter is not None and hasattr(adapter, "close") and callable(adapter.close):
            adapter.close()

    def __del__(self) -> None:
        self.close()


class StagingRoleProvisioner:
    """State machine for Neon Staging Application Role Provisioning."""

    def __init__(
        self,
        api_client: NeonApiProtocol,
        secret_store: SecretStoreProtocol,
        checkpoint_mgr: CheckpointManager,
        connector: DatabaseConnectorProtocol | None = None,
        mode: ProvisioningMode = ProvisioningMode.DRY_RUN,
        confirmed_branch: str | None = None,
        allow_keychain_overwrite: bool = False,
        redactor: SecretRedactor | None = None,
    ) -> None:
        self.api = api_client
        self.secret_store = secret_store
        self.checkpoint = checkpoint_mgr
        self.connector = connector or DatabaseConnector(redactor=redactor)
        self.mode = mode
        self.confirmed_branch = confirmed_branch
        self.allow_keychain_overwrite = allow_keychain_overwrite
        self.redactor = redactor or GLOBAL_REDACTOR
        self.state = ProvisioningState.INITIALIZED
        self._last_can_temp_db: bool | None = None
        self._last_public_can_temp_db: bool | None = None
        self._last_backend_ssl_active: bool | None = None
        self._verified_endpoint_id: str | None = None

    async def _call_api_async(
        self, method_name: str, *args: Any, timeout: float = DEFAULT_HTTP_TIMEOUT, **kwargs: Any
    ) -> Any:
        async_name = f"async_{method_name}"
        if hasattr(self.api, async_name):
            fn = getattr(self.api, async_name)
            try:
                return await fn(*args, timeout=timeout, **kwargs)
            except TypeError:
                return await fn(*args, **kwargs)
        sync_fn = getattr(self.api, method_name)
        if asyncio.iscoroutinefunction(sync_fn):
            return await sync_fn(*args, **kwargs)
        return sync_fn(*args, **kwargs)

    def _parse_and_validate_metadata(
        self, proj_data: Any, branches: Any, endpoints: Any, record_checkpoint: bool = True
    ) -> dict[str, Any]:
        project = proj_data.get("project", proj_data)
        if not isinstance(project, dict) or project.get("id") != PROJECT_ID:
            actual_id = project.get("id") if isinstance(project, dict) else "none"
            raise TargetVerificationError(
                f"Project ID mismatch: expected {PROJECT_ID}, got {actual_id}"
            )

        branch_ids = {b.get("id"): b for b in branches if isinstance(b, dict)}

        if STAGING_BRANCH_ID not in branch_ids:
            raise TargetVerificationError(
                f"Staging branch '{STAGING_BRANCH_ID}' not found in Neon project"
            )

        staging_branch = branch_ids[STAGING_BRANCH_ID]
        if staging_branch.get("name") != STAGING_BRANCH_NAME:
            raise TargetVerificationError(
                f"Branch name mismatch for {STAGING_BRANCH_ID}: expected '{STAGING_BRANCH_NAME}'"
            )

        # STRICT PRODUCTION REJECTION GUARD
        if (
            self.confirmed_branch == PRODUCTION_BRANCH_ID
            or self.confirmed_branch == PRODUCTION_BRANCH_NAME
        ):
            raise TargetVerificationError(
                f"CRITICAL SAFETY VIOLATION: Production branch '{PRODUCTION_BRANCH_ID}' "
                "specified! Execution strictly forbidden."
            )

        staging_endpoints = [
            ep
            for ep in endpoints
            if isinstance(ep, dict) and ep.get("branch_id") == STAGING_BRANCH_ID
        ]
        if not staging_endpoints:
            raise TargetVerificationError(
                f"No compute endpoints found for staging branch {STAGING_BRANCH_ID}"
            )

        matching_ep = next(
            (ep for ep in staging_endpoints if ep.get("host") == EXPECTED_ENDPOINT_HOST),
            None,
        )
        if not matching_ep:
            found_hosts = [ep.get("host") for ep in staging_endpoints]
            raise TargetVerificationError(
                f"Endpoint mismatch. Expected '{EXPECTED_ENDPOINT_HOST}', found: {found_hosts}"
            )

        self._verified_endpoint_id = matching_ep.get("id")
        self.state = ProvisioningState.METADATA_VERIFIED
        if record_checkpoint:
            try:
                self.checkpoint.record(
                    self.state,
                    step_number=1,
                    details=f"Target verified: project={PROJECT_ID}, branch={STAGING_BRANCH_ID}",
                )
            except Exception:
                if self.mode != ProvisioningMode.CONTAIN:
                    raise
        return {
            "project": project,
            "staging_branch": staging_branch,
            "endpoint": matching_ep,
        }

    def verify_target_metadata(self, record_checkpoint: bool = True) -> dict[str, Any]:
        """Obtain fresh metadata from Neon synchronously and assert strict target identity."""
        proj_data = self.api.get_project(PROJECT_ID)
        branches = self.api.list_branches(PROJECT_ID)
        endpoints = self.api.list_endpoints(PROJECT_ID)
        return self._parse_and_validate_metadata(
            proj_data, branches, endpoints, record_checkpoint=record_checkpoint
        )

    async def async_verify_target_metadata(
        self, record_checkpoint: bool = True, timeout: float = DEFAULT_HTTP_TIMEOUT
    ) -> dict[str, Any]:
        """Obtain fresh metadata from Neon asynchronously without blocking the event loop."""
        proj_data = await self._call_api_async("get_project", PROJECT_ID, timeout=timeout)
        branches = await self._call_api_async("list_branches", PROJECT_ID, timeout=timeout)
        endpoints = await self._call_api_async("list_endpoints", PROJECT_ID, timeout=timeout)
        return self._parse_and_validate_metadata(
            proj_data, branches, endpoints, record_checkpoint=record_checkpoint
        )

    async def verify_pg_tls_and_identity(self, conn: Any) -> None:
        """Verify PostgreSQL identity, current user, session user, database, and enforce TLS."""
        conn_host = getattr(conn, "host", None)
        if conn_host and conn_host != EXPECTED_ENDPOINT_HOST:
            raise TargetVerificationError(
                f"Host mismatch: expected '{EXPECTED_ENDPOINT_HOST}', connected to '{conn_host}'"
            )

        if not _verify_client_tls(conn):
            raise TargetVerificationError(
                "Client connection reports TLS is not active. Insecure connection terminated."
            )

        row = await conn.fetchrow(
            "SELECT current_user, session_user, current_database(), "
            "(SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()) AS ssl_active;"
        )
        if not row:
            raise TargetVerificationError("Failed to fetch connection identity and TLS status.")

        user, s_user, db, ssl_active = row[0], row[1], row[2], row[3]
        if user != ADMIN_ROLE or s_user != ADMIN_ROLE:
            msg = (
                f"Admin identity mismatch: user='{user}', "
                f"session_user='{s_user}', expected '{ADMIN_ROLE}'"
            )
            raise TargetVerificationError(msg)
        if db != EXPECTED_DATABASE:
            raise TargetVerificationError(
                f"Database mismatch: expected '{EXPECTED_DATABASE}', connected to '{db}'"
            )
        self._last_backend_ssl_active = bool(ssl_active) if isinstance(ssl_active, bool) else None

    async def verify_baseline_privileges(self, conn: Any) -> bool:
        """Phase A: Pre-provisioning verification. Checks existing role and PUBLIC baseline."""
        role_row = await conn.fetchrow(
            "SELECT count(*) FROM pg_roles WHERE rolname = $1;", APP_ROLE
        )
        role_count = role_row[0] if role_row else 0
        role_exists = role_count > 0

        public_row = await conn.fetchrow(
            """
            SELECT has_schema_privilege('public', 'public', 'CREATE')
                       AS pub_schema_create,
                   has_database_privilege('public', current_database(), 'CREATE')
                       AS pub_db_create,
                   has_database_privilege('public', current_database(), 'CONNECT')
                       AS pub_db_connect,
                   has_database_privilege('public', current_database(), 'TEMPORARY')
                       AS pub_db_temp;
            """
        )
        if not public_row:
            raise PrivilegeVerificationError("Failed to query PUBLIC baseline privileges.")

        if not _is_exact_bool(public_row["pub_schema_create"], False):
            raise PrivilegeVerificationError("PUBLIC role possesses CREATE on public schema.")
        if not _is_exact_bool(public_row["pub_db_create"], False):
            raise PrivilegeVerificationError("PUBLIC role possesses CREATE on database.")
        if not _is_exact_bool(public_row["pub_db_connect"], True):
            raise PrivilegeVerificationError("PUBLIC role lacks CONNECT on database.")

        pub_temp = public_row["pub_db_temp"]
        if type(pub_temp) is not bool:
            msg = (
                f"PUBLIC database TEMPORARY check returned non-boolean: "
                f"{type(pub_temp)} ({pub_temp!r})"
            )
            raise PrivilegeVerificationError(msg)
        self._last_public_can_temp_db = pub_temp

        self.state = ProvisioningState.BASELINE_VERIFIED
        details_msg = f"Phase A verified. role_exists={role_exists}, pub_temp={pub_temp}"
        self.checkpoint.record(
            self.state,
            step_number=2,
            details=details_msg,
        )
        return role_exists

    async def verify_structural_restrictions(self, conn: Any, expect_login: bool = False) -> None:
        """Verify role flags, zero memberships, zero ownership, and zero DDL permissions."""
        role_rows = await conn.fetch("SELECT * FROM pg_roles WHERE rolname = $1;", APP_ROLE)
        if len(role_rows) != 1:
            msg = f"Role row count failed for '{APP_ROLE}': expected 1, got {len(role_rows)}"
            raise PrivilegeVerificationError(msg)

        role = role_rows[0]
        if not _is_exact_bool(role["rolcanlogin"], expect_login):
            raise PrivilegeVerificationError(
                f"rolcanlogin check failed for '{APP_ROLE}': "
                f"expected {expect_login}, got {role['rolcanlogin']}"
            )
        for flag in (
            "rolsuper",
            "rolcreatedb",
            "rolcreaterole",
            "rolbypassrls",
            "rolreplication",
        ):
            if not _is_exact_bool(role[flag], False):
                raise PrivilegeVerificationError(f"{flag} is true for '{APP_ROLE}'! Denied.")

        member_row = await conn.fetchrow(
            """
            SELECT count(*) FROM pg_auth_members m
            JOIN pg_roles r ON r.oid = m.member
            WHERE r.rolname = $1;
            """,
            APP_ROLE,
        )
        if member_row is None or member_row[0] is None:
            raise PrivilegeVerificationError("Role membership query returned no data or NULL.")
        if not _is_exact_zero_int(member_row[0]):
            raise PrivilegeVerificationError(
                f"Role membership failed: '{APP_ROLE}' possesses {member_row[0]} memberships"
            )

        owner_row = await conn.fetchrow(
            """
            SELECT (SELECT count(*) FROM pg_database d
                    JOIN pg_roles r ON r.oid = d.datdba WHERE r.rolname = $1) AS db_owned,
                   (SELECT count(*) FROM pg_namespace n
                    JOIN pg_roles r ON r.oid = n.nspowner WHERE r.rolname = $1) AS nsp_owned,
                   (SELECT count(*) FROM pg_class c
                    JOIN pg_roles r ON r.oid = c.relowner WHERE r.rolname = $1) AS rel_owned,
                   (SELECT count(*) FROM pg_proc p
                    JOIN pg_roles r ON r.oid = p.proowner WHERE r.rolname = $1) AS proc_owned,
                   (SELECT count(*) FROM pg_type t
                    JOIN pg_roles r ON r.oid = t.typowner WHERE r.rolname = $1) AS typ_owned;
            """,
            APP_ROLE,
        )
        if not owner_row or any(
            not _is_exact_zero_int(owner_row[k])
            for k in ("db_owned", "nsp_owned", "rel_owned", "proc_owned", "typ_owned")
        ):
            counts = (
                {
                    k: owner_row[k]
                    for k in ("db_owned", "nsp_owned", "rel_owned", "proc_owned", "typ_owned")
                }
                if owner_row
                else "none"
            )
            raise PrivilegeVerificationError(f"Ownership assertion failed: {counts}")

        priv_row = await conn.fetchrow(
            """
            SELECT has_database_privilege($1, current_database(), 'CONNECT') AS can_connect,
                   has_database_privilege($1, current_database(), 'CREATE') AS can_create_db,
                   has_schema_privilege($1, 'public', 'CREATE') AS can_create_schema,
                   has_schema_privilege($1, 'public', 'USAGE') AS can_use_schema,
                   has_database_privilege($1, current_database(), 'TEMPORARY') AS can_temp_db;
            """,
            APP_ROLE,
        )
        if not priv_row or any(
            (priv_row.get(k) if hasattr(priv_row, "get") else priv_row[k]) is None
            for k in (
                "can_connect",
                "can_create_db",
                "can_create_schema",
                "can_use_schema",
                "can_temp_db",
            )
        ):
            msg = "Failed to query effective privileges or received NULL."
            raise PrivilegeVerificationError(msg)

        if not _is_exact_bool(priv_row["can_connect"], True):
            raise PrivilegeVerificationError(f"CONNECT denied on database for '{APP_ROLE}'")
        if not _is_exact_bool(priv_row["can_create_db"], False):
            raise PrivilegeVerificationError(f"Database CREATE granted to '{APP_ROLE}'! Denied.")
        if not _is_exact_bool(priv_row["can_create_schema"], False):
            raise PrivilegeVerificationError(f"Schema CREATE granted to '{APP_ROLE}'! Denied.")
        if not _is_exact_bool(priv_row["can_use_schema"], True):
            raise PrivilegeVerificationError(f"Schema USAGE denied to '{APP_ROLE}'")
        if type(priv_row["can_temp_db"]) is not bool:
            raise PrivilegeVerificationError(
                f"TEMPORARY privilege check returned non-boolean: {type(priv_row['can_temp_db'])}"
            )

        # Record inherited TEMPORARY status for evidence and human security decision
        self._last_can_temp_db = priv_row["can_temp_db"]

    async def execute_containment_nologin(
        self,
        conn: Any | None = None,
        admin_password: str | None = None,
        timeout: float = CONTAINMENT_OVERALL_DEADLINE,
    ) -> None:
        """Emergency containment: enforce NOLOGIN on dormiostay_app on verified staging target."""
        if self.confirmed_branch != STAGING_BRANCH_ID:
            msg = f"Confirmation failed. Expected '--confirm-branch {STAGING_BRANCH_ID}'"
            raise TargetVerificationError(msg)

        start_t = time.monotonic()
        deadline_t = start_t + timeout

        def get_remaining() -> float:
            return deadline_t - time.monotonic()

        nologin_verified = False
        active_conn = conn
        should_close = False

        try:
            # 1. Target metadata verification
            rem = get_remaining()
            if rem <= 0.05:
                raise TimeoutError("Containment deadline expired before metadata verification")

            meta = await asyncio.wait_for(
                self.async_verify_target_metadata(record_checkpoint=False, timeout=min(5.0, rem)),
                timeout=min(5.0, rem),
            )
            if (
                meta["endpoint"]["host"] != EXPECTED_ENDPOINT_HOST
                or meta["staging_branch"]["id"] != STAGING_BRANCH_ID
            ):
                raise TargetVerificationError(
                    "Control plane metadata mismatch during emergency containment."
                )

            # 2. Connection establishment (if not provided)
            if active_conn is None:
                if not admin_password:
                    msg = "Admin password required to establish containment connection."
                    raise ProvisioningError(msg)

                rem = get_remaining()
                if rem <= 0.05:
                    raise TimeoutError(
                        "Containment deadline expired before connection establishment"
                    )

                active_conn = await asyncio.wait_for(
                    self.connector.connect(
                        host=EXPECTED_ENDPOINT_HOST,
                        port=EXPECTED_ENDPOINT_PORT,
                        user=ADMIN_ROLE,
                        password=admin_password,
                        database=EXPECTED_DATABASE,
                    ),
                    timeout=min(5.0, rem),
                )
                should_close = True

            if active_conn is None:
                raise ProvisioningError("Containment connection is unavailable.")

            # 3. Verify TLS & identity
            rem = get_remaining()
            if rem <= 0.05:
                raise TimeoutError(
                    "Containment deadline expired before TLS and identity verification"
                )

            await asyncio.wait_for(
                self.verify_pg_tls_and_identity(active_conn),
                timeout=min(3.0, rem),
            )

            # 4. Alter role to NOLOGIN
            rem = get_remaining()
            if rem <= 0.05:
                raise TimeoutError("Containment deadline expired before ALTER ROLE execution")

            await asyncio.wait_for(
                active_conn.execute(f"ALTER ROLE {APP_ROLE} NOLOGIN;"),
                timeout=min(3.0, rem),
            )

            # 5. Post-alter readback confirmation
            rem = get_remaining()
            if rem <= 0.05:
                raise TimeoutError(
                    "Containment deadline expired before NOLOGIN readback verification"
                )

            await asyncio.wait_for(
                self.verify_structural_restrictions(active_conn, expect_login=False),
                timeout=min(3.0, rem),
            )
            nologin_verified = True
            self.state = ProvisioningState.CONTAINMENT_EXECUTED
            try:
                self.checkpoint.record(
                    self.state,
                    step_number=99,
                    details=f"Containment successful: {APP_ROLE} set to NOLOGIN.",
                )
            except Exception:
                pass

        except TargetVerificationError:
            raise
        except Exception as exc:
            if not nologin_verified:
                self.state = ProvisioningState.CONTAINMENT_FAILED
                try:
                    self.checkpoint.record(
                        self.state,
                        step_number=99,
                        details=f"Emergency containment failed: {exc}",
                    )
                except Exception:
                    pass
                msg = (
                    f"EMERGENCY CONTAINMENT FAILED: Role '{APP_ROLE}' login status is "
                    "UNVERIFIED/UNKNOWN. "
                    f"Trigger failure: {type(exc).__name__}: {exc}. "
                    "Manual administrator intervention required: Run "
                    f"'ALTER ROLE {APP_ROLE} NOLOGIN;' in Neon SQL Editor."
                )
                raise ContainmentFailureError(msg) from exc
        except asyncio.CancelledError as exc:
            if not nologin_verified:
                self.state = ProvisioningState.CONTAINMENT_FAILED
                try:
                    self.checkpoint.record(
                        self.state,
                        step_number=99,
                        details="Emergency containment cancelled before NOLOGIN verified.",
                    )
                except Exception:
                    pass
                msg = (
                    f"EMERGENCY CONTAINMENT FAILED: Role '{APP_ROLE}' login status is "
                    "UNVERIFIED/UNKNOWN. "
                    "Operation was cancelled before NOLOGIN could be verified. "
                    "Manual administrator intervention required: Run "
                    f"'ALTER ROLE {APP_ROLE} NOLOGIN;' in Neon SQL Editor."
                )
                raise ContainmentFailureError(msg) from exc
            raise
        finally:
            if should_close and active_conn:
                rem_close = deadline_t - time.monotonic()
                if rem_close <= 0.05:
                    term_fn = getattr(active_conn, "terminate", None)
                    if callable(term_fn):
                        try:
                            res = term_fn()
                            if asyncio.iscoroutine(res):
                                await asyncio.wait_for(res, timeout=0.01)
                        except BaseException:
                            pass
                else:
                    try:
                        await _safe_close_or_terminate(active_conn, timeout=min(2.0, rem_close))
                    except BaseException:
                        pass

    async def attempt_auto_containment(
        self,
        admin_conn: Any,
        trigger_error: BaseException,
        admin_password: str | None = None,
        containment_timeout: float = CONTAINMENT_OVERALL_DEADLINE,
    ) -> None:
        """Attempt safe containment if post-LOGIN verification fails.

        Runs containment bounded by an overall deadline and safely handles cancellation
        during cleanup and reconnection without exceeding the deadline or leaving orphaned tasks.
        """
        start_t = time.monotonic()
        deadline_t = start_t + containment_timeout

        def get_remaining() -> float:
            return deadline_t - time.monotonic()

        nologin_verified = False

        async def _do_containment() -> bool:
            nonlocal nologin_verified

            # 1. Try immediate containment on current admin connection if budget remains
            rem = get_remaining()
            if rem > 0.05:
                step1_to = min(3.0, max(0.001, rem))
                try:
                    await asyncio.wait_for(
                        admin_conn.execute(f"ALTER ROLE {APP_ROLE} NOLOGIN;"),
                        timeout=step1_to,
                    )
                    rem_verify = get_remaining()
                    if rem_verify > 0.05:
                        await asyncio.wait_for(
                            self.verify_structural_restrictions(admin_conn, expect_login=False),
                            timeout=min(3.0, max(0.001, rem_verify)),
                        )
                        nologin_verified = True
                        return True
                except BaseException:
                    pass

            # Safe cleanup of existing failed admin connection using remaining budget
            rem_close = get_remaining()
            if rem_close <= 0.05:
                term_fn = getattr(admin_conn, "terminate", None)
                if callable(term_fn):
                    try:
                        res = term_fn()
                        if asyncio.iscoroutine(res):
                            await asyncio.wait_for(res, timeout=0.01)
                    except BaseException:
                        pass
            else:
                try:
                    await _safe_close_or_terminate(admin_conn, timeout=min(1.0, rem_close * 0.5))
                except BaseException:
                    pass

            # 2. Fresh connection fallback: NEVER start fallback if budget is exhausted
            rem = get_remaining()
            if rem <= 0.1 or not admin_password:
                return nologin_verified

            fresh_conn = None
            try:
                # Metadata re-verification using remaining budget
                rem_meta = get_remaining()
                if rem_meta <= 0.05:
                    return nologin_verified

                meta = await asyncio.wait_for(
                    self.async_verify_target_metadata(
                        record_checkpoint=False, timeout=min(5.0, rem_meta)
                    ),
                    timeout=min(5.0, rem_meta),
                )
                if (
                    meta["endpoint"]["host"] != EXPECTED_ENDPOINT_HOST
                    or meta["staging_branch"]["id"] != STAGING_BRANCH_ID
                ):
                    return False

                # Fresh reconnection using remaining budget
                rem_conn = get_remaining()
                if rem_conn <= 0.05:
                    return nologin_verified

                fresh_conn = await asyncio.wait_for(
                    self.connector.connect(
                        host=EXPECTED_ENDPOINT_HOST,
                        port=EXPECTED_ENDPOINT_PORT,
                        user=ADMIN_ROLE,
                        password=admin_password,
                        database=EXPECTED_DATABASE,
                    ),
                    timeout=min(5.0, rem_conn),
                )

                # Verify TLS using remaining budget
                rem_tls = get_remaining()
                if rem_tls <= 0.05:
                    return nologin_verified

                await asyncio.wait_for(
                    self.verify_pg_tls_and_identity(fresh_conn),
                    timeout=min(3.0, rem_tls),
                )

                # Alter NOLOGIN using remaining budget
                rem_alter = get_remaining()
                if rem_alter <= 0.05:
                    return nologin_verified

                await asyncio.wait_for(
                    fresh_conn.execute(f"ALTER ROLE {APP_ROLE} NOLOGIN;"),
                    timeout=min(3.0, rem_alter),
                )

                # Post-alter readback using remaining budget
                rem_readback = get_remaining()
                if rem_readback <= 0.05:
                    return nologin_verified

                await asyncio.wait_for(
                    self.verify_structural_restrictions(fresh_conn, expect_login=False),
                    timeout=min(3.0, rem_readback),
                )
                nologin_verified = True
                return True
            except BaseException:
                return nologin_verified
            finally:
                if fresh_conn is not None:
                    rem_fc = get_remaining()
                    if rem_fc <= 0.05:
                        term_fn = getattr(fresh_conn, "terminate", None)
                        if callable(term_fn):
                            try:
                                res = term_fn()
                                if asyncio.iscoroutine(res):
                                    await asyncio.wait_for(res, timeout=0.01)
                            except BaseException:
                                pass
                    else:
                        try:
                            await _safe_close_or_terminate(fresh_conn, timeout=min(1.0, rem_fc))
                        except BaseException:
                            pass

        # Execute containment in a task bounded by containment_timeout
        containment_task = asyncio.create_task(_do_containment())
        cancelled_during_wait = False
        containment_succeeded = False

        while not containment_task.done():
            rem = get_remaining()
            if rem <= 0:
                containment_task.cancel()
                try:
                    await asyncio.wait_for(
                        asyncio.gather(containment_task, return_exceptions=True),
                        timeout=0.05,
                    )
                except BaseException:
                    pass
                break

            try:
                containment_succeeded = await asyncio.wait_for(
                    asyncio.shield(containment_task),
                    timeout=rem,
                )
            except TimeoutError:
                containment_task.cancel()
                try:
                    await asyncio.wait_for(
                        asyncio.gather(containment_task, return_exceptions=True),
                        timeout=0.05,
                    )
                except BaseException:
                    pass
                break
            except asyncio.CancelledError:
                cancelled_during_wait = True
            except BaseException:
                break

        # If NOLOGIN was already confirmed by readback before subsequent cleanup failed,
        # preserve verified NOLOGIN success!
        if nologin_verified:
            containment_succeeded = True
        elif not containment_task.done():
            containment_succeeded = False
        elif not containment_succeeded:
            try:
                containment_succeeded = bool(containment_task.result())
            except BaseException:
                containment_succeeded = False

        if containment_succeeded:
            self.state = ProvisioningState.CONTAINMENT_EXECUTED
            try:
                self.checkpoint.record(
                    self.state,
                    step_number=98,
                    details=f"Auto-containment: {APP_ROLE} reverted to NOLOGIN.",
                )
            except Exception:
                pass

            if cancelled_during_wait or isinstance(trigger_error, asyncio.CancelledError):
                raise asyncio.CancelledError()
            if isinstance(trigger_error, KeyboardInterrupt):
                raise trigger_error

            clean_err = self.redactor.redact(str(trigger_error))
            raise ProvisioningError(
                f"Post-LOGIN verification failed ({clean_err}). "
                f"Auto-containment successfully reverted '{APP_ROLE}' to NOLOGIN."
            ) from None
        else:
            self.state = ProvisioningState.CONTAINMENT_FAILED
            try:
                self.checkpoint.record(
                    self.state,
                    step_number=97,
                    details=(
                        "CRITICAL: EMERGENCY CONTAINMENT FAILED: "
                        "Auto-containment failed to revert role to NOLOGIN!"
                    ),
                )
            except Exception:
                pass
            clean_err = self.redactor.redact(str(trigger_error))
            msg = (
                f"CRITICAL: Post-LOGIN validation failed ({clean_err}) "
                f"AND emergency containment failed "
                f"(Emergency containment timed out after {containment_timeout:.2f}s)! "
                f"Role '{APP_ROLE}' login status is UNVERIFIED/UNKNOWN. "
                "EMERGENCY CONTAINMENT FAILED: Manual administrator intervention required."
            )
            raise ContainmentFailureError(msg) from None

    def format_temporary_privilege_evidence(
        self,
        public_temp: bool | None,
        app_temp: bool | None,
        role_exists: bool,
    ) -> str:
        """Format accurate TEMPORARY privilege evidence without unsupported inheritance claims."""
        if not role_exists or app_temp is None:
            return "NOT VERIFIED (ROLE DOES NOT EXIST)"
        if type(public_temp) is not bool or type(app_temp) is not bool:
            raise PrivilegeVerificationError(
                f"Invalid TEMPORARY privilege evidence types: "
                f"public={type(public_temp)}, app={type(app_temp)}"
            )
        if public_temp is True and app_temp is True:
            return "True (PUBLIC contributing grant; PENDING HUMAN ACCEPTANCE)"
        if public_temp is False and app_temp is True:
            return "True (Effective privilege granted; source unverified; PENDING HUMAN ACCEPTANCE)"
        if public_temp is False and app_temp is False:
            return "False (No verified TEMPORARY permission)"
        return "False (Effective privilege denied; PENDING HUMAN ACCEPTANCE)"

    async def poll_neon_operations(self, operations: list[dict[str, Any]]) -> None:
        """Poll Neon operations until all reach 'finished' state or fail.

        Conforms strictly to Neon API v2 specification:
        - Operation schema requires 'id' (UUID), 'project_id' (string), 'status'.
        - 'branch_id' and 'endpoint_id' are optional strings (nullable).
        """
        if not isinstance(operations, list) or len(operations) == 0:
            raise ProvisioningError("Neon API reset response missing operations list.")

        for op in operations:
            if not isinstance(op, dict):
                raise ProvisioningError("Malformed operation entry in reset response.")

            # Required field: id (UUID)
            op_id = op.get("id")
            if not isinstance(op_id, str) or not _is_valid_uuid(op_id):
                raise TargetVerificationError(f"Operation ID '{op_id}' is not a valid UUID.")

            # Required field: project_id (must match PROJECT_ID)
            op_proj = op.get("project_id")
            if (
                not isinstance(op_proj, str)
                or not op_proj
                or isinstance(op_proj, bool)
                or op_proj != PROJECT_ID
            ):
                raise TargetVerificationError(
                    f"Operation project mismatch: {op_proj!r}. Expected '{PROJECT_ID}'."
                )

            # Optional field: branch_id
            if "branch_id" in op and op["branch_id"] is not None:
                op_branch = op["branch_id"]
                if not isinstance(op_branch, str) or not op_branch or isinstance(op_branch, bool):
                    raise TargetVerificationError(f"Malformed operation branch_id: {op_branch!r}")
                if op_branch == PRODUCTION_BRANCH_ID or op_branch == PRODUCTION_BRANCH_NAME:
                    raise TargetVerificationError(
                        f"Production branch operation detected in reset response: '{op_branch}'"
                    )
                if op_branch != STAGING_BRANCH_ID:
                    raise TargetVerificationError(
                        f"Operation branch mismatch: '{op_branch}', expected '{STAGING_BRANCH_ID}'"
                    )

            # Optional field: endpoint_id
            if "endpoint_id" in op and op["endpoint_id"] is not None:
                op_ep = op["endpoint_id"]
                if not isinstance(op_ep, str) or not op_ep or isinstance(op_ep, bool):
                    raise TargetVerificationError(f"Malformed operation endpoint_id: {op_ep!r}")
                if self._verified_endpoint_id and op_ep != self._verified_endpoint_id:
                    raise TargetVerificationError(
                        f"Operation endpoint mismatch: '{op_ep}', "
                        f"expected '{self._verified_endpoint_id}'"
                    )

            status = op.get("status")
            if status == "finished":
                continue
            if status == "failed":
                raise ProvisioningError(f"Neon API operation '{op_id}' failed.")
            if status not in ("scheduling", "running"):
                msg = f"Neon API operation '{op_id}' in unexpected state '{status}'."
                raise ProvisioningError(msg)

            finished = False
            for _ in range(30):
                await asyncio.sleep(1)
                op_data = await self._call_api_async("get_operation", PROJECT_ID, op_id)
                if not isinstance(op_data, dict):
                    raise ProvisioningError("Neon operation polling returned invalid response.")
                op_obj = op_data.get("operation", op_data)
                if not isinstance(op_obj, dict):
                    raise ProvisioningError("Neon operation data malformed.")

                polled_id = op_obj.get("id")
                if (
                    not isinstance(polled_id, str)
                    or not _is_valid_uuid(polled_id)
                    or polled_id != op_id
                ):
                    msg = (
                        "Polled operation ID invalid or mismatch: "
                        f"expected '{op_id}', got '{polled_id}'"
                    )
                    raise TargetVerificationError(msg)

                # Required field: project_id on polled operation
                polled_proj = op_obj.get("project_id")
                if (
                    not isinstance(polled_proj, str)
                    or not polled_proj
                    or isinstance(polled_proj, bool)
                    or polled_proj != PROJECT_ID
                ):
                    raise TargetVerificationError(
                        f"Invalid or missing polled operation project_id: {polled_proj!r}. "
                        f"Expected '{PROJECT_ID}'."
                    )

                # Optional field: branch_id on polled operation
                if "branch_id" in op_obj and op_obj["branch_id"] is not None:
                    polled_branch = op_obj["branch_id"]
                    if (
                        not isinstance(polled_branch, str)
                        or not polled_branch
                        or isinstance(polled_branch, bool)
                    ):
                        raise TargetVerificationError(
                            f"Malformed polled operation branch_id: {polled_branch!r}"
                        )
                    if (
                        polled_branch == PRODUCTION_BRANCH_ID
                        or polled_branch == PRODUCTION_BRANCH_NAME
                    ):
                        raise TargetVerificationError(
                            f"Polled operation belongs to production branch: '{polled_branch}'"
                        )
                    if polled_branch != STAGING_BRANCH_ID:
                        raise TargetVerificationError(
                            f"Polled operation branch mismatch: '{polled_branch}'"
                        )

                # Optional field: endpoint_id on polled operation
                if "endpoint_id" in op_obj and op_obj["endpoint_id"] is not None:
                    polled_ep = op_obj["endpoint_id"]
                    if (
                        not isinstance(polled_ep, str)
                        or not polled_ep
                        or isinstance(polled_ep, bool)
                    ):
                        raise TargetVerificationError(
                            f"Malformed polled operation endpoint_id: {polled_ep!r}"
                        )
                    if self._verified_endpoint_id and polled_ep != self._verified_endpoint_id:
                        raise TargetVerificationError(
                            f"Polled operation endpoint mismatch: '{polled_ep}', "
                            f"expected '{self._verified_endpoint_id}'"
                        )

                curr_status = op_obj.get("status")
                if curr_status == "finished":
                    finished = True
                    break
                if curr_status == "failed":
                    raise ProvisioningError(f"Neon API operation '{op_id}' reported failed status.")
                if curr_status not in ("scheduling", "running"):
                    raise ProvisioningError(
                        f"Neon API operation '{op_id}' entered unexpected state '{curr_status}'."
                    )
            if not finished:
                msg = f"Neon API operation '{op_id}' timed out while pending."
                raise OperationTimeoutError(msg)

        self.state = ProvisioningState.OPERATIONS_POLLED_FINISHED
        self.checkpoint.record(
            self.state, step_number=7, details="All Neon reset operations reported finished."
        )

    async def run_dry_run(self, admin_password: str | None = None) -> dict[str, Any]:
        """Execute strictly read-only dry run inspection."""
        metadata = await self.async_verify_target_metadata()
        role_exists = False
        db_verified = False
        effective_temp_str = "NOT VERIFIED (NO ADMIN CONNECTION)"

        if admin_password:
            admin_conn = await self.connector.connect(
                host=EXPECTED_ENDPOINT_HOST,
                port=EXPECTED_ENDPOINT_PORT,
                user=ADMIN_ROLE,
                password=admin_password,
                database=EXPECTED_DATABASE,
            )
            try:
                await self.verify_pg_tls_and_identity(admin_conn)
                role_exists = await self.verify_baseline_privileges(admin_conn)
                if role_exists:
                    role_row = await admin_conn.fetchrow(
                        "SELECT rolcanlogin FROM pg_roles WHERE rolname = $1;", APP_ROLE
                    )
                    curr_login = bool(role_row[0]) if role_row else False
                    await self.verify_structural_restrictions(admin_conn, expect_login=curr_login)
                effective_temp_str = self.format_temporary_privilege_evidence(
                    public_temp=self._last_public_can_temp_db,
                    app_temp=self._last_can_temp_db,
                    role_exists=role_exists,
                )
                db_verified = True
            finally:
                await _safe_close_or_terminate(admin_conn, timeout=3.0)

        self.state = ProvisioningState.DRY_RUN_COMPLETED
        details_msg = (
            f"Dry run preflight completed. role_exists={role_exists}, "
            f"PUBLIC TEMPORARY={self._last_public_can_temp_db}, "
            f"application TEMPORARY={effective_temp_str}"
        )
        self.checkpoint.record(self.state, step_number=0, details=details_msg)

        return {
            "mode": "DRY_RUN",
            "metadata": metadata,
            "role_exists": role_exists,
            "database_verified": db_verified,
            "public_baseline_temporary": self._last_public_can_temp_db,
            "effective_application_temporary": effective_temp_str,
            "backend_ssl_active": self._last_backend_ssl_active,
            "target": {
                "project_id": PROJECT_ID,
                "branch_id": STAGING_BRANCH_ID,
                "endpoint": EXPECTED_ENDPOINT_HOST,
                "database": EXPECTED_DATABASE,
                "role": APP_ROLE,
            },
            "status": "PASS" if db_verified else "PARTIAL",
        }

    async def run_provisioning(self, admin_password: str) -> None:
        """Execute full staging provisioning state machine."""
        if self.mode != ProvisioningMode.EXECUTE:
            raise ProvisioningError("Execution mode not activated.")

        if self.confirmed_branch != STAGING_BRANCH_ID:
            msg = f"Confirmation failed. Expected '--confirm-branch {STAGING_BRANCH_ID}'"
            raise TargetVerificationError(msg)

        if not admin_password:
            raise ProvisioningError("Administrator password is required for execution.")

        self.redactor.register(admin_password)

        # Step 1: Verify Fresh Target Metadata from Neon API
        await self.async_verify_target_metadata()

        # Step 2: Establish Initial Administrative Connection & Verify TLS
        admin_conn = await self.connector.connect(
            host=EXPECTED_ENDPOINT_HOST,
            port=EXPECTED_ENDPOINT_PORT,
            user=ADMIN_ROLE,
            password=admin_password,
            database=EXPECTED_DATABASE,
        )

        try:
            await self.verify_pg_tls_and_identity(admin_conn)

            # Step 3: Phase A Baseline Verification
            role_exists = await self.verify_baseline_privileges(admin_conn)
            if role_exists:
                raise ProvisioningError(
                    f"Role '{APP_ROLE}' already exists in database. Halt to prevent overwrite."
                )

            # Step 4: Generate 256-bit CSPRNG Bootstrap Token (in memory only)
            # ACCEPTED RISK DISCLOSURE: Bootstrap password enters provider query logs during DDL.
            # Single-use 256-bit token; role is NOLOGIN and reset immediately via API before LOGIN.
            bootstrap_token = secrets.token_urlsafe(32)
            self.redactor.register(bootstrap_token)

            create_sql = f"""
                CREATE ROLE {APP_ROLE} NOLOGIN PASSWORD '{bootstrap_token}'
                    NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION;
                REVOKE ALL ON SCHEMA public FROM {APP_ROLE};
                REVOKE ALL ON DATABASE {EXPECTED_DATABASE} FROM {APP_ROLE};
                GRANT CONNECT ON DATABASE {EXPECTED_DATABASE} TO {APP_ROLE};
                GRANT USAGE ON SCHEMA public TO {APP_ROLE};
            """
            try:
                await admin_conn.execute(create_sql)
            except Exception as ddl_err:
                err_msg = map_db_error(ddl_err)
                msg = f"SQL execution failed during role creation: {err_msg}"
                raise ProvisioningError(msg) from None

            self.state = ProvisioningState.ROLE_CREATED_NOLOGIN
            self.checkpoint.record(
                self.state,
                step_number=4,
                details=f"Created {APP_ROLE} as NOLOGIN with ephemeral bootstrap token",
            )

            # Step 5: Verify Structural Restrictions (Assert NOLOGIN)
            await self.verify_structural_restrictions(admin_conn, expect_login=False)
            self.state = ProvisioningState.STRUCTURAL_CHECK_PASSED
            temp_evidence = self.format_temporary_privilege_evidence(
                self._last_public_can_temp_db, self._last_can_temp_db, role_exists=True
            )
            temp_status_msg = (
                f"PUBLIC database TEMPORARY: {self._last_public_can_temp_db}; "
                f"App effective TEMPORARY: {temp_evidence}"
            )
            self.checkpoint.record(
                self.state,
                step_number=5,
                details=f"Structural checks passed with NOLOGIN. {temp_status_msg}",
            )

            # Step 6: Trigger Neon Control Plane Password Reset API
            self.state = ProvisioningState.PASSWORD_RESET_SUBMITTED
            self.checkpoint.record(
                self.state, step_number=6, details="Calling Neon reset password API"
            )
            reset_res = await self._call_api_async(
                "reset_role_password", PROJECT_ID, STAGING_BRANCH_ID, APP_ROLE
            )

            # Step 7: Bounded Polling for all Returned Operations
            if not isinstance(reset_res, dict):
                raise ProvisioningError("Invalid password reset response format from Neon API.")

            operations = reset_res.get("operations")
            if not isinstance(operations, list) or len(operations) == 0:
                raise ProvisioningError("Neon API reset response missing operations list.")

            await self.poll_neon_operations(operations)

            # Step 8: Strict Validation of Final Password & Identity
            role_data = reset_res.get("role")
            if not isinstance(role_data, dict):
                raise ProvisioningError("Missing role object in Neon reset response.")
            if not isinstance(role_data.get("name"), str) or role_data.get("name") != APP_ROLE:
                raise ProvisioningError("Role identity mismatch in Neon reset response.")

            # Required field on Role: branch_id
            role_branch = role_data.get("branch_id")
            if not isinstance(role_branch, str) or not role_branch or isinstance(role_branch, bool):
                raise TargetVerificationError(
                    f"Invalid or missing role branch_id in reset response: {role_branch!r}. "
                    f"Expected '{STAGING_BRANCH_ID}'."
                )
            if role_branch == PRODUCTION_BRANCH_ID or role_branch == PRODUCTION_BRANCH_NAME:
                raise TargetVerificationError(
                    f"Production branch role detected in reset response: '{role_branch}'"
                )
            if role_branch != STAGING_BRANCH_ID:
                raise TargetVerificationError(
                    f"Role branch mismatch in reset response: '{role_branch}'"
                )

            # Optional field on Role: project_id (only validate if present in schema)
            if "project_id" in role_data and role_data["project_id"] is not None:
                role_proj = role_data["project_id"]
                if not isinstance(role_proj, str) or role_proj != PROJECT_ID:
                    raise TargetVerificationError(
                        f"Role project mismatch in reset response: '{role_proj}'"
                    )

            final_password = role_data.get("password")
            if not isinstance(final_password, str) or not final_password:
                raise ProvisioningError(
                    "Neon API did not return valid final password; role kept NOLOGIN."
                )

            self.redactor.register(final_password)
            self.state = ProvisioningState.PASSWORD_RESET_SUCCEEDED
            self.checkpoint.record(
                self.state, step_number=8, details="Neon API password reset succeeded."
            )

            # Step 9: Store in Keychain and Verify Readback
            retrieved_password = self.secret_store.store_password(
                final_password, allow_overwrite=self.allow_keychain_overwrite
            )
            self.redactor.register(retrieved_password)
            self.state = ProvisioningState.SECRET_STORED
            self.checkpoint.record(
                self.state,
                step_number=9,
                details=f"Stored in macOS Keychain under service '{KEYCHAIN_SERVICE}'",
            )

            if not self.secret_store.verify_stored_password(expected_password=final_password):
                raise SecretStoreError(
                    "Failed to verify durable password storage in Keychain prior to enabling LOGIN."
                )
            self.state = ProvisioningState.SECRET_VERIFIED
            self.checkpoint.record(
                self.state, step_number=10, details="Verified password in Keychain."
            )

        finally:
            # Step 10: Close initial admin connection
            await _safe_close_or_terminate(admin_conn, timeout=3.0)

        # Step 11: Fresh Administrative Reconnection & Target Re-Verification
        await self.async_verify_target_metadata()
        fresh_admin_conn = await self.connector.connect(
            host=EXPECTED_ENDPOINT_HOST,
            port=EXPECTED_ENDPOINT_PORT,
            user=ADMIN_ROLE,
            password=admin_password,
            database=EXPECTED_DATABASE,
        )

        login_transition_entered = False
        app_conn: Any | None = None
        try:
            await self.verify_pg_tls_and_identity(fresh_admin_conn)
            await self.verify_structural_restrictions(fresh_admin_conn, expect_login=False)
            self.state = ProvisioningState.ADMIN_RECONNECTED
            self.checkpoint.record(
                self.state,
                step_number=11,
                details="Fresh administrative connection established and verified.",
            )

            try:
                # Step 12: Enable LOGIN with Fail-Closed Containment Guard
                login_transition_entered = True
                await fresh_admin_conn.execute(f"ALTER ROLE {APP_ROLE} LOGIN;")
                self.state = ProvisioningState.LOGIN_ENABLED
                self.checkpoint.record(
                    self.state, step_number=12, details=f"Enabled LOGIN on {APP_ROLE}"
                )

                # Step 13: Verify Structural Restrictions (Assert LOGIN = True)
                await self.verify_structural_restrictions(fresh_admin_conn, expect_login=True)
                temp_evidence = self.format_temporary_privilege_evidence(
                    self._last_public_can_temp_db, self._last_can_temp_db, role_exists=True
                )
                temp_status_msg = (
                    f"PUBLIC database TEMPORARY: {self._last_public_can_temp_db}; "
                    f"App effective TEMPORARY: {temp_evidence}"
                )
                self.checkpoint.record(
                    self.state,
                    step_number=13,
                    details=f"Structural checks passed with LOGIN=True. {temp_status_msg}",
                )

                # Step 14: Mandatory Authentication Test Using Retrieved Keychain Password
                keychain_pwd = self.secret_store.get_password()
                if not secrets.compare_digest(
                    keychain_pwd.encode("utf-8"), final_password.encode("utf-8")
                ):
                    raise SecretStoreError(
                        "Keychain retrieval mismatch prior to application authentication."
                    )
                app_conn = await self.connector.connect(
                    host=EXPECTED_ENDPOINT_HOST,
                    port=EXPECTED_ENDPOINT_PORT,
                    user=APP_ROLE,
                    password=keychain_pwd,
                    database=EXPECTED_DATABASE,
                )
                auth_row = await app_conn.fetchrow("SELECT current_user, session_user;")
                if not auth_row or auth_row[0] != APP_ROLE or auth_row[1] != APP_ROLE:
                    raise PrivilegeVerificationError(
                        "Authentication check mismatch between current_user and session_user."
                    )

                self.state = ProvisioningState.COMPLETED
                self.checkpoint.record(
                    self.state,
                    step_number=14,
                    details="Role provisioning completed and authenticated successfully",
                )
            except BaseException as post_login_err:
                if login_transition_entered:
                    await self.attempt_auto_containment(
                        fresh_admin_conn, post_login_err, admin_password=admin_password
                    )
                raise
        finally:
            # Independent cleanup blocks ensuring app_conn cleanup failure or cancellation
            # never skips fresh_admin_conn cleanup
            app_exc: BaseException | None = None
            if app_conn is not None:
                try:
                    await _safe_close_or_terminate(app_conn, timeout=3.0)
                except BaseException as e:
                    app_exc = e

            admin_exc: BaseException | None = None
            try:
                await _safe_close_or_terminate(fresh_admin_conn, timeout=3.0)
            except BaseException as e:
                admin_exc = e

            if app_exc is not None:
                raise app_exc
            if admin_exc is not None:
                raise admin_exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="DormioStay Neon Staging Application Role Provisioning Automation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Execute strictly read-only preflight inspection (Default)",
    )
    mode_group.add_argument(
        "--execute",
        action="store_true",
        help="Execute provisioning sequence (requires --confirm-branch)",
    )
    mode_group.add_argument(
        "--contain-role",
        action="store_true",
        help="Emergency containment: set dormiostay_app to NOLOGIN (requires --confirm-branch)",
    )
    parser.add_argument(
        "--confirm-branch",
        type=str,
        default="",
        help=f"Explicit staging branch ID confirmation (must be '{STAGING_BRANCH_ID}')",
    )
    parser.add_argument(
        "--allow-keychain-overwrite",
        action="store_true",
        help="Allow overwriting pre-existing Keychain credential item",
    )
    parser.add_argument(
        "--checkpoint-file",
        type=str,
        default=DEFAULT_CHECKPOINT_FILE,
        help=f"Path to durable JSON checkpoint file (default: {DEFAULT_CHECKPOINT_FILE})",
    )
    parser.add_argument(
        "--admin-password-env",
        type=str,
        default="NEON_ADMIN_PASSWORD",
        help="Environment variable holding neondb_owner password (default: NEON_ADMIN_PASSWORD)",
    )
    return parser


def _run_with_bounded_sigint_handling[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run an async provisioning coroutine with bounded OS SIGINT routing.

    In standard Python asyncio.run(), a second SIGINT within the 300ms worker settlement
    window triggers Runner._on_sigint which synchronously raises KeyboardInterrupt on the
    main thread. This interrupts the event loop, causing asyncio.run() to terminate early,
    print unretrieved task exception tracebacks during shutdown, and exit with code 130
    (ordinary cancellation) while an uninterruptible worker is still alive with an uncertain
    provider-side outcome.

    This runner installs a dedicated SIGINT handler that:
    1. Routes repeated SIGINTs (for any number of signals during settlement) as task
       cancellation requests to allow the monotonic 300ms worker settlement deadline to
       resolve cleanly without early loop abortion.
    2. Keeps the event loop running until the main task settles.
    3. If the worker remains blocked, TargetVerificationError with critical uncertainty
       is raised and propagated to the CLI entry point (exiting 1).
    4. If the worker settles cleanly, CancelledError propagates to the CLI (exiting 130).
    5. Preserves any TargetVerificationError or ContainmentFailureError from the main task
       even if interrupted during shutdown, ensuring critical uncertainty is never masked.
    6. Strictly bounds auxiliary task cancellation and async generator shutdown so that
       uncooperative tasks cannot hang shutdown indefinitely.
    7. Reliably restores original signal handlers and releases event loop resources.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    main_task = loop.create_task(coro)

    orig_handler = signal.getsignal(signal.SIGINT)
    sigint_count = 0

    def _sigint_handler(signum: int, frame: Any) -> None:
        nonlocal sigint_count
        sigint_count += 1
        if not main_task.done():
            main_task.cancel()
            loop.call_soon_threadsafe(lambda: None)
        else:
            if sigint_count >= 10:
                print(
                    "[!] CRITICAL: Emergency forced exit. Provider state unknown; "
                    "manual verification required.",
                    file=sys.stderr,
                )
                sys.exit(1)

    if threading.current_thread() is threading.main_thread():
        try:
            signal.signal(signal.SIGINT, _sigint_handler)
        except (ValueError, OSError):
            pass

    main_task_exc: BaseException | None = None
    try:
        try:
            return loop.run_until_complete(main_task)
        except BaseException as exc:
            main_task_exc = exc
            raise
    finally:
        # Step 1: Restore original signal handler immediately
        if threading.current_thread() is threading.main_thread():
            try:
                signal.signal(signal.SIGINT, orig_handler)
            except (ValueError, OSError):
                pass

        # Step 2: If main_task is not yet done (e.g. if interrupted early),
        # allow it a bounded window to settle and record its true outcome.
        if not main_task.done():
            try:
                done, _ = loop.run_until_complete(asyncio.wait([main_task], timeout=0.5))
                if main_task in done and not main_task.cancelled():
                    err = main_task.exception()
                    if err is not None:
                        main_task_exc = err
            except BaseException as exc:
                if main_task_exc is None or isinstance(
                    main_task_exc, (asyncio.CancelledError, KeyboardInterrupt)
                ):
                    main_task_exc = exc

        # Step 3: Check main_task's terminal exception.
        # If main_task finished with a ProvisioningError (TargetVerificationError or
        # ContainmentFailureError), that error MUST take absolute precedence over
        # generic CancelledError or KeyboardInterrupt.
        if main_task.done() and not main_task.cancelled():
            try:
                task_err = main_task.exception()
                if task_err is not None:
                    main_task_exc = task_err
            except (asyncio.CancelledError, Exception):
                pass

        # Step 4: Cancel auxiliary tasks with a strict bound.
        aux_tasks = [t for t in asyncio.all_tasks(loop) if not t.done() and t is not main_task]
        for t in aux_tasks:
            t.cancel()
        if aux_tasks:
            try:
                loop.run_until_complete(asyncio.wait(aux_tasks, timeout=0.3))
            except BaseException:
                pass

        # Step 5: Bounded shutdown of async generators
        try:
            loop.run_until_complete(asyncio.wait_for(loop.shutdown_asyncgens(), timeout=0.3))
        except BaseException:
            pass
        finally:
            loop.close()
            asyncio.set_event_loop(None)

        # Step 6: If main_task raised a critical error, re-raise it here so that
        # caller / cli_entrypoint handles it appropriately.
        if main_task_exc is not None and not isinstance(
            main_task_exc, (asyncio.CancelledError, KeyboardInterrupt)
        ):
            raise main_task_exc


def cli_entrypoint(
    argv: list[str],
    api_client: NeonApiProtocol | None = None,
    secret_store: SecretStoreProtocol | None = None,
    connector: DatabaseConnectorProtocol | None = None,
    admin_password: str | None = None,
) -> int:
    """Explicit, testable command line entry point with lifecycle wiring."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.contain_role:
        mode = ProvisioningMode.CONTAIN
    elif args.execute:
        mode = ProvisioningMode.EXECUTE
    else:
        mode = ProvisioningMode.DRY_RUN

    print(f"[*] Mode: {mode.value}")

    # Guard 1: Strict production branch rejection across all modes
    if args.confirm_branch == PRODUCTION_BRANCH_ID or args.confirm_branch == PRODUCTION_BRANCH_NAME:
        print(
            f"[!] FATAL: Production branch '{args.confirm_branch}' strictly rejected.",
            file=sys.stderr,
        )
        return 1

    # Guard 2: Execution requires explicit staging branch confirmation
    if mode == ProvisioningMode.EXECUTE and args.confirm_branch != STAGING_BRANCH_ID:
        print(
            f"[!] FATAL: Execution mode requires '--confirm-branch {STAGING_BRANCH_ID}'",
            file=sys.stderr,
        )
        return 1

    # Guard 3: Containment requires explicit staging branch confirmation
    if mode == ProvisioningMode.CONTAIN and args.confirm_branch != STAGING_BRANCH_ID:
        print(
            f"[!] FATAL: Containment mode requires '--confirm-branch {STAGING_BRANCH_ID}'",
            file=sys.stderr,
        )
        return 1

    api_key = os.environ.get("NEON_API_KEY", "")
    if api_client is None:
        if not api_key:
            print("[!] FATAL: NEON_API_KEY is not set in environment.", file=sys.stderr)
            return 1
        api_client = NeonApiClient(api_key=api_key)

    try:
        if secret_store is None:
            secret_store = SecretStore()

        if connector is None:
            connector = DatabaseConnector()

        checkpoint_mgr = CheckpointManager(args.checkpoint_file)
        provisioner = StagingRoleProvisioner(
            api_client=api_client,
            secret_store=secret_store,
            checkpoint_mgr=checkpoint_mgr,
            connector=connector,
            mode=mode,
            confirmed_branch=args.confirm_branch,
            allow_keychain_overwrite=args.allow_keychain_overwrite,
        )

        db_pwd = admin_password or os.environ.get(args.admin_password_env, "")

        if mode == ProvisioningMode.DRY_RUN:
            res = _run_with_bounded_sigint_handling(
                provisioner.run_dry_run(admin_password=db_pwd or None)
            )
            print("[+] Neon Target Metadata Verified:")
            print(f"    Project:  {PROJECT_ID} ({PROJECT_NAME})")
            print(f"    Branch:   {STAGING_BRANCH_ID} ({STAGING_BRANCH_NAME})")
            print(f"    Endpoint: {EXPECTED_ENDPOINT_HOST}")
            db_check_str = (
                "COMPLETED" if res["database_verified"] else "OMITTED (no admin password)"
            )
            print(f"    DB Check: {db_check_str}")
            print(f"    PUBLIC Baseline TEMPORARY:     {res['public_baseline_temporary']}")
            print(f"    App Effective TEMPORARY:       {res['effective_application_temporary']}")
            if res.get("backend_ssl_active") is not None:
                backend_ssl_label = (
                    "True"
                    if res["backend_ssl_active"]
                    else "False (Neon proxy TLS termination active)"
                )
                print(f"    Backend pg_stat_ssl:           {backend_ssl_label}")
            print(f"    Status:   {res['status']}")
            if not res["database_verified"]:
                msg = (
                    "[!] NOTICE: Database checks omitted. "
                    "Export NEON_ADMIN_PASSWORD for full dry run."
                )
                print(msg, file=sys.stderr)
                return 1
            print("[+] Full dry run preflight PASSED (no cloud mutations performed).")
            return 0

        if mode == ProvisioningMode.CONTAIN:
            if not db_pwd:
                msg = (
                    f"[!] FATAL: Administrator password in ${args.admin_password_env} "
                    "required for containment."
                )
                print(msg, file=sys.stderr)
                return 1
            _run_with_bounded_sigint_handling(
                provisioner.execute_containment_nologin(admin_password=db_pwd)
            )
            print(f"[+] Emergency containment SUCCESSFUL: '{APP_ROLE}' set to NOLOGIN.")
            return 0

        if mode == ProvisioningMode.EXECUTE:
            if not db_pwd:
                msg = (
                    f"[!] FATAL: Administrator password in ${args.admin_password_env} "
                    "required for execution."
                )
                print(msg, file=sys.stderr)
                return 1
            _run_with_bounded_sigint_handling(provisioner.run_provisioning(admin_password=db_pwd))
            print(f"[+] Provisioning COMPLETED successfully for '{APP_ROLE}'.")
            return 0

    except (KeyboardInterrupt, asyncio.CancelledError):
        print("[!] Operation cancelled by user or signal.", file=sys.stderr)
        return 130
    except ProvisioningError as pe:
        clean_msg = _sanitize_text(str(pe))
        print(f"[!] Provisioning error: {clean_msg}", file=sys.stderr)
        return 1
    except Exception as e:
        err_str = (
            map_db_error(e)
            if "asyncpg" in type(e).__module__
            else f"{type(e).__name__}: Operation failed."
        )
        clean_msg = _sanitize_text(err_str)
        print(f"[!] Operation failed: {clean_msg}", file=sys.stderr)
        return 1

    return 0


def main() -> None:
    sys.exit(cli_entrypoint(sys.argv[1:]))


if __name__ == "__main__":
    main()
