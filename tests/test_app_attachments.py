"""Synthetic adapter guards, with native UI dependencies replaced before import."""
from pathlib import Path
import importlib.util
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SCRIPTS=Path(__file__).resolve().parents[1]/"skill"/"copilot-consult"/"scripts"
sys.path.insert(0,str(SCRIPTS))
spec=importlib.util.spec_from_file_location("attachment_guard_test_adapter",SCRIPTS/"app.py")
adapter=importlib.util.module_from_spec(spec)
with patch.dict(sys.modules,{"native_uia":SimpleNamespace(Automation=Mock(side_effect=AssertionError("No real UI in tests")),TYPES={})}):
    spec.loader.exec_module(adapter)


class AttachmentGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="attachment-test-",dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.file=Path(self.temp.name)/"synthetic.txt"
        self.file.write_text("first synthetic contents",encoding="utf-8")
        self.app=object.__new__(adapter.CopilotApp)
        self.app.window=lambda:None
        self.app.expected_attachments=()
        self.app.a=SimpleNamespace(snapshot=Mock(return_value=[]))

    def test_changed_source_is_rejected_after_preparation(self):
        self.app.expected_attachments=(self.app.attachment_fingerprint(self.file),)
        self.file.write_text("changed synthetic contents",encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError,"changed after preparation"):
            self.app.verify_prepared_attachments()

    def test_missing_or_extra_attachment_chip_is_rejected(self):
        self.app.expected_attachments=(self.app.attachment_fingerprint(self.file),)
        with self.assertRaisesRegex(RuntimeError,"controls changed"):
            self.app.verify_prepared_attachments()
        self.app.expected_attachments=()
        self.app.a.snapshot.return_value=[{"type":"Button","name":"Remover anexo synthetic.txt"}]
        with self.assertRaisesRegex(RuntimeError,"controls changed"):
            self.app.verify_prepared_attachments()

    def test_matching_fingerprint_and_chip_are_accepted(self):
        self.app.expected_attachments=(self.app.attachment_fingerprint(self.file),)
        self.app.a.snapshot.return_value=[{"type":"Button","name":"Remover anexo synthetic.txt"}]
        self.app.verify_prepared_attachments()

    def test_existing_upload_cannot_resume_from_name_only(self):
        self.app.conversation_id=lambda:"synthetic-conversation"
        self.app.exact=lambda **kw:SimpleNamespace(value=lambda:"synthetic prompt")
        self.app.a.snapshot.return_value=[{"type":"Button","name":"Remover anexo synthetic.txt"}]
        self.app.ensure_model=Mock(side_effect=AssertionError("Must stop before further UI"))
        with self.assertRaisesRegex(RuntimeError,"Cannot verify existing uploaded"):
            self.app.resume_preparation("synthetic-marker","synthetic prompt",(self.file,),"synthetic-conversation")
        self.app.ensure_model.assert_not_called()


if __name__=="__main__":
    unittest.main()
