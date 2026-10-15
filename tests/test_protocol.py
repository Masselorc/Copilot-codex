"""Synthetic-only unit tests for the SQLite/UI dispatch protocol."""
from __future__ import annotations

from collections import deque
from pathlib import Path
import sqlite3
import sys
import subprocess
import tempfile
import unittest
import uuid

SCRIPTS = Path(__file__).resolve().parents[1] / "skill" / "copilot-consult" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from protocol import (  # noqa: E402
    CopilotProtocol,
    ConversationConflict,
    GrantRequired,
    GrantScopeError,
    LockBusy,
    ModelUnavailable,
    PendingRequestError,
    ProtocolError,
    QuotaExceeded,
    RequestConflict,
    SendNotAttempted,
    exclusive_ui_lock,
)


class FakeAdapter:
    """In-memory UI double; never opens a real app or sends a real request."""

    def __init__(self, *, model="Claude Opus 5.5", conversation_ids=None, preflight_models=None):
        self.model = model
        self.conversation_ids = deque(conversation_ids or [])
        self.preflight_models = deque(preflight_models or [])
        self.current_conversation_id = None
        self.observe_results = deque()
        self.send_error = False
        self.send_not_attempted = False
        self.last_send_stage = "VALIDATING"
        self.create_error = False
        self.resume_preparation_error = False
        self.send_calls = 0
        self.send_attempts = 0
        self.calls = []
        self.mutate_path = None
        self.mutate_on = None
        self.preflight_calls = 0

    def _maybe_mutate(self, stage, occurrence=None):
        should_mutate = self.mutate_on == stage or self.mutate_on == (stage, occurrence)
        if should_mutate and self.mutate_path is not None:
            self.mutate_path.write_bytes(b"changed synthetic attachment")
            self.mutate_on = None

    def create_conversation(self, marker):
        self.calls.append(("create", marker))
        if self.create_error:
            raise RuntimeError("synthetic failure before creating a conversation")
        if self.conversation_ids:
            result = self.conversation_ids.popleft()
        else:
            result = f"conversation-{len([c for c in self.calls if c[0] == 'create'])}"
        self.current_conversation_id = result
        self._maybe_mutate("create")
        return result

    def open_conversation(self, conversation_id):
        self.calls.append(("open", conversation_id))
        self.current_conversation_id = conversation_id

    def preflight(self):
        self.calls.append(("preflight",))
        self.preflight_calls += 1
        self._maybe_mutate("preflight", self.preflight_calls)
        model = self.preflight_models.popleft() if self.preflight_models else self.model
        return {"model": model, "authenticated": True, "blocked": None}

    def draft(self, prompt, attachments):
        self.calls.append(("draft", prompt, tuple(attachments)))
        self._maybe_mutate("draft")

    def resume_preparation(self, marker, wire_prompt, attachments, known_conversation_id):
        self.calls.append(("resume_preparation", marker, wire_prompt, tuple(attachments), known_conversation_id))
        if self.resume_preparation_error:
            raise RuntimeError("synthetic ambiguous draft")
        self.current_conversation_id = known_conversation_id or "conversation-recovered"
        self._maybe_mutate("resume_preparation")

    def send(self):
        self.send_attempts += 1
        if self.send_not_attempted:
            raise SendNotAttempted("synthetic pre-click validation timeout")
        self.send_calls += 1
        self.calls.append(("send",))
        if self.send_error:
            raise TimeoutError("synthetic timeout after possible UI invoke")

    def observe(self, marker):
        self.calls.append(("observe", marker))
        self._maybe_mutate("observe")
        if self.observe_results:
            return self.observe_results.popleft()
        return {
            "conversation_id": self.current_conversation_id,
            "found": True,
            "complete": True,
            "text": "Resposta sintética concluída.",
            "blocked": None,
        }


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="protocol-test-", dir=Path(__file__).resolve().parent)
        self.state = Path(self.temporary.name)
        self.artifacts = []
        self.adapter = FakeAdapter()
        self.protocol = self.new_protocol(self.adapter)

    def tearDown(self):
        for path in self.artifacts:
            path.unlink(missing_ok=True)
        (self.state / "copilot-ui.lock").unlink(missing_ok=True)
        self.temporary.cleanup()

    def new_protocol(self, adapter, **kwargs):
        filename = f".protocol-test-{uuid.uuid4().hex}.sqlite3"
        db = self.state / filename
        self.artifacts.extend((db, Path(str(db) + "-wal"), Path(str(db) + "-shm")))
        return CopilotProtocol(self.state, adapter, ledger_filename=filename, **kwargs)

    def approve(self, project="Projeto A", demand="Demanda 1", task_id="task-1", *, synthetic_only=False):
        self.protocol.grant(
            project,
            demand,
            task_id,
            evidence="Aprovação humana sintética registrada no teste",
            scope="Somente esta demanda e esta tarefa de teste",
            synthetic_only=synthetic_only,
        )

    def query(self, request_id="req-1", prompt="Consulta sintética", **kwargs):
        return self.protocol.query(
            "Projeto A", "Demanda 1", "task-1", request_id, prompt,
            synthetic=True, **kwargs,
        )

    def test_grant_is_required_before_any_adapter_action(self):
        with self.assertRaises(GrantRequired):
            self.query()
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual(self.adapter.send_calls, 0)

    def test_synthetic_only_grant_rejects_non_synthetic_query(self):
        self.approve(synthetic_only=True)
        with self.assertRaises(GrantScopeError):
            self.protocol.query("Projeto A", "Demanda 1", "task-1", "req-1", "real-looking text")
        self.assertEqual(self.adapter.calls, [])

    def test_model_mismatch_fails_before_create_or_send(self):
        self.approve()
        self.adapter.model = "Automatico"
        with self.assertRaises(ModelUnavailable):
            self.query()
        self.assertEqual(self.adapter.send_calls, 0)
        self.assertFalse(any(call[0] == "create" for call in self.adapter.calls))
        self.assertIsNone(self.protocol.request_record("Projeto A", "Demanda 1", "task-1", "req-1"))

    def test_model_is_rechecked_after_new_conversation_and_before_send(self):
        self.approve()
        self.adapter.preflight_models = deque(["Claude Opus 5.5", "Automatico"])
        with self.assertRaises(ModelUnavailable):
            self.query()
        record = self.protocol.request_record("Projeto A", "Demanda 1", "task-1", "req-1")
        self.assertEqual(record["status"], "PREPARING")
        self.assertEqual(self.adapter.send_calls, 0)
        self.assertEqual([call[0] for call in self.adapter.calls].count("create"), 1)

        # A separate task record exercises drift after draft, immediately
        # before the durable DISPATCHING transition.
        state = self.new_protocol(self.adapter)
        self.adapter.preflight_models = deque([
            "Claude Opus 5.5", "Claude Opus 5.5", "Automatico",
        ])
        state.grant(
            "Projeto B", "Demanda 1", "task-1", evidence="Aprovação humana sintética",
            scope="Teste de drift", synthetic_only=False,
        )
        with self.assertRaises(ModelUnavailable):
            state.query("Projeto B", "Demanda 1", "task-1", "req-b", "Consulta sintética", synthetic=True)
        self.assertEqual(self.adapter.send_calls, 0)
        record = state.request_record("Projeto B", "Demanda 1", "task-1", "req-b")
        self.assertEqual(record["status"], "PREPARING")

    def test_timeout_after_send_is_at_most_once_and_reconciles_read_only(self):
        self.approve()
        self.adapter.send_error = True
        self.adapter.observe_results.append({
            "conversation_id": None,
            "found": False,
            "complete": False,
            "text": "",
            "blocked": None,
        })
        first = self.query()
        self.assertEqual(first["status"], "UNKNOWN")
        self.assertTrue(first["requires_reconcile"])
        self.assertEqual(self.adapter.send_calls, 1)

        calls_before_retry = list(self.adapter.calls)
        retry = self.query()
        self.assertEqual(retry["status"], "UNKNOWN")
        self.assertEqual(self.adapter.send_calls, 1)
        self.assertEqual(self.adapter.calls, calls_before_retry)
        with self.assertRaises(PendingRequestError):
            self.query(request_id="req-2", prompt="Another synthetic consultation")

        # Replacing/fixing an adapter does not turn UNKNOWN into permission to
        # prepare or send again. Exercise a fresh adapter with no UI history.
        repaired_adapter=FakeAdapter()
        previous_adapter=self.protocol.adapter
        self.protocol.adapter=repaired_adapter
        try:
            after_fix=self.query()
            self.assertEqual(after_fix["status"],"UNKNOWN")
            self.assertEqual(repaired_adapter.calls,[])
            self.assertEqual(repaired_adapter.send_calls,0)
        finally:
            self.protocol.adapter=previous_adapter

        # Reconciliation observes the exact marker and may complete the record.
        self.adapter.send_error = False
        self.adapter.observe_results.append({
            "conversation_id": "conversation-1",
            "found": True,
            "complete": True,
            "text": "Recuperada sem reenvio.",
            "blocked": None,
        })
        recovered = self.protocol.reconcile("Projeto A", "Demanda 1", "task-1", "req-1")
        self.assertEqual(recovered["status"], "COMPLETE")
        self.assertEqual(recovered["text"], "Recuperada sem reenvio.")
        self.assertEqual(self.adapter.send_calls, 1)
        self.assertEqual([call[0] for call in self.adapter.calls[-2:]], ["open", "observe"])

    def test_resume_preparing_recovers_precreate_failure_and_sends_once(self):
        self.approve()
        self.adapter.create_error = True
        with self.assertRaisesRegex(RuntimeError, "before creating"):
            self.query(request_id="recover-1", prompt="Consulta sintética recuperável")
        original = self.protocol.request_record("Projeto A", "Demanda 1", "task-1", "recover-1")
        self.assertEqual(original["status"], "PREPARING")

        marker = original["marker"]
        self.adapter.create_error = False
        self.adapter.observe_results.extend([
            {
                "conversation_id": None,
                "found": False,
                "complete": False,
                "text": "",
                "blocked": None,
            },
            {
                "conversation_id": "conversation-recovered",
                "found": True,
                "complete": True,
                "text": "Resposta após retomada segura.",
                "blocked": None,
            },
        ])
        result = self.protocol.resume_preparing(
            "Projeto A", "Demanda 1", "task-1", "recover-1",
            "Consulta sintética recuperável", synthetic=True,
        )
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["text"], "Resposta após retomada segura.")
        self.assertEqual(self.adapter.send_calls, 1)
        resumed = [call for call in self.adapter.calls if call[0] == "resume_preparation"]
        self.assertEqual(len(resumed), 1)
        self.assertEqual(resumed[0][1], marker)
        self.assertEqual(resumed[0][2], f"{marker}\nConsulta sintética recuperável")
        self.assertIsNone(resumed[0][4])
        self.assertEqual(self.protocol.request_record("Projeto A", "Demanda 1", "task-1", "recover-1")["marker"], marker)
        events = [event["event_type"] for event in self.protocol.request_events("Projeto A", "Demanda 1", "task-1", "recover-1")]
        self.assertEqual(events, ["RESUME_PREPARING", "RESUME_MARKER_NOT_FOUND", "RESUME_DISPATCHING", "RESUME_SEND_RETURNED", "RESUME_COMPLETE"])

    def test_send_not_attempted_restores_preparing_and_resume_is_allowed(self):
        self.approve()
        self.adapter.send_not_attempted = True
        result = self.query(request_id="preclick-query", prompt="Consulta sintética")
        self.assertEqual(result["status"], "PREPARING")
        self.assertTrue(result["requires_reconcile"])
        self.assertEqual(result["send_diagnostic"]["type"], "SendNotAttempted")
        self.assertEqual(result["send_diagnostic"]["stage"], "VALIDATING")
        self.assertEqual(self.adapter.send_calls, 0)
        self.assertEqual(self.adapter.send_attempts, 1)
        self.assertEqual(
            self.protocol.request_record("Projeto A", "Demanda 1", "task-1", "preclick-query")["status"],
            "PREPARING",
        )
        self.assertEqual(
            [event["event_type"] for event in self.protocol.request_events("Projeto A", "Demanda 1", "task-1", "preclick-query")],
            ["SEND_NOT_ATTEMPTED"],
        )

        self.adapter.send_not_attempted = False
        self.adapter.observe_results.extend([
            {"conversation_id": "conversation-1", "found": False, "complete": False, "text": "", "blocked": None},
            {"conversation_id": "conversation-1", "found": True, "complete": True, "text": "Retomada concluída.", "blocked": None},
        ])
        resumed = self.protocol.resume_preparing(
            "Projeto A", "Demanda 1", "task-1", "preclick-query", "Consulta sintética", synthetic=True,
        )
        self.assertEqual(resumed["status"], "COMPLETE")
        self.assertEqual(resumed["text"], "Retomada concluída.")
        self.assertEqual(self.adapter.send_calls, 1)
        self.assertEqual(self.adapter.send_attempts, 2)
        events = [event["event_type"] for event in self.protocol.request_events("Projeto A", "Demanda 1", "task-1", "preclick-query")]
        self.assertEqual(events, ["SEND_NOT_ATTEMPTED", "RESUME_PREPARING", "RESUME_MARKER_NOT_FOUND", "RESUME_DISPATCHING", "RESUME_SEND_RETURNED", "RESUME_COMPLETE"])

    def test_resume_send_not_attempted_restores_preparing_with_history(self):
        self.approve()
        self.adapter.create_error = True
        with self.assertRaises(RuntimeError):
            self.query(request_id="preclick-resume", prompt="Consulta sintética")
        self.adapter.create_error = False
        self.adapter.observe_results.append(
            {"conversation_id": None, "found": False, "complete": False, "text": "", "blocked": None}
        )
        self.adapter.send_not_attempted = True
        result = self.protocol.resume_preparing(
            "Projeto A", "Demanda 1", "task-1", "preclick-resume", "Consulta sintética", synthetic=True,
        )
        self.assertEqual(result["status"], "PREPARING")
        self.assertTrue(result["requires_reconcile"])
        self.assertEqual(result["send_diagnostic"]["type"], "SendNotAttempted")
        self.assertEqual(self.adapter.send_calls, 0)
        self.assertEqual(self.adapter.send_attempts, 1)
        events = [event["event_type"] for event in self.protocol.request_events("Projeto A", "Demanda 1", "task-1", "preclick-resume")]
        self.assertEqual(events, ["RESUME_PREPARING", "RESUME_MARKER_NOT_FOUND", "RESUME_DISPATCHING", "SEND_NOT_ATTEMPTED"])

        self.adapter.send_not_attempted = False
        self.adapter.observe_results.extend([
            {"conversation_id": "conversation-recovered", "found": False, "complete": False, "text": "", "blocked": None},
            {"conversation_id": "conversation-recovered", "found": True, "complete": True, "text": "Retomada após validação.", "blocked": None},
        ])
        resumed = self.protocol.resume_preparing(
            "Projeto A", "Demanda 1", "task-1", "preclick-resume", "Consulta sintética", synthetic=True,
        )
        self.assertEqual(resumed["status"], "COMPLETE")
        self.assertEqual(self.adapter.send_calls, 1)
        self.assertEqual(self.adapter.send_attempts, 2)
        events = [event["event_type"] for event in self.protocol.request_events("Projeto A", "Demanda 1", "task-1", "preclick-resume")]
        self.assertEqual(events[-2:], ["RESUME_SEND_RETURNED", "RESUME_COMPLETE"])

    def test_resume_preparing_observed_marker_never_sends_again(self):
        self.approve()
        self.adapter.create_error = True
        with self.assertRaises(RuntimeError):
            self.query(request_id="already-sent", prompt="Consulta sintética")
        request = self.protocol.request_record("Projeto A", "Demanda 1", "task-1", "already-sent")
        marker = request["marker"]
        self.adapter.create_error = False
        self.adapter.observe_results.append({
            "conversation_id": "conversation-already-sent",
            "found": True,
            "complete": True,
            "text": "Resposta localizada sem novo envio.",
            "blocked": None,
        })

        result = self.protocol.resume_preparing(
            "Projeto A", "Demanda 1", "task-1", "already-sent", "Consulta sintética", synthetic=True,
        )
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["text"], "Resposta localizada sem novo envio.")
        self.assertEqual(self.adapter.send_calls, 0)
        self.assertFalse(any(call[0] == "resume_preparation" for call in self.adapter.calls))
        self.assertEqual(result["marker"], marker)
        events = [event["event_type"] for event in self.protocol.request_events("Projeto A", "Demanda 1", "task-1", "already-sent")]
        self.assertEqual(events, ["RESUME_PREPARING", "RESUME_FOUND_COMPLETE"])

    def test_resume_send_timeout_keeps_at_most_once_and_returns_diagnostic(self):
        self.approve()
        self.adapter.create_error = True
        with self.assertRaises(RuntimeError):
            self.query(request_id="resume-timeout", prompt="Consulta sintética")
        self.adapter.create_error = False
        self.adapter.observe_results.extend([
            {
                "conversation_id": None,
                "found": False,
                "complete": False,
                "text": "",
                "blocked": None,
            },
            {
                "conversation_id": None,
                "found": False,
                "complete": False,
                "text": "",
                "blocked": None,
            },
        ])
        self.adapter.send_error = True
        result = self.protocol.resume_preparing(
            "Projeto A", "Demanda 1", "task-1", "resume-timeout", "Consulta sintética", synthetic=True,
        )
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["send_diagnostic"]["type"], "TimeoutError")
        self.assertEqual(result["send_diagnostic"]["stage"], "VALIDATING")
        self.assertEqual(self.adapter.send_calls, 1)
        calls_after_resume = list(self.adapter.calls)
        with self.assertRaises(PendingRequestError):
            self.protocol.resume_preparing(
                "Projeto A", "Demanda 1", "task-1", "resume-timeout", "Consulta sintética", synthetic=True,
            )
        self.assertEqual(self.adapter.calls, calls_after_resume)
        self.assertEqual(self.adapter.send_calls, 1)

    def test_resume_preparing_refuses_unknown_without_observation_or_send(self):
        self.approve()
        self.adapter.send_error = True
        self.adapter.observe_results.append({
            "conversation_id": None,
            "found": False,
            "complete": False,
            "text": "",
            "blocked": None,
        })
        self.assertEqual(self.query(request_id="unknown-1")["status"], "UNKNOWN")
        calls_before = list(self.adapter.calls)
        with self.assertRaises(PendingRequestError):
            self.protocol.resume_preparing(
                "Projeto A", "Demanda 1", "task-1", "unknown-1", "Consulta sintética", synthetic=True,
            )
        self.assertEqual(self.adapter.calls, calls_before)
        self.assertEqual(self.adapter.send_calls, 1)
        events = [event["event_type"] for event in self.protocol.request_events("Projeto A", "Demanda 1", "task-1", "unknown-1")]
        self.assertEqual(events, ["RESUME_PREPARING", "RESUME_REJECTED_STATUS"])

    def test_resume_preparing_requires_explicit_clear_observation_before_draft(self):
        bad_observations = [
            None,
            {
                "conversation_id": None,
                "found": False,
                "complete": False,
                "text": "",
                "blocked": "UI observation unavailable",
            },
        ]
        for index, bad_observation in enumerate(bad_observations):
            with self.subTest(index=index):
                project = f"Projeto observação {index}"
                request_id = f"observe-fail-{index}"
                self.protocol.grant(
                    project, "Demanda 1", "task-1", evidence="Aprovação humana sintética",
                    scope="Retomada sintética", synthetic_only=False,
                )
                self.adapter.create_error = True
                with self.assertRaises(RuntimeError):
                    self.protocol.query(
                        project, "Demanda 1", "task-1", request_id,
                        "Consulta sintética", synthetic=True,
                    )
                self.adapter.create_error = False
                self.adapter.observe_results.append(bad_observation)
                calls_before_resume = list(self.adapter.calls)
                with self.assertRaises(ProtocolError):
                    self.protocol.resume_preparing(
                        project, "Demanda 1", "task-1", request_id,
                        "Consulta sintética", synthetic=True,
                    )
                self.assertEqual(self.adapter.send_calls, 0)
                self.assertEqual(
                    [call[0] for call in self.adapter.calls[len(calls_before_resume):]],
                    ["observe"],
                )
                record = self.protocol.request_record(project, "Demanda 1", "task-1", request_id)
                self.assertEqual(record["status"], "PREPARING")
                events = [
                    event["event_type"]
                    for event in self.protocol.request_events(project, "Demanda 1", "task-1", request_id)
                ]
                self.assertEqual(events, ["RESUME_PREPARING", "RESUME_OBSERVATION_FAILED"])

    def test_resume_preparing_rejects_changed_payload_before_ui_action(self):
        self.approve()
        self.adapter.create_error = True
        with self.assertRaises(RuntimeError):
            self.query(request_id="payload-1", prompt="Consulta sintética original")
        calls_before = list(self.adapter.calls)
        with self.assertRaises(RequestConflict):
            self.protocol.resume_preparing(
                "Projeto A", "Demanda 1", "task-1", "payload-1", "Consulta sintética alterada", synthetic=True,
            )
        self.assertEqual(self.adapter.calls, calls_before)
        self.assertEqual(self.adapter.send_calls, 0)
        events = [event["event_type"] for event in self.protocol.request_events("Projeto A", "Demanda 1", "task-1", "payload-1")]
        self.assertEqual(events, ["RESUME_PREPARING", "RESUME_REJECTED_PAYLOAD"])

    def test_recovery_event_history_is_append_only(self):
        self.approve()
        self.adapter.create_error = True
        with self.assertRaises(RuntimeError):
            self.query(request_id="events-1", prompt="Consulta sintética")
        self.protocol.resume_preparing(
            "Projeto A", "Demanda 1", "task-1", "events-1", "Consulta sintética", synthetic=True,
        )
        conn = sqlite3.connect(self.protocol.db_path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE request_events SET event_type='rewritten' WHERE event_id=1")
            conn.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM request_events WHERE event_id=1")
        finally:
            conn.close()
        self.assertGreaterEqual(len(self.protocol.request_events("Projeto A", "Demanda 1", "task-1", "events-1")), 2)

    def test_unicode_and_code_response_is_preserved_exactly(self):
        self.approve()
        expected = 'Ação, coração e 🙂\n```python\ndef soma(a, b):\n    return a + b\n```\nFim.'
        self.adapter.observe_results.append({
            "conversation_id": "conversation-1",
            "found": True,
            "complete": True,
            "text": expected,
            "blocked": None,
        })
        result = self.query(prompt="Retorne conteúdo sintético com código")
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["text"], expected)
        self.assertEqual(self.query(prompt="Retorne conteúdo sintético com código")["text"], expected)
        self.assertEqual(self.adapter.send_calls, 1)

    def test_empty_text_never_becomes_complete(self):
        self.approve()
        self.adapter.observe_results.append({
            "conversation_id": "conversation-1",
            "found": True,
            "complete": True,
            "text": "",
            "blocked": None,
        })
        result = self.query()
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIsNone(result["text"])
        record = self.protocol.request_record("Projeto A", "Demanda 1", "task-1", "req-1")
        self.assertIsNone(record["response_text"])
        self.assertEqual(self.adapter.send_calls, 1)

    def test_new_conversation_placeholder_binds_only_observed_ui_id(self):
        self.approve()
        self.adapter.conversation_ids = deque(["pending:request-uuid"])
        self.adapter.observe_results.append({
            "conversation_id": "ui-observed-conversation-uuid",
            "found": True,
            "complete": True,
            "text": "Resposta sintética concluída.",
            "blocked": None,
        })
        result = self.query()
        self.assertEqual(result["status"], "COMPLETE")
        conn = sqlite3.connect(self.protocol.db_path)
        try:
            conversation_id = conn.execute(
                "SELECT conversation_id FROM conversations WHERE project=? AND demand=?",
                ("Projeto A", "Demanda 1"),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(conversation_id, "ui-observed-conversation-uuid")
        self.assertEqual(self.adapter.send_calls, 1)

    def test_request_id_hashes_prompt_and_files_and_conversations_are_pair_isolated(self):
        self.approve()
        source = self.state / f".attachment-{uuid.uuid4().hex}.txt"
        self.artifacts.append(source)
        source.write_text("conteúdo alfa", encoding="utf-8")
        first = self.query(attachments=[source])
        self.assertEqual(first["status"], "COMPLETE")
        first_record = self.protocol.request_record("Projeto A", "Demanda 1", "task-1", "req-1")

        source.write_text("conteúdo beta", encoding="utf-8")
        with self.assertRaises(RequestConflict):
            self.query(attachments=[source])
        self.assertEqual(self.adapter.send_calls, 1)

        second = self.query(request_id="req-2", attachments=[source])
        self.assertEqual(second["status"], "COMPLETE")
        second_record = self.protocol.request_record("Projeto A", "Demanda 1", "task-1", "req-2")
        self.assertEqual(first_record["prompt_sha256"], second_record["prompt_sha256"])
        self.assertNotEqual(first_record["attachments_sha256"], second_record["attachments_sha256"])
        self.assertNotEqual(first_record["payload_sha256"], second_record["payload_sha256"])

        self.protocol.grant(
            "Projeto B", "Demanda 1", "task-1", evidence="Aprovação humana sintética",
            scope="Somente projeto B", synthetic_only=False,
        )
        third = self.protocol.query("Projeto B", "Demanda 1", "task-1", "req-1", "Consulta sintética", synthetic=True)
        self.assertEqual(third["status"], "COMPLETE")
        conn = sqlite3.connect(self.protocol.db_path)
        try:
            rows = conn.execute(
                "SELECT project,demand,conversation_id FROM conversations ORDER BY project"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], "Projeto A")
        self.assertEqual(rows[1][0], "Projeto B")
        self.assertNotEqual(rows[0][2], rows[1][2])

    def test_attachment_basenames_are_unique_case_insensitively_before_ui(self):
        self.approve()
        first = self.state / "folder-a" / "Evidence.TXT"
        second = self.state / "folder-b" / "evidence.txt"
        first.parent.mkdir()
        second.parent.mkdir()
        first.write_bytes(b"synthetic first file")
        second.write_bytes(b"synthetic second file")
        self.artifacts.extend((first, second))

        with self.assertRaisesRegex(ValueError, "basenames must be unique"):
            self.query(attachments=[first, second])
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual(self.adapter.send_attempts, 0)
        self.assertIsNone(self.protocol.request_record("Projeto A", "Demanda 1", "task-1", "req-1"))

    def test_query_rehashes_attachments_after_create_preflight_and_draft(self):
        cases = (
            ("create", "BEFORE_DRAFT", False),
            ("draft", "BEFORE_DISPATCH", True),
            (("preflight", 3), "BEFORE_DISPATCH", True),
        )
        for index, (mutation, expected_stage, draft_expected) in enumerate(cases):
            with self.subTest(mutation=mutation):
                adapter = FakeAdapter()
                protocol = self.new_protocol(adapter)
                project = f"Mutação sintética {index}"
                protocol.grant(
                    project, "Demanda 1", "task-1", evidence="Synthetic grant",
                    scope="Synthetic attachment hashing test", synthetic_only=False,
                )
                attachment = self.state / f".mutating-attachment-{uuid.uuid4().hex}.txt"
                attachment.write_bytes(b"initial synthetic attachment")
                self.artifacts.append(attachment)
                adapter.mutate_path = attachment
                adapter.mutate_on = mutation

                with self.assertRaisesRegex(ProtocolError, "changed at"):
                    protocol.query(
                        project, "Demanda 1", "task-1", f"mutating-{index}",
                        "Synthetic prompt", [attachment], synthetic=True,
                    )

                record = protocol.request_record(project, "Demanda 1", "task-1", f"mutating-{index}")
                self.assertEqual(record["status"], "PREPARING")
                self.assertEqual(adapter.send_calls, 0)
                self.assertEqual(adapter.send_attempts, 0)
                call_names = [call[0] for call in adapter.calls]
                self.assertEqual("draft" in call_names, draft_expected)
                self.assertNotIn("send", call_names)
                event_types = [
                    event["event_type"]
                    for event in protocol.request_events(project, "Demanda 1", "task-1", f"mutating-{index}")
                ]
                self.assertEqual(event_types, [f"PAYLOAD_CHANGED_{expected_stage}"])

    def test_resume_rehashes_attachments_before_preparation_and_dispatch(self):
        cases = (
            ("observe", "BEFORE_RESUME_PREPARATION"),
            ("resume_preparation", "BEFORE_DISPATCH"),
        )
        for index, (mutation, expected_stage) in enumerate(cases):
            with self.subTest(mutation=mutation):
                adapter = FakeAdapter()
                protocol = self.new_protocol(adapter)
                project = f"Retomada sintética {index}"
                protocol.grant(
                    project, "Demanda 1", "task-1", evidence="Synthetic grant",
                    scope="Synthetic resume hashing test", synthetic_only=False,
                )
                attachment = self.state / f".resume-attachment-{uuid.uuid4().hex}.txt"
                attachment.write_bytes(b"initial synthetic attachment")
                self.artifacts.append(attachment)
                adapter.create_error = True
                with self.assertRaises(RuntimeError):
                    protocol.query(
                        project, "Demanda 1", "task-1", f"resume-mutating-{index}",
                        "Synthetic prompt", [attachment], synthetic=True,
                    )
                adapter.create_error = False
                adapter.observe_results.append({
                    "conversation_id": None,
                    "found": False,
                    "complete": False,
                    "text": "",
                    "blocked": None,
                })
                adapter.mutate_path = attachment
                adapter.mutate_on = mutation

                with self.assertRaisesRegex(ProtocolError, "changed at"):
                    protocol.resume_preparing(
                        project, "Demanda 1", "task-1", f"resume-mutating-{index}",
                        "Synthetic prompt", [attachment], synthetic=True,
                    )

                record = protocol.request_record(project, "Demanda 1", "task-1", f"resume-mutating-{index}")
                self.assertEqual(record["status"], "PREPARING")
                self.assertEqual(adapter.send_calls, 0)
                self.assertEqual(adapter.send_attempts, 0)
                self.assertNotIn("send", [call[0] for call in adapter.calls])
                event_types = [
                    event["event_type"]
                    for event in protocol.request_events(project, "Demanda 1", "task-1", f"resume-mutating-{index}")
                ]
                self.assertEqual(event_types[-1], f"PAYLOAD_CHANGED_{expected_stage}")

    def test_same_request_id_with_different_prompt_is_refused(self):
        self.approve()
        self.query(request_id="fixed-id", prompt="Original sintético")
        with self.assertRaises(RequestConflict):
            self.query(request_id="fixed-id", prompt="Conteúdo diferente")
        self.assertEqual(self.adapter.send_calls, 1)

    def test_request_id_is_unique_across_tasks_in_same_pair(self):
        self.approve(task_id="task-1")
        self.query(request_id="pair-unique", prompt="Consulta sintética")
        self.protocol.grant(
            "Projeto A", "Demanda 1", "task-2", evidence="Aprovação humana sintética",
            scope="Tarefa dois", synthetic_only=False,
        )
        with self.assertRaises(RequestConflict):
            self.protocol.query("Projeto A", "Demanda 1", "task-2", "pair-unique", "Consulta sintética", synthetic=True)
        self.assertEqual(self.adapter.send_calls, 1)

    def test_global_conversation_id_cannot_be_reused_for_another_pair(self):
        self.adapter.conversation_ids = deque(["same-ui-id", "same-ui-id"])
        self.approve()
        self.query()
        self.protocol.grant(
            "Projeto B", "Demanda 1", "task-1", evidence="Aprovação humana sintética",
            scope="Projeto B", synthetic_only=False,
        )
        with self.assertRaises(ConversationConflict):
            self.protocol.query("Projeto B", "Demanda 1", "task-1", "req-b", "Consulta sintética", synthetic=True)
        self.assertEqual(self.adapter.send_calls, 1)

    def test_request_count_and_byte_quotas(self):
        self.adapter = FakeAdapter()
        self.protocol = self.new_protocol(self.adapter, max_requests_per_task=1)
        self.approve()
        self.query(request_id="quota-1")
        with self.assertRaises(QuotaExceeded):
            self.query(request_id="quota-2")
        self.assertEqual(self.adapter.send_calls, 1)

        small_adapter = FakeAdapter()
        small = self.new_protocol(small_adapter, max_request_bytes=5)
        small.grant(
            "P", "D", "T", evidence="Aprovação sintética", scope="Teste sintético", synthetic_only=False,
        )
        with self.assertRaises(QuotaExceeded):
            small.query("P", "D", "T", "too-large", "seis!!", synthetic=True)
        self.assertEqual(small_adapter.send_calls, 0)

    def test_process_level_global_ui_lock(self):
        child_code = """
import sys
sys.path.insert(0, sys.argv[1])
from protocol import exclusive_ui_lock, LockBusy
try:
    with exclusive_ui_lock(sys.argv[2], timeout=0.15):
        raise SystemExit(12)
except LockBusy:
    raise SystemExit(0)
raise SystemExit(13)
"""
        with exclusive_ui_lock(self.state, timeout=1.0):
            completed = subprocess.run(
                [sys.executable, "-B", "-c", child_code, str(SCRIPTS), str(self.state)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        self.assertEqual(completed.returncode, 0)


if __name__ == "__main__":
    unittest.main()
