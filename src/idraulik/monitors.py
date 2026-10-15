"""Deterministic monitors. Each takes a snapshot (key -> value), diffs it against the stored baseline
and emits alerts. First run only records the baseline. The AI agent then triages the alerts."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .alerts import Alerter
from .config import Config

MAX_HASH_BYTES = 20 * 1024 * 1024
MAX_FILES = 5000
EXPOSED = ("0.0.0.0", "[::]", "*", "::")


def _out(cmd: list[str], timeout: int = 30) -> str | None:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired, PermissionError):
        return None
    return p.stdout if p.returncode == 0 or p.stdout.strip() else None


# ---------------------------------------------------------------- collectors

def parse_ss(text: str) -> dict[str, str]:
    """`ss -H -tulpn` -> {"tcp/0.0.0.0:22": "sshd"}"""
    snap = {}
    for line in text.splitlines():
        f = line.split()
        if len(f) < 5:
            continue
        m = re.search(r'\("([^"]+)"', line)
        snap[f"{f[0]}/{f[4]}"] = m.group(1) if m else "?"
    return snap


def collect_ports() -> dict[str, str] | None:
    out = _out(["ss", "-H", "-tulpn"])
    return None if out is None else parse_ss(out)


def _split_nmcli(line: str) -> list[str]:
    return [p.replace("\\:", ":") for p in re.split(r"(?<!\\):", line)]


def parse_nmcli_wifi(text: str) -> dict[str, str]:
    """`nmcli -t -f ACTIVE,SSID,BSSID,SECURITY dev wifi list` -> {bssid: "ssid|security"}"""
    snap = {}
    for line in text.splitlines():
        f = _split_nmcli(line)
        if len(f) < 4:
            continue
        active, ssid, bssid, sec = f[0], f[1], f[2], f[3]
        snap[bssid] = f"{ssid}|{sec}"
        if active == "yes":
            snap["__connected__"] = f"{ssid}|{bssid}"
    return snap


def collect_wifi() -> dict[str, str] | None:
    out = _out(["nmcli", "-t", "-f", "ACTIVE,SSID,BSSID,SECURITY", "dev", "wifi", "list"])
    return None if out is None else parse_nmcli_wifi(out)


def parse_ip_neigh(text: str) -> dict[str, str]:
    snap = {}
    for line in text.splitlines():
        m = re.match(r"(\S+) dev (\S+) lladdr (\S+)", line)
        if m and not line.rstrip().endswith(("FAILED", "INCOMPLETE")):
            snap[m.group(3).lower()] = f"{m.group(1)} on {m.group(2)}"
    return snap


def collect_lan() -> dict[str, str] | None:
    out = _out(["ip", "neigh"])
    return None if out is None else parse_ip_neigh(out)


def parse_btctl(text: str, prefix: str = "") -> dict[str, str]:
    return {prefix + m.group(1).upper(): m.group(2).strip()
            for m in re.finditer(r"^Device ([0-9A-Fa-f:]{17}) ?(.*)$", text, re.M)}


def make_bluetooth_collector(scan_seconds: int) -> Callable[[], dict[str, str] | None]:
    def collect():
        if scan_seconds > 0:
            _out(["bluetoothctl", "--timeout", str(scan_seconds), "scan", "on"], timeout=scan_seconds + 10)
        devices = _out(["bluetoothctl", "devices"])
        if devices is None or "No default controller" in devices:
            return None
        snap = parse_btctl(devices)
        paired = _out(["bluetoothctl", "devices", "Paired"]) or ""
        snap.update(parse_btctl(paired, "paired:"))
        return snap
    return collect


def _hash(path: Path) -> str:
    try:
        st = path.stat()
        if st.st_size > MAX_HASH_BYTES:
            return f"big:{st.st_size}|{st.st_mode:o}"
        h = hashlib.sha256(path.read_bytes()).hexdigest()[:24]
        return f"{h}|{st.st_mode:o}|{st.st_uid}"
    except OSError:
        return "unreadable"


def make_file_collector(paths: list[str]) -> Callable[[], dict[str, str]]:
    def collect():
        snap: dict[str, str] = {}
        roots = list(paths)
        try:
            roots += [str(p) for p in Path("/home").glob("*/.ssh")]
        except OSError:
            pass
        for root in roots:
            rp = Path(root)
            if rp.is_file():
                snap[root] = _hash(rp)
            elif rp.is_dir():
                for dirpath, _, files in os.walk(rp):
                    for name in files:
                        if len(snap) >= MAX_FILES:
                            return snap
                        fp = Path(dirpath) / name
                        if fp.is_file() and not fp.is_symlink():
                            snap[str(fp)] = _hash(fp)
        return snap
    return collect


# ---------------------------------------------------------------- classifiers
# classify(kind, key, old, new, old_snapshot) -> (level, message) | None ; kind in new|removed|changed

def classify_ports(kind, key, old, new, snap):
    proto, addr = key.split("/", 1)
    exposed = addr.rsplit(":", 1)[0] in EXPOSED
    if kind == "new":
        return ("WARN" if exposed else "INFO",
                f"new {'EXPOSED ' if exposed else 'local '}listener {proto} {addr} (process: {new})")
    if kind == "removed":
        return "INFO", f"listener closed {proto} {addr} (was: {old})"
    return "WARN", f"listener {proto} {addr} changed process {old} -> {new}"


def classify_wifi(kind, key, old, new, snap):
    if key == "__connected__":
        return "WARN", f"wifi connection changed: {old} -> {new}"
    if kind != "new":
        return ("WARN", f"wifi AP {key} changed {old} -> {new}") if kind == "changed" else None
    ssid, sec = new.split("|", 1)
    if any(v.split("|", 1)[0] == ssid and k != key for k, v in snap.items() if k != "__connected__") and ssid:
        return "WARN", f"new access point {key} for known SSID '{ssid}' (possible evil twin)"
    if not sec or sec == "--":
        return "WARN", f"new OPEN wifi network '{ssid}' ({key}) nearby"
    return "INFO", f"new wifi network '{ssid}' ({key}, {sec}) nearby"


def classify_lan(kind, key, old, new, snap):
    if kind == "new":
        return "WARN", f"new device on local network: {key} ({new})"
    return None


def classify_bluetooth(kind, key, old, new, snap):
    if kind != "new":
        return None
    if key.startswith("paired:"):
        return "CRIT", f"new PAIRED bluetooth device {key[7:]} ({new})"
    return "INFO", f"new bluetooth device nearby {key} ({new})"


def classify_files(kind, key, old, new, snap):
    return {"new": ("WARN", f"file created: {key}"), "removed": ("WARN", f"file removed: {key}"),
            "changed": ("CRIT", f"file modified: {key}")}[kind]


@dataclass
class Monitor:
    name: str
    collect: Callable[[], dict[str, str] | None]
    classify: Callable


def build_monitors(cfg: Config) -> list[Monitor]:
    all_ = {
        "ports": Monitor("ports", collect_ports, classify_ports),
        "wifi": Monitor("wifi", collect_wifi, classify_wifi),
        "lan": Monitor("lan", collect_lan, classify_lan),
        "bluetooth": Monitor("bluetooth", make_bluetooth_collector(cfg.bluetooth_scan_seconds), classify_bluetooth),
        "files": Monitor("files", make_file_collector(cfg.watch_paths), classify_files),
    }
    return [all_[n] for n in cfg.monitors_enabled if n in all_]


def diff(old: dict[str, str], new: dict[str, str]) -> list[tuple[str, str, str | None, str | None]]:
    out = [("new", k, None, v) for k, v in new.items() if k not in old]
    out += [("removed", k, v, None) for k, v in old.items() if k not in new]
    out += [("changed", k, old[k], v) for k, v in new.items() if k in old and old[k] != v]
    return out


class MonitorRunner:
    def __init__(self, cfg: Config, alerter: Alerter):
        self.monitors = build_monitors(cfg)
        self.alerter = alerter
        self.path = Path(cfg.state_dir) / "baseline.json"

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def reset(self) -> None:
        self.path.unlink(missing_ok=True)

    def run_once(self) -> int:
        base = self._load()
        count = 0
        for m in self.monitors:
            try:
                snap = m.collect()
            except Exception as e:
                self.alerter.alert("WARN", m.name, f"monitor failed: {type(e).__name__}: {e}")
                continue
            if snap is None:
                continue  # hardware/tool not available on this host
            if m.name not in base:
                base[m.name] = snap
                self.alerter.alert("INFO", m.name, f"baseline created ({len(snap)} items)")
                continue
            for kind, key, o, n in diff(base[m.name], snap):
                res = m.classify(kind, key, o, n, base[m.name])
                if res:
                    self.alerter.alert(res[0], m.name, res[1])
                    count += 1
            base[m.name] = snap
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(base))
        tmp.replace(self.path)
        return count
