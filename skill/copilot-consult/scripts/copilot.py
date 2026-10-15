"""Consult the installed Windows Copilot app using Python's standard library."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import sqlite3
from protocol import CopilotProtocol, ProtocolError
from authorization import scope_hash, check_grant


def project_key(path):
    return os.path.normcase(str(Path(path).resolve(strict=True)))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir",type=Path,default=Path(os.environ.get("LOCALAPPDATA",str(Path.home())))/"CodexCopilot")
    sub=parser.add_subparsers(dest="command",required=True)
    sub.add_parser("doctor",help="Read-only app/model check")
    for command in ["authorize","revoke","query","resume","recover","status"]:
        p=sub.add_parser(command)
        p.add_argument("--project",required=True,type=Path)
        p.add_argument("--demand",required=True)
        p.add_argument("--task",required=True,help="Current task ID; authorization does not carry over to other tasks")
        if command=="authorize":
            p.add_argument("--evidence",required=True,help="Reference to actual human authorization")
            p.add_argument("--scope",required=True)
            p.add_argument("--synthetic-only",action="store_true")
        elif command=="revoke":
            p.add_argument("--reason",required=True)
        else:
            p.add_argument("--request",required=True,help="Stable consultation ID; reuse after uncertainty")
        if command in ("query","resume"):
            p.add_argument("--scope-sha256",required=True,help="SHA256 returned when recording the approved scope")
            p.add_argument("--prompt-file",required=True,type=Path)
            p.add_argument("--attach",action="append",type=Path,default=[])
            p.add_argument("--synthetic",action="store_true")
        if command in ("query","resume","recover"):
            p.add_argument("--wait",type=float,default=12,help="Read-only response wait, capped at 55 seconds")
    args=parser.parse_args(argv)
    try:
        if args.command=="doctor":
            from app import CopilotApp
            app=CopilotApp(wait_seconds=0)
            data={"status":"UI_ACCESSIBLE","application":"Microsoft 365 Copilot",
                  "window_class":app.w.prop("class"),"selected_model_label":app.exact(id="gptModeSwitcher").prop("name"),
                  "conversation_id":app.conversation_id(),"transport":"native Windows UI Automation + SendInput",
                  "node_required":False,"powershell_required":False,"send_performed":False}
        else:
            project=project_key(args.project)
            if args.command in ("query","resume"):
                check_grant(args.state_dir,project,args.demand,args.task,args.scope_sha256,synthetic=args.synthetic)
            adapter=None
            if args.command in ("query","resume","recover"):
                from app import CopilotApp
                adapter=CopilotApp(wait_seconds=args.wait)
            protocol=CopilotProtocol(args.state_dir,adapter)
            if args.command=="authorize":
                protocol.grant(project,args.demand,args.task,evidence=args.evidence,scope=args.scope,synthetic_only=args.synthetic_only)
                data={"status":"AUTHORIZED","scope":args.scope,"scope_sha256":scope_hash(args.scope),"synthetic_only":args.synthetic_only}
            elif args.command=="revoke":
                protocol.revoke(project,args.demand,args.task,reason=args.reason)
                data={"status":"REVOKED"}
            elif args.command=="query":
                data=protocol.query(project,args.demand,args.task,args.request,args.prompt_file.read_text(encoding="utf-8"),args.attach,synthetic=args.synthetic,expected_scope_sha256=args.scope_sha256)
            elif args.command=="resume":
                data=protocol.resume_preparing(project,args.demand,args.task,args.request,args.prompt_file.read_text(encoding="utf-8"),args.attach,synthetic=args.synthetic,expected_scope_sha256=args.scope_sha256)
            elif args.command=="recover":
                data=protocol.reconcile(project,args.demand,args.task,args.request)
            else:
                data=protocol.request_record(project,args.demand,args.task,args.request)
            if args.command in ("query","resume","recover"):
                data["conversation_id"]=protocol._conversation_id(project,args.demand)
        print(json.dumps(data,ensure_ascii=True,indent=2))
        return 0 if data and data.get("status") not in ("UNKNOWN","PREPARING","DISPATCHING") else 3
    except (ProtocolError,OSError,RuntimeError,ValueError,ImportError,sqlite3.Error) as exc:
        print(json.dumps({"status":"BLOCKED","reason":str(exc),
                          "next_step":"Inspect UI and ledger. After uncertain input/send, recover only; do not repeat with a new request ID."},ensure_ascii=True,indent=2))
        return 2


if __name__=="__main__":
    raise SystemExit(main())
