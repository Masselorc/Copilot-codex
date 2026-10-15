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


class ContextSelectorTests(unittest.TestCase):
    """Synthetic UI trees only; no window discovery or real invocation."""
    class Node:
        def __init__(self,*,id="",name="",type="Hyperlink",url="",children=()):
            self.props={"id":id,"name":name,"type":type,"offscreen":False}
            self.url=url
            self.children=list(children)
            self.invoke=Mock(side_effect=AssertionError("No action in selector tests"))
        def prop(self,key):return self.props[key]
        def value(self):return self.url

    def make_app(self,children,*,url="https://m365.cloud.microsoft/hwav2/chat"):
        doc=self.Node(id="RootWebArea",type="Document",url=url,children=children)
        app=object.__new__(adapter.CopilotApp)
        app.root=lambda:doc
        def exact(root,**selector):
            matches=[node for node in root.children if all(node.prop(key)==value for key,value in selector.items() if key!="visible") and not node.prop("offscreen")]
            if len(matches)!=1:
                raise OSError("Synthetic selector ambiguity, found "+str(len(matches)))
            return matches[0]
        app.a=SimpleNamespace(exact=Mock(side_effect=exact))
        return app,doc

    def test_history_title_collision_resolves_by_context_and_identity_both_orders(self):
        action=self.Node(name="Novo chat")
        history=self.Node(id="synthetic-conversation-id",name="Novo chat")
        for nodes in ([history,action],[action,history]):
            with self.subTest(history_first=nodes[0] is history):
                app,doc=self.make_app(nodes)
                self.assertIs(app.new_chat_control(),action)
                app.a.exact.assert_called_once_with(doc,id="",name="Novo chat",type="Hyperlink")
        action.invoke.assert_not_called()
        history.invoke.assert_not_called()

    def test_duplicate_actions_fail_before_navigation(self):
        first=self.Node(name="Novo chat")
        second=self.Node(name="Novo chat")
        app,_=self.make_app([first,second])
        app.no_draft=Mock()
        with self.assertRaisesRegex(OSError,"found 2"):
            app.create_conversation("synthetic-marker")
        first.invoke.assert_not_called()
        second.invoke.assert_not_called()

    def test_history_only_never_becomes_new_chat_action(self):
        history=self.Node(id="synthetic-conversation-id",name="Novo chat")
        app,_=self.make_app([history])
        with self.assertRaisesRegex(OSError,"found 0"):
            app.new_chat_control()
        history.invoke.assert_not_called()

    def test_action_outside_validated_document_is_not_considered(self):
        outside=self.Node(name="Novo chat")
        app,_=self.make_app([])
        app.window=lambda:self.Node(type="Window",children=[outside])
        with self.assertRaisesRegex(OSError,"found 0"):
            app.new_chat_control()
        outside.invoke.assert_not_called()

    def test_unexpected_document_stops_before_selecting_any_link(self):
        for url in ("https://unrelated.example/hwav2/chat","https://m365.cloud.microsoft/hwav2/chat-settings"):
            with self.subTest(url=url):
                action=self.Node(name="Novo chat")
                app,_=self.make_app([action],url=url)
                with self.assertRaisesRegex(RuntimeError,"Unexpected Copilot app document"):
                    app.new_chat_control()
                app.a.exact.assert_not_called()
                action.invoke.assert_not_called()

    def test_changed_action_identity_is_refused_without_fallback(self):
        action=self.Node(id="synthetic-new-layout",name="Novo chat")
        app,_=self.make_app([action])
        with self.assertRaisesRegex(OSError,"found 0"):
            app.new_chat_control()
        action.invoke.assert_not_called()


if __name__=="__main__":
    unittest.main()
