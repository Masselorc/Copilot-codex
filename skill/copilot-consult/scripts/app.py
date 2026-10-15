"""Adapter for the installed Microsoft 365 Copilot Windows application."""
from pathlib import Path
import hashlib
import re
import time
from urllib.parse import urlsplit

from native_uia import Automation, TYPES
from response_text import code_from_accessible_lines
from protocol import SendNotAttempted

MODEL_LABEL = "Opus 5.5, Seletor de Modelo"


class CopilotApp:
    def __init__(self, wait_seconds=12):
        self.a=Automation()
        self.w=self.a.copilot()
        self.identity=(self.w.prop("handle"),self.w.prop("pid"))
        self.wait_seconds=max(0,min(float(wait_seconds),55))
        self.expected_prompt=None
        self.expected_attachments=()
        self.expected_conversation=None
        self.advertised_extensions=[]

    def window(self):
        fresh=self.a.copilot()
        if (fresh.prop("handle"),fresh.prop("pid")) != self.identity:
            raise RuntimeError("Copilot window changed; reconnect after inspecting the UI")
        return fresh

    def exact(self, **selector):
        return self.a.exact(self.window(),**selector)

    def root(self):
        return self.exact(id="RootWebArea",type="Document")

    def app_document(self):
        document=self.root()
        url=urlsplit(document.value())
        if url.scheme != "https" or url.hostname != "m365.cloud.microsoft" or not re.fullmatch(r"/hwav2/chat(?:/conversation/[0-9a-fA-F-]{36})?/?",url.path):
            raise RuntimeError("Unexpected Copilot app document location; stop")
        return document,url

    def conversation_id(self):
        _,url=self.app_document()
        match=re.fullmatch(r"/hwav2/chat/conversation/([0-9a-fA-F-]{36})/?",url.path)
        return match.group(1).lower() if match else None

    def new_chat_control(self):
        """Observed action identity, scoped to the validated Copilot document.

        A history conversation can also be titled 'Novo chat', but carries a
        conversation automation ID. The observed navigation action has an empty
        ID. Require that complete identity and uniqueness; never select first
        or fall back to a history entry when the layout changes.
        """
        document,_=self.app_document()
        return self.a.exact(document,id="",name="Novo chat",type="Hyperlink")

    def wait(self, read, predicate, seconds=10):
        deadline=time.monotonic()+seconds
        last=None
        while True:
            try:
                last=read()
                if predicate(last):
                    return last
            except OSError as exc:
                # Only observation retries. Actions never run inside this loop.
                if "0x80040201" not in str(exc) and "found 0" not in str(exc):
                    raise
            if time.monotonic() >= deadline:
                raise RuntimeError("Expected UI state not observed within bounded wait; inspect without retrying input")
            time.sleep(.4)

    def ensure_model(self):
        if self.exact(id="gptModeSwitcher").prop("name") != MODEL_LABEL:
            self.exact(id="gptModeSwitcher").expand()
            self.a.exact(self.window(),name="Claude Anthropic",type="MenuItem").expand()
            self.a.exact(self.window(),name="Opus 5.5",type="MenuItem").invoke()
            self.wait(lambda:self.exact(id="gptModeSwitcher").prop("name"),lambda v:v==MODEL_LABEL)
        if self.exact(id="gptModeSwitcher").prop("name") != MODEL_LABEL:
            raise RuntimeError("Claude Opus 5.5 unavailable or not selected; no substitution")

    def preflight(self):
        self.conversation_id()  # Verify app document host/path without any network call.
        items=self.a.snapshot(self.window())
        auth=any(e["type"]=="Button" and "Conta corporativa" in e["name"] for e in items)
        modal=any(e["type"]=="Window" and e["name"] for e in items)
        busy=any(e["name"]=="Parar de gerar" and e["type"]=="Button" for e in items)
        if not auth or modal or busy:
            return {"model":None,"authenticated":auth,"blocked":"Login, dialog or generation pending"}
        self.ensure_model()
        return {"model":"Claude Opus 5.5","authenticated":True,"blocked":None}

    def no_draft(self):
        # The empty WebView editor exposes one newline, not an existing draft.
        if self.exact(id="m365-chat-editor-target-element").value() not in ("", "\n"):
            raise RuntimeError("Existing draft preserved; do not navigate or overwrite")
        if any(e["type"]=="Button" and e["name"].startswith("Remover anexo ") for e in self.a.snapshot(self.window())):
            raise RuntimeError("Existing attachments preserved; do not navigate")

    def create_conversation(self, marker):
        self.no_draft()
        self.new_chat_control().invoke()
        self.wait(self.conversation_id,lambda v:v is None)
        self.no_draft()
        self.ensure_model()
        self.expected_conversation=None
        return None  # The application assigns its ID only after the first send.

    def open_conversation(self, conversation_id):
        if not re.fullmatch(r"[0-9a-fA-F-]{36}",conversation_id):
            raise ValueError("Invalid observed conversation ID")
        self.expected_conversation=conversation_id.lower()
        if self.conversation_id()==self.expected_conversation:
            return
        self.no_draft()
        link=self.exact(id=self.expected_conversation,type="Hyperlink",visible=False)
        if link.prop("offscreen"):
            link.scroll_into_view()
        self.exact(id=self.expected_conversation,type="Hyperlink").invoke()
        self.wait(self.conversation_id,lambda v:v==self.expected_conversation)

    def attach(self, path):
        path=Path(path).resolve(strict=True)
        if not path.is_file() or path.stat().st_size > 10*1024*1024:
            raise ValueError("Attachment missing or above local 10 MiB guard")
        self.exact(name="Adicionar e gerenciar fontes",type="Button").expand()
        self.exact(name="Carregar",type="MenuItem").expand()
        self.exact(name="Carregar imagens e arquivos",type="MenuItem").invoke()
        dialog=self.exact(name="Abrir",type="Window")
        file_filter=self.a.exact(dialog,id="1136",type="ComboBox").value()
        self.advertised_extensions=sorted(set(re.findall(r"\*([.][a-zA-Z0-9]+)",file_filter.lower())))
        if path.suffix.lower() not in self.advertised_extensions:
            self.a.exact(dialog,id="2",name="Cancelar",type="Button").invoke()
            raise ValueError("Extension not advertised by this Copilot picker; no upload")
        field=self.a.exact(dialog,id="1148",type="Edit")
        if field.value():
            raise RuntimeError("File picker contains a user draft; preserved")
        field.set_value(str(path))
        if self.a.exact(dialog,id="1148",type="Edit").value()!=str(path):
            raise RuntimeError("File picker readback mismatch; no upload")
        self.a.exact(dialog,id="1",name="Abrir",type="Button").invoke()
        def uploaded():
            rows=self.a.snapshot(self.window())
            chip=any(e["name"]=="Remover anexo "+path.name and e["type"]=="Button" for e in rows)
            pending=any("Upload de "+path.name+" em andamento" in e["name"] for e in rows)
            return chip and not pending
        self.wait(uploaded,bool,seconds=20)

    @staticmethod
    def attachment_fingerprint(path):
        path=Path(path).resolve(strict=True)
        before=path.stat()
        if not path.is_file() or before.st_size>10*1024*1024:
            raise RuntimeError("Attachment missing or above local guard")
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        after=path.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
            raise RuntimeError("Attachment changed during verification")
        return (path,before.st_size,digest)

    def verify_prepared_attachments(self):
        expected=self.expected_attachments
        if tuple(self.attachment_fingerprint(item[0]) for item in expected)!=expected:
            raise RuntimeError("Attachment changed after preparation; no send")
        chips=[e["name"][len("Remover anexo "):] for e in self.a.snapshot(self.window())
               if e["type"]=="Button" and e["name"].startswith("Remover anexo ")]
        if sorted(chips)!=sorted(item[0].name for item in expected):
            raise RuntimeError("Prepared attachment controls changed; no send")

    def draft(self, prompt, attachments=()):
        self.ensure_model()
        if self.expected_conversation is not None and self.conversation_id()!=self.expected_conversation:
            raise RuntimeError("Conversation changed before composing")
        self.no_draft()
        self.expected_attachments=tuple(self.attachment_fingerprint(path) for path in attachments)
        names=[item[0].name.casefold() for item in self.expected_attachments]
        if len(names)!=len(set(names)):
            raise RuntimeError("Duplicate attachment names cannot be distinguished by the UI")
        for path in attachments:
            self.attach(path)
        self.verify_prepared_attachments()
        self.expected_prompt=prompt.replace("\r\n","\n").replace("\r","\n")
        self.a.type_text(self.window(),self.exact(id="m365-chat-editor-target-element"),self.expected_prompt)
        def matches(value):
            return value==self.expected_prompt or value==self.expected_prompt+"\n"
        self.wait(lambda:self.exact(id="m365-chat-editor-target-element").value(),matches)

    def resume_preparation(self, marker, prompt, attachments, known_conversation_id):
        """Resume only an intentional send that the ledger has never attempted."""
        current=self.conversation_id()
        value=self.exact(id="m365-chat-editor-target-element").value()
        chips=[e["name"][len("Remover anexo "):] for e in self.a.snapshot(self.window())
               if e["type"]=="Button" and e["name"].startswith("Remover anexo ")]
        expected=prompt.replace("\r\n","\n").replace("\r","\n")
        if chips:
            # A filename chip cannot prove the contents of an upload left by
            # an earlier adapter instance, even when its name matches exactly.
            raise RuntimeError("Cannot verify existing uploaded attachment contents; draft preserved")
        if value in (expected,expected+"\n"):
            if attachments or current!=known_conversation_id:
                raise RuntimeError("Prepared draft identity/attachments do not match the ledger; preserved")
            self.expected_conversation=current
            self.expected_prompt=expected
            self.expected_attachments=()
            self.ensure_model()
            return
        if value not in ("", "\n") or chips:
            raise RuntimeError("Unrelated draft/attachment preserved; preparation cannot resume")
        if known_conversation_id:
            self.open_conversation(known_conversation_id)
        else:
            self.create_conversation(marker)
        self.draft(prompt,attachments)

    def send(self):
        if self.expected_prompt is None:
            raise SendNotAttempted("No verified draft in this adapter instance")
        self.last_send_stage="VALIDATING"
        def ready():
            if self.exact(id="gptModeSwitcher").prop("name")!=MODEL_LABEL:
                raise RuntimeError("Model drift before send; do not send")
            actual=self.exact(id="m365-chat-editor-target-element").value()
            return actual in (self.expected_prompt,self.expected_prompt+"\n") and self.exact(name="Enviar",type="Button").prop("enabled")
        try:
            # Image previews can push Send outside the visible viewport even
            # while the editor stays visible. Scroll the exact observed control.
            control=self.exact(name="Enviar",type="Button",visible=False)
            if control.prop("offscreen"):
                control.scroll_into_view()
            self.wait(ready,bool,seconds=45)
            self.verify_prepared_attachments()
            if self.expected_conversation and self.conversation_id()!=self.expected_conversation:
                raise RuntimeError("Conversation changed before send")
        except (OSError,RuntimeError) as exc:
            raise SendNotAttempted(str(exc)) from exc
        self.last_send_stage="INVOKING"
        # One normal click on the observed, now-visible Send control.
        self.a.click_control(self.window(),self.exact(name="Enviar",type="Button"))
        self.last_send_stage="INVOKED"

    def observe_once(self, marker):
        conversation=self.conversation_id()
        result={"conversation_id":conversation,"found":False,"complete":False,"text":"","blocked":None}
        nodes=self.a.all(self.window())
        seen=False
        response=None
        generating=False
        for e in nodes:
            name=e.prop("name")
            kind=TYPES.get(e.prop("type"))
            if kind=="Button" and name=="Parar de gerar":
                generating=True
            if kind=="Group" and name.startswith("You said:"):
                if seen:
                    break
                seen=marker in name
                result["found"]=seen
            elif seen and kind=="Group" and name.startswith("Copilot said:"):
                response=e
        if not response:
            return result
        children=self.a.all(response,2)
        bodies=[e for e in children if TYPES.get(e.prop("type"))=="Group" and e.prop("name")==""]
        if len(bodies)!=1:
            return result  # Never fall back to whole-document text/sidebar history.
        text=self.root().range_text(bodies[0])
        # Rich code views mix line numbers and indentation in TextPattern.
        # The individual accessible line Names preserve the source whitespace.
        viewers=[e for e in self.a.all(bodies[0]) if e.prop("type")==50026 and e.prop("name")=="Visualização de Código"]
        for viewer in viewers:
            editor=self.a.exact(viewer,name="Editor de código",type="Edit",visible=False)
            rows=[e.describe() for e in self.a.all(editor,2)]
            try:
                code=code_from_accessible_lines(rows)
            except ValueError as exc:
                result["blocked"]=str(exc)
                return result
            names=[e.prop("name") for e in self.a.all(viewer) if e.prop("type")==50020]
            language=names[0].lower() if names and re.fullmatch(r"[a-zA-Z0-9+#._-]{1,32}",names[0]) else ""
            raw_view=self.root().range_text(viewer)
            if not raw_view or text.count(raw_view)!=1:
                result["blocked"]="Code viewer range is ambiguous; full response not verified"
                return result
            text=text.replace(raw_view,"```"+language+"\n"+code+"\n```",1)
        actions=self.a.snapshot(response)
        copy=any(e["type"]=="Button" and e["name"]=="Copiar Resposta" for e in actions)
        used=any(e["type"]=="Button" and "Usado Opus 5.5" in e["name"] for e in actions)
        if any(s in text.lower() for s in ["atingiu o limite", "limite de uso", "limite de mensagens"]):
            result["blocked"]="Copilot usage limit reported; continue without Copilot"
            return result
        result.update(text=text,complete=bool(text.strip()) and copy and used and not generating)
        return result

    def observe(self, marker):
        deadline=time.monotonic()+self.wait_seconds
        previous=None
        result={"conversation_id":None,"found":False,"complete":False,"text":"","blocked":None}
        while True:
            try:
                result=self.observe_once(marker)
                if result["blocked"]:
                    return result
                if result["complete"] and result["text"]==previous:
                    return result
                previous=result["text"] if result["complete"] else None
            except OSError as exc:
                if "0x80040201" not in str(exc) and "found 0" not in str(exc):
                    raise
            if time.monotonic() >= deadline:
                result["complete"]=False
                return result
            time.sleep(.6)
