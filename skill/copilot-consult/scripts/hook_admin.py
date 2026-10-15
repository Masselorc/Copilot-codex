"""Bind local hook task metadata; this command cannot grant Copilot authorization."""
import argparse
import json
import os
from pathlib import Path
from hook_dispatch import Store


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--state-dir",type=Path,default=Path(os.environ.get("LOCALAPPDATA",str(Path.home())))/"CodexCopilot"/"hooks")
    p.add_argument("--session",required=True)
    p.add_argument("--cwd",required=True,type=Path)
    p.add_argument("--project",required=True,type=Path)
    p.add_argument("--demand",required=True)
    p.add_argument("--task",required=True)
    p.add_argument("--stage",required=True)
    p.add_argument("--extensive",action="store_true")
    p.add_argument("--expensive-command-file",action="append",type=Path,default=[])
    args=p.parse_args(argv)
    key=Store(args.state_dir).bind(args.session,str(args.cwd.resolve(strict=True)),str(args.project.resolve(strict=True)),args.demand,args.task,args.stage,extensive=args.extensive,expensive=[f.read_text(encoding="utf-8") for f in args.expensive_command_file])
    print(json.dumps({"status":"BOUND","task_key":key,"authorization_granted":False}))


if __name__=="__main__":
    main()
