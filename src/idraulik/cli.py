"""idraulik CLI."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import __version__
from .agent import Agent
from .alerts import LEVELS, Alerter
from .config import load_config, load_env_file
from .daemon import run_check, serve
from .monitors import MonitorRunner
from .skills import load_skills
from .tools import build_tools


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="idraulik", description="AI sysadmin daemon")
    ap.add_argument("-c", "--config", help="path to idraulik.toml")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("daemon", help="run the daemon in foreground (used by systemd)")
    sub.add_parser("check", help="run one AI health check now")
    a = sub.add_parser("ask", help="ask the agent something")
    a.add_argument("question", nargs="+")
    sub.add_parser("tools", help="list tools")
    sub.add_parser("skills", help="list skills")
    sub.add_parser("providers", help="list configured AI providers")
    sub.add_parser("reports", help="show last AI reports").add_argument("-n", type=int, default=3)
    al = sub.add_parser("alerts", help="show recent alerts")
    al.add_argument("-n", type=int, default=20)
    al.add_argument("--level", choices=LEVELS, default="INFO")
    mo = sub.add_parser("monitor", help="run all monitors once (first run creates the baseline)")
    mo.add_argument("--reset", action="store_true", help="discard baseline and start over")
    ap_ = sub.add_parser("approvals", help="manage pending write actions")
    ap_.add_argument("action", choices=["list", "approve", "reject"])
    ap_.add_argument("id", nargs="?")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    load_env_file()
    cfg = load_config(args.config)

    if args.cmd == "daemon":
        serve(cfg)
    elif args.cmd == "check":
        print(run_check(Agent(cfg, alerter=Alerter(cfg.alerts_file, cfg.state_dir)), cfg,
                        Alerter(cfg.alerts_file, cfg.state_dir)))
    elif args.cmd == "ask":
        print(Agent(cfg, alerter=Alerter(cfg.alerts_file, cfg.state_dir)).run(" ".join(args.question)))
    elif args.cmd == "tools":
        for t in build_tools().values():
            print(f"{t.name:18} [{t.risk}] {t.description}")
    elif args.cmd == "skills":
        for s in load_skills(cfg.skills_dir):
            print(f"{s.name:20} {s.description}")
    elif args.cmd == "providers":
        for n in [cfg.agent_provider, *cfg.fallback_providers]:
            p = cfg.providers[n]
            role = "primary" if n == cfg.agent_provider else "fallback"
            print(f"{n:12} {role:9} {p.type:10} {p.model}  {p.base_url}")
    elif args.cmd == "reports":
        p = Path(cfg.state_dir) / "reports.jsonl"
        for l in (p.read_text().splitlines()[-args.n:] if p.exists() else []):
            r = json.loads(l)
            print(f"--- {r['ts']}\n{r['report']}\n")
    elif args.cmd == "alerts":
        for l in Alerter(cfg.alerts_file, cfg.state_dir).recent(args.n, args.level):
            print(l)
    elif args.cmd == "monitor":
        runner = MonitorRunner(cfg, Alerter(cfg.alerts_file, cfg.state_dir))
        if args.reset:
            runner.reset()
        print(f"{runner.run_once()} alert(s) raised; see: idraulik alerts")
    elif args.cmd == "approvals":
        agent = Agent(cfg, providers=[])
        if args.action == "list":
            for i in agent.approvals.list("pending"):
                print(f"{i['id']}  {i['tool']} {json.dumps(i['arguments'])}  ({i['reason']})")
        elif not args.id:
            print("id required", file=sys.stderr)
            return 2
        elif args.action == "approve":
            print(agent.run_approved(args.id))
        else:
            agent.approvals.update(args.id, status="rejected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
