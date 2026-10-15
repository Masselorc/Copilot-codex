"""Prepare/register/remove optional local hooks. Never trust hooks automatically."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid
import install_skill

RULES=(("SessionStart","session","startup|resume|compact|fork|clear"),("PostToolUse","failures","*"),("PreToolUse","authorize","Bash"),("PreToolUse","cost","Bash"),("Stop","stop",None))


def encoded(value):
    return (json.dumps(value,ensure_ascii=True,indent=2)+"\n").encode("utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_config(path):
    install_skill._reject_link_components(path,"Hook config")
    if not path.exists():
        return {},None
    raw=path.read_bytes()
    if len(raw)>1024*1024:
        raise ValueError("Hook config exceeds the local 1 MiB limit")
    def unique(pairs):
        obj={}
        for key,value in pairs:
            if key in obj:
                raise ValueError("Duplicate JSON key in hook config")
            obj[key]=value
        return obj
    obj=json.loads(raw,object_pairs_hook=unique)
    if not isinstance(obj,dict) or not isinstance(obj.get("hooks",{}),dict):
        raise ValueError("Hook config must be an object with a hooks object")
    for groups in obj.get("hooks",{}).values():
        if not isinstance(groups,list):
            raise ValueError("Hook event definitions must be lists")
    return obj,raw


def definitions(skill_dir,state_dir,bridge_state_dir):
    handler=Path(os.path.abspath(Path(skill_dir)/"scripts"/"hook_dispatch.py"))
    install_skill._reject_link_components(handler,"Hook handler")
    if not handler.is_file():
        raise ValueError("Installed hook handler is missing")
    # Bare python works in both common Windows command shells. No shell code,
    # variable expansion, Node or PowerShell script is embedded.
    args=[str(handler),"--state-dir",str(Path(state_dir).resolve()),"--bridge-state-dir",str(Path(bridge_state_dir).resolve())]
    if any(any(char in value for char in '\"\n\r`$%&|<>') for value in args):
        raise ValueError("Hook paths contain unsupported shell metacharacters")
    entries=[]
    for event,rule,matcher in RULES:
        command="python -B "+" ".join('"'+x+'"' for x in args)+" --rule "+rule
        group={"hooks":[{"type":"command","command":command,"commandWindows":command,"timeout":2,"statusMessage":"Copilot consultation guard: "+rule,"additionalContextLimit":300}]}
        if matcher is not None:
            group["matcher"]=matcher
        if event=="Stop":
            group["hooks"][0].pop("additionalContextLimit",None)
        entries.append({"event":event,"group":group})
    return entries


def merge(config,entries):
    result=copy.deepcopy(config)
    hooks=result.setdefault("hooks",{})
    for entry in entries:
        groups=hooks.setdefault(entry["event"],[])
        if entry["group"] not in groups:
            groups.append(entry["group"])
    return result


def atomic_write(path,data):
    path=Path(path)
    install_skill._reject_link_components(path,"Output")
    path.parent.mkdir(parents=True,exist_ok=True)
    install_skill._reject_link_components(path.parent,"Output parent")
    descriptor,temporary=tempfile.mkstemp(prefix=".copilot-hooks-",suffix=".tmp",dir=path.parent)
    try:
        with os.fdopen(descriptor,"wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def manage(action,config_path,entries,*,apply=False,output=None):
    config_path=Path(os.path.abspath(config_path))
    receipt_path=config_path.with_name(config_path.name+".copilot-consult-receipt.json")
    install_skill._reject_link_components(receipt_path,"Hook receipt")
    config,original=read_config(config_path)
    receipt=json.loads(receipt_path.read_bytes()) if receipt_path.exists() else None
    if action=="remove":
        if not receipt or receipt.get("status")=="removed":
            return {"status":"NOT_REGISTERED","written":False}
        if receipt.get("config")!=str(config_path):
            raise ValueError("Hook receipt belongs to a different config")
        entries=receipt["entries"]
        result=copy.deepcopy(config)
        for entry in entries:
            groups=result.get("hooks",{}).get(entry["event"],[])
            if entry["group"] not in groups:
                raise ValueError("Registered definition changed or disappeared; preserve it and review removal manually")
            groups.remove(entry["group"])
            if not groups:
                del result["hooks"][entry["event"]]
        if not result.get("hooks"):
            result.pop("hooks",None)
    else:
        result=merge(config,entries)
        if action=="register" and receipt and receipt.get("status")=="registered":
            if receipt["entries"]==entries and all(e["group"] in config.get("hooks",{}).get(e["event"],[]) for e in entries):
                return {"status":"ALREADY_REGISTERED","written":False,"trusted_by_this_program":False}
            raise ValueError("Remove the previous registered definitions before replacing them")
    desired=encoded(result)
    summary={"status":"DRY_RUN","action":action,"definition_sha256":sha(encoded(entries)),"handlers":len(entries),"written":False,"trusted_by_this_program":False}
    if not apply:
        return summary
    if action=="prepare":
        if output is None or Path(output).resolve() in {config_path,receipt_path}:
            raise ValueError("Prepare needs a separate pending output, never the live config")
        if Path(output).exists():
            raise ValueError("Pending output exists; refusing overwrite")
        atomic_write(Path(output),desired)
        return {**summary,"status":"PREPARED_INACTIVE","written":True}
    if (config_path.read_bytes() if config_path.exists() else None)!=original:
        raise ValueError("Config changed during review; refusing overwrite")
    backup=None
    if original is not None:
        backup=config_path.with_name(config_path.name+".copilot-backup-"+uuid.uuid4().hex)
        with backup.open("xb") as stream:
            stream.write(original)
    new_receipt={"config":str(config_path),"entries":entries,"status":"removed" if action=="remove" else "registered","definition_sha256":sha(encoded(entries)),"trust_granted":False}
    atomic_write(config_path,desired)
    try:
        atomic_write(receipt_path,encoded(new_receipt))
    except Exception:
        if config_path.read_bytes()==desired:
            if original is None:
                config_path.unlink()
            else:
                atomic_write(config_path,original)
        raise
    return {**summary,"status":"REMOVED" if action=="remove" else "REGISTERED_REQUIRES_REVIEW","written":True,"backup_created":backup is not None}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("action",choices=["prepare","register","remove"])
    p.add_argument("--skill-dir",type=Path,default=Path(__file__).resolve().parent/"skill"/"copilot-consult")
    p.add_argument("--config",type=Path,default=Path.home()/".codex"/"hooks.json")
    base=Path(os.environ.get("LOCALAPPDATA",str(Path.home())))/"CodexCopilot"
    p.add_argument("--state-dir",type=Path,default=base/"hooks")
    p.add_argument("--bridge-state-dir",type=Path,default=base)
    p.add_argument("--output",type=Path)
    p.add_argument("--apply",action="store_true")
    args=p.parse_args(argv)
    try:
        entries=definitions(args.skill_dir,args.state_dir,args.bridge_state_dir) if args.action!="remove" else []
        result=manage(args.action,args.config,entries,apply=args.apply,output=args.output)
        print(json.dumps(result,ensure_ascii=True,indent=2))
        return 0
    except (ValueError,OSError,install_skill.InstallError) as exc:
        print(json.dumps({"status":"BLOCKED","reason":str(exc)},ensure_ascii=True))
        return 2


if __name__=="__main__":
    raise SystemExit(main())
