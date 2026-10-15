"""Durable at-most-once protocol for a visible-UI Copilot adapter.

The adapter must use only the already-authenticated, visible Windows UI.  This
module never logs in, accesses a browser API, or sends network requests itself.
The state directory should be local to the user (not a synced workspace) and
shared by every caller that can operate the Copilot UI.

Adapter contract::

    create_conversation(marker) -> str | None
    open_conversation(conversation_id) -> None
    preflight() -> {"model": "Claude Opus 5.5", "authenticated": True,
                    "blocked": None}
    draft(prompt, attachments) -> None  # must read back and verify exact text
    send() -> None                     # exactly one visible UI invoke
    observe(marker) -> {"conversation_id": str | None, "found": bool,
                        "complete": bool, "text": str,
                        "blocked": str | None}

``observe`` must be read-only and ``found`` must mean that the exact marker is
visible in a user turn of the conversation it reports.  A timeout after the
durable DISPATCHING transition is always treated as possibly sent.  Recovery
can only observe; it never retries a send.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import uuid
from typing import Any, Iterable, Iterator, Mapping, Protocol as TypingProtocol


REQUIRED_MODEL = "Claude Opus 5.5"
STATUSES = frozenset({"PREPARING", "DISPATCHING", "UNKNOWN", "COMPLETE"})
DEFAULT_MAX_REQUESTS_PER_TASK = 10
DEFAULT_MAX_REQUEST_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_ATTACHMENTS = 10
_SCHEMA_VERSION = 1


class ProtocolError(RuntimeError):
    """A fail-closed protocol or adapter error."""


class SendNotAttempted(ProtocolError):
    """The adapter guarantees that no visible Send action was invoked."""


class GrantRequired(ProtocolError):
    """No matching human approval was recorded."""


class GrantConflict(ProtocolError):
    """A task grant already exists with different evidence or scope."""


class GrantScopeError(ProtocolError):
    """A synthetic-only grant was used for a non-synthetic request."""


class RequestConflict(ProtocolError):
    """A request ID was reused with different prompt or attachment hashes."""


class PendingRequestError(ProtocolError):
    """Another possibly-sent request blocks this project/demand pair."""


class QuotaExceeded(ProtocolError):
    """A request or task quota would be exceeded."""


class ModelUnavailable(ProtocolError):
    """The exact required model or authenticated UI is unavailable."""


class ConversationConflict(ProtocolError):
    """A UI conversation ID is already assigned to a different pair."""


class LockBusy(ProtocolError):
    """Another process currently owns the global UI lock."""


class Adapter(TypingProtocol):
    def create_conversation(self, marker: str) -> str | None: ...
    def open_conversation(self, conversation_id: str) -> None: ...
    def preflight(self) -> Mapping[str, Any]: ...
    def draft(self, prompt: str, attachments: tuple[Path, ...]) -> None: ...
    def resume_preparation(
        self,
        marker: str,
        wire_prompt: str,
        attachments: tuple[Path, ...],
        known_conversation_id: str | None,
    ) -> None: ...
    def send(self) -> None: ...
    def observe(self, marker: str) -> Mapping[str, Any]: ...


def _clean_key(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    # Identifiers are caller-supplied canonical values; only surrounding
    # whitespace is removed so differently-cased projects do not collide.
    return value.strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def exclusive_ui_lock(state_dir: str | os.PathLike[str], timeout: float = 10.0) -> Iterator[None]:
    """Take an exclusive cross-process lock for all UI operations in state_dir."""
    if timeout < 0:
        raise ValueError("timeout must be non-negative")
    directory = Path(state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / "copilot-ui.lock"
    handle = lock_path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + timeout
        if sys.platform == "win32":
            import msvcrt

            while True:
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise LockBusy("Copilot UI is locked by another process") from exc
                    time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise LockBusy("Copilot UI is locked by another process") from exc
                    time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _hash_request(
    prompt: str,
    attachments: Iterable[str | os.PathLike[str]],
    *,
    max_request_bytes: int,
    max_attachments: int,
) -> tuple[str, str, str, tuple[Path, ...]]:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")
    prompt_bytes = prompt.encode("utf-8")
    if len(prompt_bytes) > max_request_bytes:
        raise QuotaExceeded("Prompt exceeds the configured request byte quota")

    paths = tuple(Path(item).resolve(strict=True) for item in attachments)
    if len(paths) > max_attachments:
        raise QuotaExceeded("Attachment count exceeds the configured quota")
    seen_basenames: set[str] = set()
    for path in paths:
        basename = path.name.casefold()
        if basename in seen_basenames:
            raise ValueError(f"Attachment basenames must be unique (case-insensitive): {path.name}")
        seen_basenames.add(basename)

    file_entries: list[tuple[str, int, str]] = []
    total_bytes = len(prompt_bytes)
    for path in paths:
        if not path.is_file():
            raise ValueError(f"Attachment is not a regular file: {path.name}")
        before = path.stat()
        if total_bytes + before.st_size > max_request_bytes:
            raise QuotaExceeded("Prompt and attachments exceed the configured request byte quota")
        file_digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                total_bytes += len(block)
                if total_bytes > max_request_bytes:
                    raise QuotaExceeded("Prompt and attachments exceed the configured request byte quota")
                file_digest.update(block)
        after = path.stat()
        if size != before.st_size or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ProtocolError(f"Attachment changed while hashing: {path.name}")
        file_entries.append((path.name, size, file_digest.hexdigest()))

    prompt_hash = hashlib.sha256(prompt_bytes).hexdigest()
    attachment_payload = json.dumps(file_entries, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    attachments_hash = hashlib.sha256(attachment_payload).hexdigest()
    combined = hashlib.sha256()
    combined.update(b"copilot-request-v1\0")
    for part in (prompt_bytes, attachment_payload):
        combined.update(len(part).to_bytes(8, "big"))
        combined.update(part)
    return prompt_hash, attachments_hash, combined.hexdigest(), paths


class CopilotProtocol:
    """SQLite-backed request ledger and at-most-once visible-UI coordinator."""

    def __init__(
        self,
        state_dir: str | os.PathLike[str],
        adapter: Adapter,
        *,
        max_requests_per_task: int = DEFAULT_MAX_REQUESTS_PER_TASK,
        max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
        max_attachments: int = DEFAULT_MAX_ATTACHMENTS,
        lock_timeout: float = 10.0,
        ledger_filename: str = "copilot-ledger.sqlite3",
    ) -> None:
        if max_requests_per_task < 1 or max_request_bytes < 1 or max_attachments < 0:
            raise ValueError("quotas must be positive (max_attachments may be zero)")
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if not isinstance(ledger_filename, str) or Path(ledger_filename).name != ledger_filename or not ledger_filename:
            raise ValueError("ledger_filename must be a simple filename")
        self.db_path = self.state_dir / ledger_filename
        self.adapter = adapter
        self.max_requests_per_task = max_requests_per_task
        self.max_request_bytes = max_request_bytes
        self.max_attachments = max_attachments
        self.lock_timeout = lock_timeout
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _initialize(self) -> None:
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, _SCHEMA_VERSION):
                raise ProtocolError(f"Unsupported ledger schema version: {version}")
            if version == 0:
                with conn:
                    conn.executescript(
                        """
                        CREATE TABLE grants (
                            project TEXT NOT NULL,
                            demand TEXT NOT NULL,
                            task_id TEXT NOT NULL,
                            evidence TEXT NOT NULL,
                            scope TEXT NOT NULL,
                            synthetic_only INTEGER NOT NULL CHECK (synthetic_only IN (0, 1)),
                            created_at TEXT NOT NULL,
                            PRIMARY KEY (project, demand, task_id)
                        );
                        CREATE TABLE conversations (
                            project TEXT NOT NULL,
                            demand TEXT NOT NULL,
                            conversation_id TEXT UNIQUE,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL,
                            PRIMARY KEY (project, demand)
                        );
                        CREATE TABLE requests (
                            project TEXT NOT NULL,
                            demand TEXT NOT NULL,
                            task_id TEXT NOT NULL,
                            request_id TEXT NOT NULL,
                            prompt_sha256 TEXT NOT NULL,
                            attachments_sha256 TEXT NOT NULL,
                            payload_sha256 TEXT NOT NULL,
                            marker TEXT NOT NULL UNIQUE,
                            status TEXT NOT NULL CHECK (status IN ('PREPARING', 'DISPATCHING', 'UNKNOWN', 'COMPLETE')),
                            response_text TEXT,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL,
                            PRIMARY KEY (project, demand, request_id),
                            FOREIGN KEY (project, demand, task_id)
                                REFERENCES grants(project, demand, task_id)
                        );
                        CREATE UNIQUE INDEX one_pending_request_per_pair
                            ON requests(project, demand) WHERE status != 'COMPLETE';
                        PRAGMA user_version=1;
                        """
                    )
            with conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS request_events (
                        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        project TEXT NOT NULL,
                        demand TEXT NOT NULL,
                        task_id TEXT NOT NULL,
                        request_id TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (project, demand, request_id)
                            REFERENCES requests(project, demand, request_id)
                    );
                    CREATE TRIGGER IF NOT EXISTS request_events_no_update
                    BEFORE UPDATE ON request_events BEGIN
                        SELECT RAISE(ABORT, 'request_events is append-only');
                    END;
                    CREATE TRIGGER IF NOT EXISTS request_events_no_delete
                    BEFORE DELETE ON request_events BEGIN
                        SELECT RAISE(ABORT, 'request_events is append-only');
                    END;
                    """
                )
        finally:
            conn.close()

    @staticmethod
    def _pair(project: str, demand: str, task_id: str) -> tuple[str, str, str]:
        return (
            _clean_key(project, "project"),
            _clean_key(demand, "demand"),
            _clean_key(task_id, "task_id"),
        )

    def grant(
        self,
        project: str,
        demand: str,
        task_id: str,
        *,
        evidence: str,
        scope: str,
        synthetic_only: bool = False,
    ) -> None:
        """Record a one-time human approval for this pair and task."""
        project, demand, task_id = self._pair(project, demand, task_id)
        evidence = _clean_key(evidence, "evidence")
        scope = _clean_key(scope, "scope")
        if not isinstance(synthetic_only, bool):
            raise ValueError("synthetic_only must be a bool")
        conn = self._connect()
        try:
            with conn:
                row = conn.execute(
                    "SELECT evidence, scope, synthetic_only FROM grants "
                    "WHERE project=? AND demand=? AND task_id=?",
                    (project, demand, task_id),
                ).fetchone()
                expected = (evidence, scope, int(synthetic_only))
                if row is not None:
                    current = (row["evidence"], row["scope"], row["synthetic_only"])
                    if current != expected:
                        raise GrantConflict("A different approval is already recorded for this task")
                    return
                conn.execute(
                    "INSERT INTO grants(project,demand,task_id,evidence,scope,synthetic_only,created_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (project, demand, task_id, evidence, scope, int(synthetic_only), _utc_now()),
                )
        finally:
            conn.close()

    def _grant_row(self, project: str, demand: str, task_id: str) -> sqlite3.Row:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM grants WHERE project=? AND demand=? AND task_id=?",
                (project, demand, task_id),
            ).fetchone()
            if row is None:
                raise GrantRequired("Record human approval for this project/demand/task before querying")
            return row
        finally:
            conn.close()

    def query(
        self,
        project: str,
        demand: str,
        task_id: str,
        request_id: str,
        prompt: str,
        attachments: Iterable[str | os.PathLike[str]] = (),
        *,
        synthetic: bool = False,
    ) -> dict[str, Any]:
        """Run one request; after DISPATCHING, every path is at-most-once."""
        project, demand, task_id = self._pair(project, demand, task_id)
        request_id = _clean_key(request_id, "request_id")
        if not isinstance(synthetic, bool):
            raise ValueError("synthetic must be a bool")
        grant = self._grant_row(project, demand, task_id)
        if grant["synthetic_only"] and not synthetic:
            raise GrantScopeError("This approval allows synthetic requests only")
        prompt_hash, attachment_hash, payload_hash, paths = _hash_request(
            prompt,
            attachments,
            max_request_bytes=self.max_request_bytes,
            max_attachments=self.max_attachments,
        )

        with exclusive_ui_lock(self.state_dir, self.lock_timeout):
            existing = self._request_row(project, demand, task_id, request_id)
            if existing is not None:
                self._assert_same_payload(existing, prompt_hash, attachment_hash, payload_hash)
                return self._result(existing, requires_reconcile=existing["status"] != "COMPLETE")

            pending = self._pending_for_pair(project, demand)
            if pending is not None:
                raise PendingRequestError(
                    f"Request {pending['request_id']} is {pending['status']}; reconcile it before another query"
                )
            self._check_task_quota(project, demand, task_id)
            self._preflight()

            marker = f"[CODEX:{uuid.uuid4()}]"
            self._insert_preparing(
                project,
                demand,
                task_id,
                request_id,
                prompt_hash,
                attachment_hash,
                payload_hash,
                marker,
            )
            conversation_id = self._conversation_id(project, demand)
            if conversation_id is None:
                created_id = self.adapter.create_conversation(marker)
                if created_id is not None and not self._is_pending_conversation_id(created_id):
                    conversation_id = self._bind_conversation(project, demand, created_id)
            else:
                self.adapter.open_conversation(conversation_id)

            # A new chat can reset the app's model selector. Re-establish the
            # required model after the target conversation is visible.
            self._preflight()
            self._verify_payload_unchanged(
                project, demand, task_id, request_id, prompt, paths,
                (prompt_hash, attachment_hash, payload_hash), "BEFORE_DRAFT",
            )
            # The visible adapter must verify its composer readback before returning.
            self.adapter.draft(f"{marker}\n{prompt}", paths)
            # Catch selector drift between drafting and the one permitted send.
            self._preflight()
            self._verify_payload_unchanged(
                project, demand, task_id, request_id, prompt, paths,
                (prompt_hash, attachment_hash, payload_hash), "BEFORE_DISPATCH",
            )
            self._set_status(project, demand, task_id, request_id, "DISPATCHING")

            # From here onward, even an exception is potentially post-click.
            send_error = None
            try:
                self.adapter.send()
            except SendNotAttempted as exc:
                return self._send_not_attempted_result(
                    project, demand, task_id, request_id, exc
                )
            except Exception as exc:
                send_error = {"type": type(exc).__name__, "reason": str(exc),
                              "stage": getattr(self.adapter, "last_send_stage", "UNKNOWN")}
            observation = self._observe_safely(marker)
            result = self._apply_observation(
                project,
                demand,
                task_id,
                request_id,
                observation,
                known_conversation_id=conversation_id,
            )
            if send_error:
                result["send_diagnostic"] = send_error
            return result

    def reconcile(self, project: str, demand: str, task_id: str, request_id: str) -> dict[str, Any]:
        """Read-only recovery: navigate to known thread, observe once, never draft/send."""
        project, demand, task_id = self._pair(project, demand, task_id)
        request_id = _clean_key(request_id, "request_id")
        with exclusive_ui_lock(self.state_dir, self.lock_timeout):
            request = self._request_row(project, demand, task_id, request_id)
            if request is None:
                raise ProtocolError("No such request is recorded")
            if request["status"] == "COMPLETE":
                return self._result(request)

            conversation_id = self._conversation_id(project, demand)
            if conversation_id is not None:
                self.adapter.open_conversation(conversation_id)
            observation = self._observe_safely(request["marker"])
            return self._apply_observation(
                project,
                demand,
                task_id,
                request_id,
                observation,
                known_conversation_id=conversation_id,
            )

    def resume_preparing(
        self,
        project: str,
        demand: str,
        task_id: str,
        request_id: str,
        prompt: str,
        attachments: Iterable[str | os.PathLike[str]] = (),
        *,
        synthetic: bool = False,
    ) -> dict[str, Any]:
        """Safely continue only a PREPARING request, with a read-only precheck.

        The adapter's ``resume_preparation`` must inspect the visible composer
        and reuse only the exact draft identified by ``marker``. It must refuse
        ambiguous or unrelated drafts and must not invoke Send itself.
        """
        project, demand, task_id = self._pair(project, demand, task_id)
        request_id = _clean_key(request_id, "request_id")
        if not isinstance(synthetic, bool):
            raise ValueError("synthetic must be a bool")
        grant = self._grant_row(project, demand, task_id)
        if grant["synthetic_only"] and not synthetic:
            raise GrantScopeError("This approval allows synthetic requests only")
        prompt_hash, attachment_hash, payload_hash, paths = _hash_request(
            prompt,
            attachments,
            max_request_bytes=self.max_request_bytes,
            max_attachments=self.max_attachments,
        )

        with exclusive_ui_lock(self.state_dir, self.lock_timeout):
            request = self._request_row(project, demand, task_id, request_id)
            if request is None:
                raise ProtocolError("No such request is recorded")
            self._record_event(project, demand, task_id, request_id, "RESUME_PREPARING")
            if request["status"] != "PREPARING":
                self._record_event(project, demand, task_id, request_id, "RESUME_REJECTED_STATUS")
                raise PendingRequestError(
                    f"Only PREPARING requests can resume; recorded status is {request['status']}"
                )
            try:
                self._assert_same_payload(request, prompt_hash, attachment_hash, payload_hash)
            except RequestConflict:
                self._record_event(project, demand, task_id, request_id, "RESUME_REJECTED_PAYLOAD")
                raise

            conversation_id = self._conversation_id(project, demand)
            if conversation_id is not None:
                try:
                    self.adapter.open_conversation(conversation_id)
                except Exception as exc:
                    self._record_event(project, demand, task_id, request_id, "RESUME_OPEN_FAILED")
                    raise ProtocolError("Could not safely reopen the recorded conversation") from exc

            # First check whether a prior attempt already put this marker in a
            # user turn. If so, apply that observation and never send again.
            observation = self._observe_safely(request["marker"])
            required_observation_keys = {
                "conversation_id", "found", "complete", "text", "blocked",
            }
            candidate_id = observation.get("conversation_id") if observation is not None else None
            blocked = observation.get("blocked") if observation is not None else None
            observation_valid = (
                observation is not None
                and required_observation_keys.issubset(observation.keys())
                and isinstance(observation.get("found"), bool)
                and isinstance(observation.get("complete"), bool)
                and isinstance(observation.get("text"), str)
                and (
                    candidate_id is None
                    or (isinstance(candidate_id, str) and bool(candidate_id.strip()))
                )
                and (blocked is None or isinstance(blocked, str))
            )
            if not observation_valid:
                self._record_event(project, demand, task_id, request_id, "RESUME_OBSERVATION_FAILED")
                raise ProtocolError(
                    "Could not verify whether the PREPARING request was already sent; no send was attempted"
                )
            if observation["found"] is True:
                result = self._apply_observation(
                    project,
                    demand,
                    task_id,
                    request_id,
                    observation,
                    known_conversation_id=conversation_id,
                )
                outcome = "RESUME_FOUND_COMPLETE" if result["status"] == "COMPLETE" else "RESUME_FOUND_UNCONFIRMED"
                self._record_event(project, demand, task_id, request_id, outcome)
                return result

            # Only an explicit, well-formed found=False with no blocked state
            # proves it is safe to inspect/reuse the unsent draft.
            if blocked not in (None, ""):
                self._record_event(project, demand, task_id, request_id, "RESUME_OBSERVATION_FAILED")
                raise ProtocolError(
                    "The PREPARING request could not be ruled out as already sent; no send was attempted"
                )
            self._record_event(project, demand, task_id, request_id, "RESUME_MARKER_NOT_FOUND")
            wire_prompt = f"{request['marker']}\n{prompt}"
            self._verify_payload_unchanged(
                project, demand, task_id, request_id, prompt, paths,
                (prompt_hash, attachment_hash, payload_hash), "BEFORE_RESUME_PREPARATION",
            )
            try:
                self.adapter.resume_preparation(
                    request["marker"], wire_prompt, paths, conversation_id
                )
            except Exception as exc:
                self._record_event(project, demand, task_id, request_id, "RESUME_PREPARATION_FAILED")
                raise ProtocolError(
                    "Could not safely recover the PREPARING draft; no send was attempted"
                ) from exc

            try:
                self._preflight()
            except ModelUnavailable:
                self._record_event(project, demand, task_id, request_id, "RESUME_PREFLIGHT_BLOCKED")
                raise
            self._verify_payload_unchanged(
                project, demand, task_id, request_id, prompt, paths,
                (prompt_hash, attachment_hash, payload_hash), "BEFORE_DISPATCH",
            )

            # This durable transition precedes the sole permitted UI invoke.
            self._set_status(project, demand, task_id, request_id, "DISPATCHING")
            self._record_event(project, demand, task_id, request_id, "RESUME_DISPATCHING")
            send_error = None
            try:
                self.adapter.send()
            except SendNotAttempted as exc:
                return self._send_not_attempted_result(
                    project, demand, task_id, request_id, exc
                )
            except Exception as exc:
                send_error = {
                    "type": type(exc).__name__,
                    "reason": str(exc),
                    "stage": getattr(self.adapter, "last_send_stage", "UNKNOWN"),
                }
                self._record_event(project, demand, task_id, request_id, "RESUME_SEND_ERROR")
            else:
                self._record_event(project, demand, task_id, request_id, "RESUME_SEND_RETURNED")

            observation = self._observe_safely(request["marker"])
            result = self._apply_observation(
                project,
                demand,
                task_id,
                request_id,
                observation,
                known_conversation_id=conversation_id,
            )
            if send_error:
                result["send_diagnostic"] = send_error
            self._record_event(
                project,
                demand,
                task_id,
                request_id,
                "RESUME_COMPLETE" if result["status"] == "COMPLETE" else "RESUME_UNKNOWN",
            )
            return result

    def request_record(self, project: str, demand: str, task_id: str, request_id: str) -> dict[str, Any] | None:
        """Return ledger metadata for CLI status display; never returns a prompt."""
        project, demand, task_id = self._pair(project, demand, task_id)
        request_id = _clean_key(request_id, "request_id")
        row = self._request_row(project, demand, task_id, request_id)
        if row is None:
            return None
        return {
            "project": project,
            "demand": demand,
            "task_id": task_id,
            "request_id": request_id,
            "prompt_sha256": row["prompt_sha256"],
            "attachments_sha256": row["attachments_sha256"],
            "payload_sha256": row["payload_sha256"],
            "marker": row["marker"],
            "status": row["status"],
            "response_text": row["response_text"],
        }

    def request_events(self, project: str, demand: str, task_id: str, request_id: str) -> list[dict[str, Any]]:
        """Read the append-only recovery event history for one request."""
        project, demand, task_id = self._pair(project, demand, task_id)
        request_id = _clean_key(request_id, "request_id")
        # Reuse the same pair/request-id uniqueness and task ownership check.
        request = self._request_row(project, demand, task_id, request_id)
        if request is None:
            return []
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT event_id,event_type,created_at FROM request_events "
                "WHERE project=? AND demand=? AND task_id=? AND request_id=? ORDER BY event_id",
                (project, demand, task_id, request_id),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def _preflight(self) -> None:
        try:
            result = self.adapter.preflight()
        except Exception as exc:
            raise ModelUnavailable("Copilot UI preflight failed") from exc
        if not isinstance(result, Mapping):
            raise ModelUnavailable("Copilot UI preflight returned invalid data")
        if result.get("blocked"):
            raise ModelUnavailable(f"Copilot UI is blocked: {result['blocked']}")
        if result.get("authenticated") is not True:
            raise ModelUnavailable("Copilot is not visibly authenticated")
        if result.get("model") != REQUIRED_MODEL:
            raise ModelUnavailable(f"Required model {REQUIRED_MODEL!r} is not selected")

    def _verify_payload_unchanged(
        self,
        project: str,
        demand: str,
        task_id: str,
        request_id: str,
        prompt: str,
        paths: tuple[Path, ...],
        initial_hashes: tuple[str, str, str],
        stage: str,
    ) -> None:
        """Re-hash prompt and attachments under the UI lock before UI transitions."""
        request = self._request_row(project, demand, task_id, request_id)
        if request is None:
            raise ProtocolError("Request record disappeared during payload revalidation")
        ledger_hashes = (
            request["prompt_sha256"],
            request["attachments_sha256"],
            request["payload_sha256"],
        )
        try:
            current_hashes = _hash_request(
                prompt,
                paths,
                max_request_bytes=self.max_request_bytes,
                max_attachments=self.max_attachments,
            )[:3]
        except Exception as exc:
            self._record_event(
                project, demand, task_id, request_id,
                f"PAYLOAD_REVALIDATION_FAILED_{stage}",
            )
            raise ProtocolError(
                f"Request payload could not be revalidated at {stage}; no send was attempted"
            ) from exc

        if initial_hashes != ledger_hashes or current_hashes != ledger_hashes:
            self._record_event(
                project, demand, task_id, request_id,
                f"PAYLOAD_CHANGED_{stage}",
            )
            raise ProtocolError(
                f"Request prompt or attachment changed at {stage}; no send was attempted"
            )

    def _observe_safely(self, marker: str) -> Mapping[str, Any] | None:
        try:
            result = self.adapter.observe(marker)
        except Exception:
            return None
        if not isinstance(result, Mapping):
            return None
        return result

    def _apply_observation(
        self,
        project: str,
        demand: str,
        task_id: str,
        request_id: str,
        observation: Mapping[str, Any] | None,
        *,
        known_conversation_id: str | None,
    ) -> dict[str, Any]:
        matched = False
        complete = False
        text = ""
        observed_id: str | None = None
        if observation is not None:
            found_value = observation.get("found")
            complete_value = observation.get("complete")
            text_value = observation.get("text")
            candidate_id = observation.get("conversation_id")
            blocked = observation.get("blocked")
            valid_id = candidate_id is None or (isinstance(candidate_id, str) and bool(candidate_id.strip()))
            valid_shape = (
                isinstance(found_value, bool)
                and isinstance(complete_value, bool)
                and isinstance(text_value, str)
                and valid_id
            )
            if valid_shape and not blocked:
                observed_id = candidate_id.strip() if isinstance(candidate_id, str) else None
                id_matches = (
                    known_conversation_id is None
                    or observed_id == known_conversation_id
                )
                matched = (
                    found_value
                    and id_matches
                    and observed_id is not None
                    and not self._is_pending_conversation_id(observed_id)
                )
                complete = matched and complete_value and bool(text_value.strip())
                text = text_value if complete else ""

        if matched and observed_id is not None:
            self._bind_conversation(project, demand, observed_id)
        status = "COMPLETE" if complete else "UNKNOWN"
        self._finish_request(project, demand, task_id, request_id, status, text if complete else None)
        row = self._request_row(project, demand, task_id, request_id)
        assert row is not None
        return self._result(row, requires_reconcile=status != "COMPLETE")

    def _bind_conversation(self, project: str, demand: str, conversation_id: str) -> str:
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise ProtocolError("Adapter returned an invalid conversation ID")
        conversation_id = conversation_id.strip()
        if self._is_pending_conversation_id(conversation_id):
            raise ProtocolError("A pending placeholder is not an observed UI conversation ID")
        conn = self._connect()
        try:
            with conn:
                existing_pair = conn.execute(
                    "SELECT project,demand FROM conversations WHERE conversation_id=?",
                    (conversation_id,),
                ).fetchone()
                if existing_pair is not None and (existing_pair["project"], existing_pair["demand"]) != (project, demand):
                    raise ConversationConflict("Conversation ID is already assigned to another project/demand pair")
                row = conn.execute(
                    "SELECT conversation_id FROM conversations WHERE project=? AND demand=?",
                    (project, demand),
                ).fetchone()
                if row is not None and row["conversation_id"] not in (None, conversation_id):
                    raise ConversationConflict("Project/demand already has a different UI conversation")
                now = _utc_now()
                conn.execute(
                    "INSERT INTO conversations(project,demand,conversation_id,created_at,updated_at) "
                    "VALUES(?,?,?,?,?) ON CONFLICT(project,demand) DO UPDATE SET "
                    "conversation_id=excluded.conversation_id,updated_at=excluded.updated_at",
                    (project, demand, conversation_id, now, now),
                )
            return conversation_id
        finally:
            conn.close()

    def _insert_preparing(
        self,
        project: str,
        demand: str,
        task_id: str,
        request_id: str,
        prompt_hash: str,
        attachment_hash: str,
        payload_hash: str,
        marker: str,
    ) -> None:
        now = _utc_now()
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO requests(project,demand,task_id,request_id,prompt_sha256,attachments_sha256,"
                    "payload_sha256,marker,status,response_text,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?, 'PREPARING',NULL,?,?)",
                    (project, demand, task_id, request_id, prompt_hash, attachment_hash, payload_hash, marker, now, now),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO conversations(project,demand,conversation_id,created_at,updated_at) "
                    "VALUES(?,?,NULL,?,?)",
                    (project, demand, now, now),
                )
        except sqlite3.IntegrityError as exc:
            raise PendingRequestError("A pending request already exists for this project/demand pair") from exc
        finally:
            conn.close()

    def _set_status(self, project: str, demand: str, task_id: str, request_id: str, status: str) -> None:
        if status not in STATUSES:
            raise ValueError("invalid request status")
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "UPDATE requests SET status=?,updated_at=? WHERE project=? AND demand=? AND task_id=? AND request_id=?",
                    (status, _utc_now(), project, demand, task_id, request_id),
                )
        finally:
            conn.close()

    def _send_not_attempted_result(
        self,
        project: str,
        demand: str,
        task_id: str,
        request_id: str,
        exc: SendNotAttempted,
    ) -> dict[str, Any]:
        """Restore PREPARING only when the adapter proves it never clicked Send."""
        conn = self._connect()
        try:
            with conn:
                updated = conn.execute(
                    "UPDATE requests SET status='PREPARING',response_text=NULL,updated_at=? "
                    "WHERE project=? AND demand=? AND task_id=? AND request_id=? AND status='DISPATCHING'",
                    (_utc_now(), project, demand, task_id, request_id),
                )
                if updated.rowcount != 1:
                    raise ProtocolError("Could not restore the request to PREPARING")
                conn.execute(
                    "INSERT INTO request_events(project,demand,task_id,request_id,event_type,created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (project, demand, task_id, request_id, "SEND_NOT_ATTEMPTED", _utc_now()),
                )
        finally:
            conn.close()

        row = self._request_row(project, demand, task_id, request_id)
        if row is None:
            raise ProtocolError("Send was not attempted, but the request record is unavailable")
        result = self._result(row, requires_reconcile=True)
        result["send_diagnostic"] = {
            "type": type(exc).__name__,
            "reason": str(exc),
            "stage": getattr(self.adapter, "last_send_stage", "UNKNOWN"),
        }
        return result

    def _finish_request(
        self,
        project: str,
        demand: str,
        task_id: str,
        request_id: str,
        status: str,
        response_text: str | None,
    ) -> None:
        if status not in {"UNKNOWN", "COMPLETE"}:
            raise ValueError("final status must be UNKNOWN or COMPLETE")
        if status == "COMPLETE" and (not isinstance(response_text, str) or not response_text.strip()):
            raise ValueError("complete responses must contain non-empty text")
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "UPDATE requests SET status=?,response_text=?,updated_at=? "
                    "WHERE project=? AND demand=? AND task_id=? AND request_id=?",
                    (status, response_text, _utc_now(), project, demand, task_id, request_id),
                )
        finally:
            conn.close()

    def _record_event(
        self,
        project: str,
        demand: str,
        task_id: str,
        request_id: str,
        event_type: str,
    ) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO request_events(project,demand,task_id,request_id,event_type,created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (project, demand, task_id, request_id, event_type, _utc_now()),
                )
        finally:
            conn.close()

    def _request_row(self, project: str, demand: str, task_id: str, request_id: str) -> sqlite3.Row | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM requests WHERE project=? AND demand=? AND request_id=?",
                (project, demand, request_id),
            ).fetchone()
            if row is not None and row["task_id"] != task_id:
                raise RequestConflict("request_id is already assigned to another task in this project/demand pair")
            return row
        finally:
            conn.close()

    def _pending_for_pair(self, project: str, demand: str) -> sqlite3.Row | None:
        conn = self._connect()
        try:
            return conn.execute(
                "SELECT request_id,status FROM requests WHERE project=? AND demand=? AND status!='COMPLETE' "
                "ORDER BY created_at LIMIT 1",
                (project, demand),
            ).fetchone()
        finally:
            conn.close()

    def _conversation_id(self, project: str, demand: str) -> str | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT conversation_id FROM conversations WHERE project=? AND demand=?",
                (project, demand),
            ).fetchone()
            return None if row is None else row["conversation_id"]
        finally:
            conn.close()

    def _check_task_quota(self, project: str, demand: str, task_id: str) -> None:
        conn = self._connect()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM requests WHERE project=? AND demand=? AND task_id=?",
                (project, demand, task_id),
            ).fetchone()[0]
        finally:
            conn.close()
        if count >= self.max_requests_per_task:
            raise QuotaExceeded("Request count for this task has reached its quota")

    @staticmethod
    def _assert_same_payload(
        row: sqlite3.Row,
        prompt_hash: str,
        attachment_hash: str,
        payload_hash: str,
    ) -> None:
        if (row["prompt_sha256"], row["attachments_sha256"], row["payload_sha256"]) != (
            prompt_hash,
            attachment_hash,
            payload_hash,
        ):
            raise RequestConflict("request_id was already used with a different prompt or attachment payload")

    @staticmethod
    def _is_pending_conversation_id(conversation_id: Any) -> bool:
        return isinstance(conversation_id, str) and conversation_id.startswith("pending:")

    @staticmethod
    def _result(row: sqlite3.Row, *, requires_reconcile: bool = False) -> dict[str, Any]:
        status = row["status"]
        return {
            "status": status,
            "text": row["response_text"] if status == "COMPLETE" else None,
            "marker": row["marker"],
            "requires_reconcile": requires_reconcile,
        }


# Short alias for callers that prefer the simpler name.
Protocol = CopilotProtocol

