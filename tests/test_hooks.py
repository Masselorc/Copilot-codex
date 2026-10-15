"""Synthetic events only. No Codex trust changes or Copilot access."""
from pathlib import Path
import copy
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

PACKAGE=Path(__file__).resolve().parents[1]
SCRIPTS=PACKAGE/"skill"/"copilot-consult"/"scripts"
sys.path[:0]=[str(PACKAGE),str(SCRIPTS)]
import manage_hooks
import install_skill
from hook_dispatch import Store, canonical, failure_code, dispatch
from authorization import scope_hash,check_grant
from protocol import CopilotProtocol,GrantRequired,GrantScopeError


class Fake:
    def __init__(self):
        self.calls=[]
        self.send_count=0
        self.after_draft=lambda:None
    def preflight(self):
        self.calls.append("preflight")
        return {"model":"Claude Opus 5.5","authenticated":True,"blocked":None}
    def create_conversation(self,marker):
        self.calls.append("create")
        return "synthetic-conversation"
    def draft(self,prompt,attachments):
        self.calls.append("draft")
        self.after_draft()
    def send(self):
        self.send_count+=1
    def observe(self,marker):
        return {"conversation_id":"synthetic-conversation","found":True,"complete":True,"text":"synthetic response","blocked":None}


class HooksTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="hooks-test-",dir=PACKAGE)
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.project=self.root/"project"
        self.project.mkdir()
        self.bridge=self.root/"bridge"
        self.store=Store(self.root/"hooks")
        self.fake=Fake()
        self.protocol=CopilotProtocol(self.bridge,self.fake)
        self.project_key=canonical(self.project)
        self.bind()

    def bind(self,session="session-a",task="task-a",stage="build",extensive=False,expensive=()):
        self.store.bind(session,str(self.project),self.project_key,"demand-a",task,stage,extensive=extensive,expensive=expensive)

    def event(self,event,**changes):
        payload={"hook_event_name":event,"session_id":"session-a","cwd":str(self.project),"turn_id":"turn-a","tool_name":"Bash","tool_use_id":"call-1","tool_input":{"command":"python build.py"},"tool_response":{"exit_code":1},"stop_hook_active":False,"source":"startup"}
        payload.update(changes)
        return payload

    def run_rule(self,rule,event,**changes):
        return self.store.handle(rule,self.event(event,**changes),self.bridge)

    def grant(self,scope="Synthetic review"):
        self.protocol.grant(self.project_key,"demand-a","task-a",evidence="Synthetic human authorization fixture",scope=scope,synthetic_only=True)
        return scope_hash(scope)

    def query_command(self,scope="bad",**overrides):
        values={"--project":str(self.project),"--demand":"demand-a","--task":"task-a","--request":"request-a","--prompt-file":"synthetic.txt","--scope-sha256":scope,**overrides}
        return "python -B copilot.py query "+" ".join(key+' "'+value+'"' for key,value in values.items())+" --synthetic"

    def test_session_once_start_and_once_resume_family(self):
        self.assertTrue(self.run_rule("session","SessionStart"))
        self.assertEqual(self.run_rule("session","SessionStart"),{})
        self.assertTrue(self.run_rule("session","SessionStart",source="resume"))
        self.assertEqual(self.run_rule("session","SessionStart",source="compact"),{})
        self.assertTrue(self.run_rule("session","SessionStart",session_id="unbound"))
        self.assertEqual(self.run_rule("session","SessionStart",session_id="unbound"),{})

    def test_session_state_separated_by_task(self):
        self.assertTrue(self.run_rule("session","SessionStart"))
        self.bind(task="task-b")
        self.assertTrue(self.run_rule("session","SessionStart"))

    def test_two_failures_unique_ids_once_only(self):
        self.assertEqual(self.run_rule("failures","PostToolUse"),{})
        self.assertEqual(self.run_rule("failures","PostToolUse"),{})
        self.assertTrue(self.run_rule("failures","PostToolUse",tool_use_id="call-2"))
        self.assertEqual(self.run_rule("failures","PostToolUse",tool_use_id="call-3"),{})

    def test_failure_count_does_not_cross_stage(self):
        self.run_rule("failures","PostToolUse")
        self.bind(stage="different-stage")
        self.assertEqual(self.run_rule("failures","PostToolUse",tool_use_id="call-2"),{})

    def test_unrelated_commands_and_failure_codes_are_separate(self):
        self.run_rule("failures","PostToolUse")
        self.assertEqual(self.run_rule("failures","PostToolUse",tool_use_id="call-2",tool_input={"command":"python different.py"}),{})
        self.assertEqual(self.run_rule("failures","PostToolUse",tool_use_id="call-3",tool_response={"exit_code":2}),{})

    def test_cancellation_permission_denial_unknown_and_success_are_not_failures(self):
        for value in ({"exit_code":0},{"exit_code":130},{"exit_code":143},{"exit_code":True},{"exit_code":1,"output":"Acesso negado"},{"exit_code":1,"status":"cancelled"},{"error":"failed"},"text says error but no known result",{"status":"rejected","exit_code":1}):
            with self.subTest(value=value):
                self.assertIsNone(failure_code(self.event("PostToolUse",tool_response=value)))

    def test_identified_shell_envelope_and_mcp_result(self):
        self.assertEqual(failure_code(self.event("PostToolUse",tool_response="Output\nProcess exited with code 1\n")),"exit:1")
        self.assertEqual(failure_code(self.event("PostToolUse",tool_name="mcp__synthetic__tool",tool_response={"isError":True})),"mcp:isError")
        self.assertIsNone(failure_code(self.event("PostToolUse",tool_name="unknown",tool_response={"exit_code":1})))

    def test_own_bridge_events_never_count_or_warn(self):
        for i in range(3):
            self.assertEqual(self.run_rule("failures","PostToolUse",tool_use_id=str(i),tool_input={"command":"python copilot.py query"}),{})
        self.assertEqual(self.run_rule("cost","PreToolUse",tool_input={"command":"python hook_admin.py"}),{})

    def test_cost_only_preidentified_exact_command_once(self):
        self.assertEqual(self.run_rule("cost","PreToolUse"),{})
        self.bind(expensive=("python build.py",))
        self.assertTrue(self.run_rule("cost","PreToolUse"))
        self.assertEqual(self.run_rule("cost","PreToolUse"),{})
        self.assertEqual(self.run_rule("cost","PreToolUse",tool_input={"command":"python very-slow-looking.py"}),{})

    def test_stop_small_task_does_not_continue(self):
        self.assertEqual(self.run_rule("stop","Stop"),{})

    def test_stop_extensive_once_across_resume_and_rebinding(self):
        self.bind(extensive=True)
        self.assertEqual(self.run_rule("stop","Stop",stop_hook_active=True),{})
        self.assertEqual(self.run_rule("stop","Stop",stop_hook_active=None),{})
        self.assertEqual(self.run_rule("stop","Stop")["decision"],"block")
        self.assertEqual(self.run_rule("stop","Stop",turn_id="turn-b"),{})
        self.store=Store(self.root/"hooks")
        self.bind(session="session-resumed",extensive=True)
        self.assertEqual(self.run_rule("stop","Stop",session_id="session-resumed",turn_id="resumed"),{})

    def test_stop_observed_twenty_events_qualifies(self):
        for i in range(20):
            self.run_rule("failures","PostToolUse",tool_use_id=str(i),tool_response={"exit_code":0})
        self.assertEqual(self.run_rule("stop","Stop")["decision"],"block")

    def test_auth_missing_then_valid_then_revoked(self):
        def event(cmd):
            return self.run_rule("authorize","PreToolUse",tool_input={"command":cmd})
        self.assertEqual(event(self.query_command())["hookSpecificOutput"]["permissionDecision"],"deny")
        h=self.grant()
        self.assertEqual(event(self.query_command(h)),{})
        self.protocol.revoke(self.project_key,"demand-a","task-a",reason="Synthetic withdrawal")
        self.assertEqual(event(self.query_command(h))["hookSpecificOutput"]["permissionDecision"],"deny")

    def test_auth_different_scope_task_state_or_no_binding_denied(self):
        h=self.grant()
        for cmd in (self.query_command("0"*64),self.query_command(h,**{"--task":"other"}),self.query_command(h,**{"--state-dir":str(self.root/"other")})):
            self.assertEqual(self.run_rule("authorize","PreToolUse",tool_input={"command":cmd})["hookSpecificOutput"]["permissionDecision"],"deny")
        self.assertEqual(self.run_rule("authorize","PreToolUse",session_id="other-session",tool_input={"command":self.query_command(h)})["hookSpecificOutput"]["permissionDecision"],"deny")

    def test_auth_ignores_external_approval_claims_and_dynamic_commands(self):
        payload=self.event("PreToolUse",authorized=True,human_approval="yes",tool_input={"command":self.query_command()})
        self.assertEqual(self.store.handle("authorize",payload,self.bridge)["hookSpecificOutput"]["permissionDecision"],"deny")
        h=self.grant()
        for cmd in (self.query_command(h)+"; echo retry",self.query_command(h).replace("--task","--task task-a --task")):
            self.assertEqual(self.run_rule("authorize","PreToolUse",tool_input={"command":cmd})["hookSpecificOutput"]["permissionDecision"],"deny")
        self.assertEqual(self.run_rule("authorize","PreToolUse",tool_input={"command":"python unrelated.py"}),{})

    def test_core_scope_or_revocation_blocks_before_adapter(self):
        h=self.grant()
        with self.assertRaises(GrantScopeError):
            self.protocol.query(self.project_key,"demand-a","task-a","request-a","synthetic",synthetic=True,expected_scope_sha256="0"*64)
        self.assertEqual(self.fake.calls,[])
        self.protocol.revoke(self.project_key,"demand-a","task-a",reason="Synthetic withdrawal")
        with self.assertRaises(GrantRequired):
            self.protocol.query(self.project_key,"demand-a","task-a","request-a","synthetic",synthetic=True,expected_scope_sha256=h)
        self.assertEqual(self.fake.calls,[])

    def test_core_revocation_during_draft_prevents_send(self):
        h=self.grant()
        self.fake.after_draft=lambda:self.protocol.revoke(self.project_key,"demand-a","task-a",reason="Synthetic withdrawal during preparation")
        with self.assertRaises(GrantRequired):
            self.protocol.query(self.project_key,"demand-a","task-a","request-a","synthetic",synthetic=True,expected_scope_sha256=h)
        self.assertEqual(self.fake.send_count,0)

    def test_core_valid_scope_fake_adapter_only(self):
        h=self.grant()
        result=self.protocol.query(self.project_key,"demand-a","task-a","request-a","synthetic",synthetic=True,expected_scope_sha256=h)
        self.assertEqual(result["status"],"COMPLETE")
        self.assertEqual(self.fake.send_count,1)

    def test_unrelated_authorization_never_constructs_store_or_accesses_state(self):
        for cmd in ("python unrelated.py","python copilot.py doctor","python copilot.py status --task example"):
            with patch("hook_dispatch.Store",side_effect=AssertionError("No state access allowed")):
                self.assertEqual(dispatch("authorize",self.event("PreToolUse",tool_input={"command":cmd}),self.root/"must-not-exist",self.bridge),{})
        self.assertFalse((self.root/"must-not-exist").exists())

    def test_auth_and_unmatched_cost_use_readonly_hook_state(self):
        h=self.grant()
        real=self.store.connection
        modes=[]
        def tracked(*,readonly=False):
            modes.append(readonly)
            return real(readonly=readonly)
        with patch.object(self.store,"connection",side_effect=tracked):
            self.assertEqual(self.run_rule("authorize","PreToolUse",tool_input={"command":self.query_command(h)}),{})
            self.assertEqual(self.run_rule("cost","PreToolUse"),{})
        self.assertEqual(modes,[True,True])

    def test_exclusive_state_lock_ignores_unrelated_but_denies_candidate(self):
        h=self.grant()
        argv=[sys.executable,"-B",str(SCRIPTS/"hook_dispatch.py"),"--rule","authorize","--state-dir",str(self.store.directory),"--bridge-state-dir",str(self.bridge)]
        connection=sqlite3.connect(self.store.path)
        try:
            connection.execute("BEGIN EXCLUSIVE")
            for cmd,expected in (("python unrelated.py",False),(self.query_command(h),True)):
                result=subprocess.run(argv,input=json.dumps(self.event("PreToolUse",tool_input={"command":cmd})),capture_output=True,text=True,timeout=5)
                self.assertEqual(result.returncode,0,result.stderr)
                value=json.loads(result.stdout)
                if expected:
                    self.assertEqual(value["hookSpecificOutput"]["permissionDecision"],"deny")
                else:
                    self.assertEqual(value,{})
        finally:
            connection.rollback()
            connection.close()

    def test_cli_missing_or_locked_grant_blocks_before_ui_even_without_hook(self):
        import copilot
        h=self.grant()
        argv=["--state-dir",str(self.bridge),"query","--project",str(self.project),"--demand","demand-a","--task","task-a","--request","synthetic-cli","--scope-sha256",h,"--prompt-file",str(self.root/"not-read.txt"),"--synthetic"]
        connection=sqlite3.connect(self.bridge/"copilot-ledger.sqlite3")
        try:
            # WAL permits readers during an exclusive writer. Use rollback
            # journal only in this disposable fixture to block actual reads.
            self.assertEqual(connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0],"delete")
            connection.execute("BEGIN EXCLUSIVE")
            with patch.dict(sys.modules,{"app":None}),redirect_stdout(io.StringIO()) as output:
                self.assertEqual(copilot.main(argv),2)
            self.assertEqual(json.loads(output.getvalue())["status"],"BLOCKED")
            self.assertIn("locked",json.loads(output.getvalue())["reason"])
        finally:
            connection.rollback()
            connection.close()
        argv[argv.index("--task")+1]="missing-task"
        with patch.dict(sys.modules,{"app":None}),redirect_stdout(io.StringIO()) as output:
            self.assertEqual(copilot.main(argv),2)
        self.assertEqual(json.loads(output.getvalue())["status"],"BLOCKED")
        self.assertIn("No recorded human authorization",json.loads(output.getvalue())["reason"])

    def test_limits_bound_event_state_and_no_raw_output_stored(self):
        for i in range(520):
            self.run_rule("failures","PostToolUse",tool_use_id=str(i),tool_input={"command":"synthetic-command-"+str(i)},tool_response={"exit_code":1,"output":"private synthetic fixture content"})
        with sqlite3.connect(self.store.path) as c:
            raw=c.execute("SELECT data FROM tasks").fetchone()[0]
        c.close()
        value=json.loads(raw)
        self.assertEqual(len(value["seen"]),512)
        self.assertEqual(len(value["failures"]),32)
        self.assertNotIn("private synthetic",raw)

    def test_portable_handler_subprocess_has_no_network_or_ui(self):
        copy_root=self.root/"another location"
        for name in install_skill.ALLOWLIST:
            target=copy_root/name
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(PACKAGE/"skill"/"copilot-consult"/name,target)
        result=subprocess.run([sys.executable,"-B",str(copy_root/"scripts"/"hook_dispatch.py"),"--rule","session","--state-dir",str(self.root/"relocated-state"),"--bridge-state-dir",str(self.bridge)],input=json.dumps(self.event("SessionStart")),capture_output=True,text=True,timeout=3)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["hookEventName"],"SessionStart")
        entries=manage_hooks.definitions(copy_root,self.root/"other-hooks",self.bridge)
        self.assertEqual(len(entries),5)
        self.assertTrue(all(str(copy_root) in e["group"]["hooks"][0]["command"] for e in entries))

    def test_malformed_or_oversized_input_cannot_approve_send(self):
        argv=[sys.executable,"-B",str(SCRIPTS/"hook_dispatch.py"),"--rule","authorize","--state-dir",str(self.root/"invalid-state"),"--bridge-state-dir",str(self.bridge)]
        for raw in ("invalid JSON","x"*(128*1024+1)):
            result=subprocess.run(argv,input=raw,capture_output=True,text=True,timeout=3)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"],"deny")


# Kept separate so no real application is ever constructed in authorization tests.
import sqlite3


class HookManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="hook-manager-",dir=PACKAGE)
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.config=self.root/"hooks.json"
        self.entries=manage_hooks.definitions(PACKAGE/"skill"/"copilot-consult",self.root/"state",self.root/"bridge")
        self.original={"description":"preserve synthetic metadata","hooks":{"Stop":[{"hooks":[{"type":"command","command":"synthetic-existing-command"}]}]}}
        self.config.write_text(json.dumps(self.original),encoding="utf-8")

    def test_prepare_dryrun_and_pending_do_not_change_live_config(self):
        before=self.config.read_bytes()
        self.assertFalse(manage_hooks.manage("prepare",self.config,self.entries)["written"])
        out=self.root/"hooks.pending.json"
        r=manage_hooks.manage("prepare",self.config,self.entries,apply=True,output=out)
        self.assertEqual(r["status"],"PREPARED_INACTIVE")
        self.assertEqual(self.config.read_bytes(),before)
        self.assertTrue(out.exists())
        with self.assertRaises(ValueError):
            manage_hooks.manage("prepare",self.config,self.entries,apply=True,output=self.config)

    def test_registration_is_idempotent_and_removal_preserves_other_hooks(self):
        result=manage_hooks.manage("register",self.config,self.entries,apply=True)
        self.assertEqual(result["status"],"REGISTERED_REQUIRES_REVIEW")
        self.assertFalse(result["trusted_by_this_program"])
        self.assertEqual(manage_hooks.manage("register",self.config,self.entries,apply=True)["status"],"ALREADY_REGISTERED")
        current=json.loads(self.config.read_text(encoding="utf-8"))
        current["extra"]="preserve new synthetic metadata"
        self.config.write_text(json.dumps(current),encoding="utf-8")
        result=manage_hooks.manage("remove",self.config,[],apply=True)
        self.assertEqual(result["status"],"REMOVED")
        self.assertEqual(json.loads(self.config.read_text(encoding="utf-8")),{**self.original,"extra":"preserve new synthetic metadata"})
        self.assertEqual(manage_hooks.manage("remove",self.config,[],apply=True)["status"],"NOT_REGISTERED")
        self.assertGreaterEqual(len(list(self.root.glob("*.copilot-backup-*"))),2)

    def test_removal_refuses_changed_owned_definition(self):
        manage_hooks.manage("register",self.config,self.entries,apply=True)
        value=json.loads(self.config.read_text(encoding="utf-8"))
        value["hooks"]["SessionStart"][0]["hooks"][0]["timeout"]=7
        self.config.write_text(json.dumps(value),encoding="utf-8")
        before=self.config.read_bytes()
        with self.assertRaises(ValueError):
            manage_hooks.manage("remove",self.config,[],apply=True)
        self.assertEqual(self.config.read_bytes(),before)

    def test_receipt_write_failure_rolls_back_existing_config(self):
        before=self.config.read_bytes()
        real_write=manage_hooks.atomic_write
        def failing_write(path,data):
            if str(path).endswith(".copilot-consult-receipt.json"):
                raise OSError("Synthetic receipt write failure")
            real_write(path,data)
        with patch.object(manage_hooks,"atomic_write",side_effect=failing_write):
            with self.assertRaises(OSError):
                manage_hooks.manage("register",self.config,self.entries,apply=True)
        self.assertEqual(self.config.read_bytes(),before)

    def test_receipt_write_failure_removes_only_new_config(self):
        self.config.unlink()
        real_write=manage_hooks.atomic_write
        def failing_write(path,data):
            if str(path).endswith(".copilot-consult-receipt.json"):
                raise OSError("Synthetic receipt write failure")
            real_write(path,data)
        with patch.object(manage_hooks,"atomic_write",side_effect=failing_write):
            with self.assertRaises(OSError):
                manage_hooks.manage("register",self.config,self.entries,apply=True)
        self.assertFalse(self.config.exists())

    def test_duplicate_json_keys_rejected(self):
        self.config.write_text('{"hooks":{},"hooks":{}}',encoding="utf-8")
        with self.assertRaises(ValueError):
            manage_hooks.read_config(self.config)


if __name__=="__main__":
    unittest.main()
