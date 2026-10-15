"""Five bounded local hook rules. No UI, network, transcript reads or subprocesses."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import sys
import argparse
from authorization import check_grant

MAX_INPUT=128*1024
RULE_EVENTS={"session":"SessionStart","failures":"PostToolUse","authorize":"PreToolUse","cost":"PreToolUse","stop":"Stop"}
SELF=re.compile(r"(?:copilot|hook_dispatch|hook_admin|manage_hooks|install_skill|authorization)\.py",re.I)


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical(value,base=None):
    path=Path(value)
    if not path.is_absolute():
        path=Path(base or Path.cwd())/path
    return os.path.normcase(str(path.resolve()))


def command(payload):
    value=payload.get("tool_input",{})
    if not isinstance(value,dict):
        return ""
    result=value.get("command",value.get("cmd",""))
    return result if isinstance(result,str) else ""


def command_hash(value):
    return digest(" ".join(value.split()))


def context(event,message):
    return {"hookSpecificOutput":{"hookEventName":event,"additionalContext":message}}


def deny(message):
    return {"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":message}}


class Store:
    def __init__(self,directory):
        self.directory=Path(directory)
        self.directory.mkdir(parents=True,exist_ok=True)
        self.path=self.directory/"hook-state.sqlite3"
        with self.connection() as c:
            c.executescript("CREATE TABLE IF NOT EXISTS tasks(k TEXT PRIMARY KEY,data TEXT NOT NULL); CREATE TABLE IF NOT EXISTS bindings(k TEXT PRIMARY KEY,task_key TEXT NOT NULL); CREATE TABLE IF NOT EXISTS starts(k TEXT PRIMARY KEY);")

    @contextmanager
    def connection(self):
        c=sqlite3.connect(self.path,timeout=.1)
        try:
            c.execute("PRAGMA busy_timeout=100")
            with c:
                yield c
        finally:
            c.close()

    @staticmethod
    def binding_key(session,cwd):
        return digest(json.dumps([session,canonical(cwd)]))

    def bind(self,session,cwd,project,demand,task,stage,*,extensive=False,expensive=()):
        if not all(isinstance(v,str) and v.strip() and len(v)<=256 for v in (session,demand,task,stage)):
            raise ValueError("Nonempty bounded session/task/demand/stage identifiers required")
        if len(expensive)>16:
            raise ValueError("At most 16 expensive commands per task")
        project=canonical(project)
        key=digest(json.dumps([project,demand,task]))
        with self.connection() as c:
            row=c.execute("SELECT data FROM tasks WHERE k=?",(key,)).fetchone()
            data=json.loads(row[0]) if row else {"project":project,"demand":demand,"task":task,"seen":[],"failures":{},"notices":[],"calls":0,"stop_issued":False}
            data.update(stage=stage,extensive=bool(extensive),expensive=[command_hash(x) for x in expensive])
            c.execute("INSERT OR REPLACE INTO tasks VALUES(?,?)",(key,json.dumps(data)))
            c.execute("INSERT OR REPLACE INTO bindings VALUES(?,?)",(self.binding_key(session,cwd),key))
        return key

    def handle(self,rule,payload,bridge_state):
        event=RULE_EVENTS[rule]
        if payload.get("hook_event_name")!=event:
            return {}
        session=payload.get("session_id")
        cwd=payload.get("cwd")
        if not isinstance(session,str) or not session or len(session)>256 or not isinstance(cwd,str) or not Path(cwd).is_absolute():
            return deny("Cannot identify the task for Copilot authorization") if rule=="authorize" and "copilot.py" in command(payload).lower() else {}
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            binding=c.execute("SELECT task_key FROM bindings WHERE k=?",(self.binding_key(session,cwd),)).fetchone()
            key=binding[0] if binding else None
            row=c.execute("SELECT data FROM tasks WHERE k=?",(key,)).fetchone() if key else None
            data=json.loads(row[0]) if row else None
            if rule=="authorize":
                return authorize(payload,data,bridge_state)
            if rule=="session" and data is None:
                # At most one startup and one resume-family notice per unbound session.
                family="startup" if payload.get("source")=="startup" else "resume"
                token=digest(self.binding_key(session,cwd)+family)
                if c.execute("SELECT 1 FROM starts WHERE k=?",(token,)).fetchone():
                    return {}
                c.execute("INSERT INTO starts VALUES(?)",(token,))
                return context(event,"copilot-consult is available (19 criteria). Bind the current task/stage before hook tracking; confirm human authorization before any Copilot transmission. On resume, recover uncertain requests instead of resending.")
            if data is None:
                return {}
            result=evaluate(rule,payload,data)
            c.execute("UPDATE tasks SET data=? WHERE k=?",(json.dumps(data),key))
            return result


def notice(data,event,key,message):
    if key in data["notices"] or len(data["notices"])>=5:
        return {}
    data["notices"].append(key)
    return context(event,message)


def failure_code(payload):
    response=payload.get("tool_response")
    if len(json.dumps(response))>MAX_INPUT:
        return None
    text=json.dumps(response,ensure_ascii=False).lower()
    if any(word in text for word in ("permission denied","access denied","acesso negado","permission_denied","unacceptable risk","cancelled","canceled","interrupted","approval denied")):
        return None
    if isinstance(response,str):
        try:
            decoded=json.loads(response)
            if isinstance(decoded,dict):
                response=decoded
        except json.JSONDecodeError:
            pass
    if isinstance(response,dict):
        if str(response.get("status","")).lower() in {"cancelled","canceled","denied","rejected","interrupted"}:
            return None
        code=response.get("exit_code")
        if payload.get("tool_name") in {"Bash","exec_command"} and isinstance(code,int) and not isinstance(code,bool):
            return "exit:"+str(code) if code not in {0,130,143,-1073741510} else None
        if str(payload.get("tool_name","")).startswith("mcp__") and response.get("isError") is True:
            return "mcp:isError"
    if isinstance(response,str) and payload.get("tool_name") in {"Bash","exec_command"}:
        found=re.search(r"(?im)^\s*(?:Process exited with code|Exit code:|Process exit code:)\s*(-?\d+)\s*$",response)
        if found:
            code=int(found.group(1))
            return "exit:"+str(code) if code not in {0,130,143,-1073741510} else None
    return None


def evaluate(rule,payload,data):
    event=RULE_EVENTS[rule]
    cmd=command(payload)
    if SELF.search(cmd):
        return {}
    if rule=="session":
        family="startup" if payload.get("source")=="startup" else "resume"
        return notice(data,event,"session:"+family,"Evaluate copilot-consult's 19 criteria when useful. Authorization is per task. After context loss, verify state and recover uncertain requests; never duplicate a send.")
    if rule=="failures":
        identifier=payload.get("tool_use_id")
        if not isinstance(identifier,str) or not identifier or len(identifier)>256:
            return {}
        token=digest(identifier)
        if token in data["seen"] or len(data["seen"])>=512:
            return {}
        data["seen"].append(token)
        data["calls"]+=1
        code=failure_code(payload)
        if code is None:
            return {}
        signature=digest(json.dumps([data["stage"],payload.get("tool_name"),command_hash(cmd),code]))
        if signature not in data["failures"] and len(data["failures"])>=32:
            return {}
        data["failures"][signature]=min(2,data["failures"].get(signature,0)+1)
        if data["failures"][signature]==2:
            return notice(data,event,"failures","Two identifiable similar failures occurred in this stage. Reassess the cause and consider copilot-consult if useful and authorized. Cancellation or denied access is not a reason to retry or bypass a restriction.")
    elif rule=="cost":
        if cmd and command_hash(cmd) in data["expensive"]:
            return notice(data,event,"cost","This exact command was previously marked costly or slow for this task. Evaluate plan, estimate and a small validation before proceeding; consider copilot-consult only within human-approved scope.")
    elif rule=="stop":
        if payload.get("stop_hook_active") is not False or not payload.get("turn_id") or data["stop_issued"]:
            return {}
        if not data["extensive"] and data["calls"]<20:
            return {}
        data["stop_issued"]=True
        # One continuation at most for the entire bound task, including resumes.
        return {"decision":"block","reason":"Perform one final local review of requirements, edge cases, evidence and practical installation/use for this extensive task. Do not repeat this review hook or send content to Copilot unless the task's human authorization already covers it. Then finish."}
    return {}


def copilot_arguments(cmd):
    """Recognize only a literal, single Copilot CLI query/resume; never evaluate shell."""
    if not re.search(r"copilot\.py",cmd,re.I):
        return None
    tokens=[token[1:-1] if len(token)>1 and token[0]==token[-1] and token[0] in "\"'" else token for token in shlex.split(cmd,posix=False)]
    scripts=[i for i,t in enumerate(tokens) if Path(t).name.lower()=="copilot.py"]
    if len(scripts)!=1:
        raise ValueError("Use a literal Copilot CLI invocation so authorization can be checked")
    following=tokens[scripts[0]+1:]
    actions=[t for t in following if t in {"query","resume"}]
    if not actions:
        return None
    if any(symbol in cmd for symbol in (";","|","&","\n","\r","`","$")) or len(actions)!=1:
        raise ValueError("Compound or dynamic Copilot command cannot be authorized by this hook")
    values={"--synthetic":False}
    i=0
    while i<len(following):
        token=following[i]
        if token in {"query","resume"}:
            i+=1
            continue
        if token=="--synthetic":
            values[token]=True
            i+=1
            continue
        if token not in {"--project","--demand","--task","--request","--prompt-file","--scope-sha256","--state-dir","--attach","--wait"} or i+1>=len(following):
            raise ValueError("Unsupported or incomplete Copilot CLI arguments")
        if token in values and token!="--attach":
            raise ValueError("Duplicate Copilot identity argument")
        values[token]=following[i+1]
        i+=2
    return values


def authorize(payload,data,bridge_state):
    try:
        values=copilot_arguments(command(payload))
        if values is None:
            return {}
        if data is None:
            raise ValueError("Bind this Codex session to the current task before Copilot sends")
        inputs=payload.get("tool_input",{})
        execution_cwd=inputs.get("cwd",inputs.get("workdir",payload["cwd"]))
        project=canonical(values["--project"],execution_cwd)
        if (project,values["--demand"],values["--task"])!=(data["project"],data["demand"],data["task"]):
            raise ValueError("Copilot command belongs to a different bound task or scope")
        if "--state-dir" in values and canonical(values["--state-dir"],execution_cwd)!=canonical(bridge_state):
            raise ValueError("Copilot command uses a different authorization state directory")
        check_grant(bridge_state,project,values["--demand"],values["--task"],values["--scope-sha256"],synthetic=values["--synthetic"])
        return {}  # Never return allow/ask, autoapprove or rewrite a tool call.
    except (ValueError,KeyError,OSError,sqlite3.Error):
        return deny("Copilot authorization could not be verified for this task/scope, or was revoked. Stop this send; obtain explicit human authorization if absent. Do not bypass the guard.")


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rule",required=True,choices=RULE_EVENTS)
    p.add_argument("--state-dir",required=True,type=Path)
    p.add_argument("--bridge-state-dir",required=True,type=Path)
    args=p.parse_args(argv)
    result={}
    try:
        raw=sys.stdin.buffer.read(MAX_INPUT+1)
        if len(raw)>MAX_INPUT:
            raise ValueError("Hook payload exceeds local bound")
        payload=json.loads(raw)
        if not isinstance(payload,dict):
            raise ValueError("Hook input must be an object")
        result=Store(args.state_dir).handle(args.rule,payload,args.bridge_state_dir)
    except (ValueError,OSError,sqlite3.Error,KeyError,TypeError):
        result=deny("Copilot authorization hook unavailable; stop and inspect locally") if args.rule=="authorize" else {}
    print(json.dumps(result,ensure_ascii=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
