"""Daemon: fast deterministic monitors every `monitor_interval`, AI health check every `interval_seconds`."""
from __future__ import annotations

import json
import logging
import re
import signal
import threading
import time
from pathlib import Path

from .agent import Agent
from .alerts import Alerter
from .config import Config
from .monitors import MonitorRunner

log = logging.getLogger("idraulik")

CHECK_PROMPT = ("Run a periodic health and security check of this server: resources, failed services, "
                "recent error logs, disk, network exposure (ports, wifi, bluetooth, LAN) and file changes. "
                "Start by calling `recent_alerts` and triage them: separate real threats from noise. "
                "Apply your skills. Be concise.")


def run_check(agent: Agent, cfg: Config, alerter: Alerter) -> str:
    try:
        report = agent.run(CHECK_PROMPT)
    except Exception as e:
        alerter.alert("WARN", "ai", f"AI check failed: {type(e).__name__}: {e}")
        return f"AI check failed: {e}"
    d = Path(cfg.state_dir)
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "reports.jsonl", "a") as f:
        f.write(json.dumps({"ts": int(time.time()), "report": report}) + "\n")
    m = re.search(r"STATUS:?\s*\**\s*(OK|WARN|CRIT)", report, re.I)
    status = m.group(1).upper() if m else "OK"
    if status != "OK":
        alerter.alert(status, "ai", report.replace("\n", " ")[:600])
    log.info("report:\n%s", report)
    return report


def serve(cfg: Config) -> None:
    stop = threading.Event()
    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, lambda *_: stop.set())
    alerter = Alerter(cfg.alerts_file, cfg.state_dir)
    runner = MonitorRunner(cfg, alerter)
    agent = Agent(cfg, alerter=alerter)
    log.info("idraulik started: monitors=%s every %ss, AI check every %ss, alerts -> %s",
             cfg.monitors_enabled, cfg.monitor_interval, cfg.interval_seconds, alerter.path)
    next_ai = 0.0
    while not stop.is_set():
        try:
            runner.run_once()
        except Exception:
            log.exception("monitor cycle failed")
        if time.monotonic() >= next_ai:
            run_check(agent, cfg, alerter)
            next_ai = time.monotonic() + cfg.interval_seconds
        stop.wait(cfg.monitor_interval)
    log.info("idraulik stopped")
