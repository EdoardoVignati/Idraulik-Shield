"""System tools exposed to the agents. risk='read' runs freely; risk='write' goes through the approval policy."""
from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Callable

MAX_OUTPUT = 6000
NAME_RE = re.compile(r"^[A-Za-z0-9@_.:-]{1,100}$")
PRIORITIES = ("emerg", "alert", "crit", "err", "warning", "notice", "info", "debug")

DEFAULT_DENYLIST = [
    r"\brm\s+(-\S+\s+)*-\S*r\S*\s+/(\s|$)", r"\bmkfs", r"\bdd\s+.*of=/dev/",
    r":\(\)\s*\{", r"\bshutdown\b", r"\breboot\b", r"\bpoweroff\b", r"\bhalt\b",
    r"chmod\s+-R\s+\d+\s+/(\s|$)", r"\bpasswd\b", r">\s*/dev/sd",
]


def _run(cmd: list[str], timeout: int = 30) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return f"error: command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return f"error: timeout after {timeout}s"
    out = (p.stdout + (("\n[stderr] " + p.stderr) if p.stderr.strip() else "")).strip()
    if len(out) > MAX_OUTPUT:
        out = out[:MAX_OUTPUT] + "\n...[truncated]"
    return f"[exit {p.returncode}]\n{out}" if p.returncode else out


@dataclass
class Tool:
    name: str
    description: str
    fn: Callable[..., str]
    params: dict = field(default_factory=dict)  # name -> JSON schema
    optional: tuple = ()
    risk: str = "read"

    def schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": self.params,
                           "required": [k for k in self.params if k not in self.optional]}}}


def system_overview() -> str:
    parts = []
    for f in ("/proc/uptime", "/proc/loadavg"):
        try:
            with open(f) as fh:
                parts.append(f"{f}: {fh.read().strip()}")
        except OSError:
            pass
    try:
        with open("/proc/meminfo") as fh:
            mem = {l.split(":")[0]: l.split(":")[1].strip() for l in fh}
        parts.append("mem: " + ", ".join(f"{k}={mem[k]}" for k in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree") if k in mem))
    except OSError:
        pass
    du = shutil.disk_usage("/")
    parts.append(f"disk /: total={du.total >> 30}GiB used={du.used >> 30}GiB free={du.free >> 30}GiB")
    parts.append(_run(["uname", "-a"]))
    return "\n".join(parts)


def disk_usage(path: str = "/") -> str:
    return _run(["df", "-hT", path]) + "\n" + _run(["df", "-hi", path])


def top_processes(count: int = 10, sort: str = "cpu") -> str:
    key = "-%mem" if sort == "mem" else "-%cpu"
    count = max(1, min(int(count), 30))
    lines = _run(["ps", "-eo", "pid,user,%cpu,%mem,etime,comm", f"--sort={key}"]).splitlines()
    return "\n".join(lines[:count + 1])


def failed_services() -> str:
    return _run(["systemctl", "--failed", "--no-pager", "--plain"])


def service_status(name: str) -> str:
    if not NAME_RE.match(name):
        return "error: invalid service name"
    return _run(["systemctl", "status", name, "--no-pager", "-n", "15"])


def read_journal(unit: str = "", lines: int = 50, priority: str = "warning") -> str:
    cmd = ["journalctl", "--no-pager", "-n", str(max(1, min(int(lines), 200))),
           "-p", priority if priority in PRIORITIES else "warning"]
    if unit:
        if not NAME_RE.match(unit):
            return "error: invalid unit name"
        cmd += ["-u", unit]
    return _run(cmd)


def listening_ports() -> str:
    return _run(["ss", "-tulpn"])


def recent_logins() -> str:
    return _run(["last", "-n", "15", "-a"]) + "\n" + _run(["lastb", "-n", "10"])


def restart_service(name: str) -> str:
    if not NAME_RE.match(name):
        return "error: invalid service name"
    return _run(["systemctl", "restart", name], timeout=60) or f"{name} restarted"


def run_command(command: str, denylist: list[str] | None = None) -> str:
    for pat in DEFAULT_DENYLIST + list(denylist or []):
        if re.search(pat, command):
            return f"error: command blocked by denylist ({pat})"
    try:
        argv = shlex.split(command)
    except ValueError as e:
        return f"error: {e}"
    if not argv:
        return "error: empty command"
    return _run(argv, timeout=60)


def network_connections() -> str:
    return _run(["ss", "-H", "-tunap", "state", "established"])


def wifi_networks() -> str:
    return _run(["nmcli", "-f", "IN-USE,SSID,BSSID,CHAN,SIGNAL,SECURITY", "dev", "wifi", "list"])


def bluetooth_devices() -> str:
    return _run(["bluetoothctl", "devices"]) + "\n[paired]\n" + _run(["bluetoothctl", "devices", "Paired"])


def lan_devices() -> str:
    return _run(["ip", "neigh"])


def build_tools(denylist: list[str] | None = None, alerter=None) -> dict[str, Tool]:
    S, I = {"type": "string"}, {"type": "integer"}

    def recent_alerts(count: int = 30, min_level: str = "INFO") -> str:
        if alerter is None:
            return "alert log not available"
        return "\n".join(alerter.recent(max(1, min(int(count), 100)), min_level)) or "no alerts"

    tools = [
        Tool("recent_alerts", "Recent entries of the alert log written by the monitors.", recent_alerts,
             {"count": I, "min_level": {**S, "enum": ["INFO", "WARN", "CRIT"]}}, ("count", "min_level")),
        Tool("network_connections", "Established TCP/UDP connections with processes.", network_connections),
        Tool("wifi_networks", "Visible wifi access points and the one in use.", wifi_networks),
        Tool("bluetooth_devices", "Known and paired bluetooth devices.", bluetooth_devices),
        Tool("lan_devices", "Neighbours (IP/MAC) seen on the local network.", lan_devices),
        Tool("system_overview", "Uptime, load, memory, root disk and kernel info.", system_overview),
        Tool("disk_usage", "Disk and inode usage for a path.", disk_usage, {"path": S}, ("path",)),
        Tool("top_processes", "Top processes by cpu or mem.", top_processes,
             {"count": I, "sort": {**S, "enum": ["cpu", "mem"]}}, ("count", "sort")),
        Tool("failed_services", "List failed systemd units.", failed_services),
        Tool("service_status", "systemctl status for a unit.", service_status, {"name": S}),
        Tool("read_journal", "Read journalctl logs, optionally for a unit, at a minimum priority.", read_journal,
             {"unit": S, "lines": I, "priority": {**S, "enum": list(PRIORITIES)}}, ("unit", "lines", "priority")),
        Tool("listening_ports", "List listening TCP/UDP sockets with processes.", listening_ports),
        Tool("recent_logins", "Recent successful and failed logins.", recent_logins),
        Tool("restart_service", "Restart a systemd service.", restart_service, {"name": S}, risk="write"),
        Tool("run_command", "Run a command (argv-split, no shell/pipes). Use only if no other tool fits.",
             lambda command: run_command(command, denylist), {"command": S}, risk="write"),
    ]
    return {t.name: t for t in tools}
