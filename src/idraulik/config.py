"""Configuration loading (TOML) and secrets (env file)."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG = "/etc/idraulik/idraulik.toml"
DEFAULT_SECRETS = "/etc/idraulik/secrets.env"

# Remote providers auto-enabled as fallbacks when their API key is present in secrets.env.
PRESETS: dict[str, dict] = {
    "claude": dict(type="anthropic", base_url="https://api.anthropic.com",
                   model="claude-sonnet-4-5", api_key_env="ANTHROPIC_API_KEY"),
    "gemini": dict(type="gemini", base_url="https://generativelanguage.googleapis.com",
                   model="gemini-2.5-flash", api_key_env="GEMINI_API_KEY"),
    "github": dict(type="openai", base_url="https://models.github.ai/inference",
                   model="openai/gpt-4.1-mini", api_key_env="GITHUB_TOKEN"),
    "openai": dict(type="openai", base_url="https://api.openai.com/v1",
                   model="gpt-4o-mini", api_key_env="OPENAI_API_KEY"),
    "openrouter": dict(type="openai", base_url="https://openrouter.ai/api/v1",
                       model="anthropic/claude-sonnet-4.5", api_key_env="OPENROUTER_API_KEY"),
}

DEFAULT_WATCH_PATHS = [
    "/etc/passwd", "/etc/shadow", "/etc/group", "/etc/sudoers", "/etc/sudoers.d", "/etc/ssh",
    "/etc/crontab", "/etc/cron.d", "/etc/hosts", "/etc/ld.so.preload", "/etc/systemd/system",
    "/root/.ssh", "/etc/idraulik/idraulik.toml",
]


@dataclass
class ProviderConfig:
    name: str
    type: str = "ollama"  # ollama | openai | anthropic | gemini
    base_url: str = "http://127.0.0.1:11434"
    model: str = "qwen2.5:7b"
    api_key_env: str = ""  # name of env var holding the token (never store tokens in the TOML)
    timeout: int = 120


@dataclass
class Config:
    interval_seconds: int = 900
    agent_provider: str = "local"
    fallback_providers: list[str] = field(default_factory=list)
    max_steps: int = 8
    approval: str = "required"  # required | auto
    state_dir: str = "/var/lib/idraulik"
    skills_dir: str = "/etc/idraulik/skills"
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    command_denylist: list[str] = field(default_factory=list)
    alerts_file: str = "/var/log/idraulik/alerts.log"
    monitors_enabled: list[str] = field(default_factory=lambda: ["ports", "wifi", "lan", "bluetooth", "files"])
    monitor_interval: int = 60
    watch_paths: list[str] = field(default_factory=lambda: list(DEFAULT_WATCH_PATHS))
    bluetooth_scan_seconds: int = 5


def load_env_file(path: str | None = None) -> None:
    p = Path(path or os.environ.get("IDRAULIK_SECRETS", DEFAULT_SECRETS))
    try:
        text = p.read_text()
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if v.strip().strip('"').strip("'"):
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def find_config_path(explicit: str | None = None) -> Path | None:
    for c in (explicit, os.environ.get("IDRAULIK_CONFIG"), DEFAULT_CONFIG, "config/idraulik.toml"):
        if c and Path(c).is_file():
            return Path(c)
    return None


def load_config(explicit: str | None = None) -> Config:
    path = find_config_path(explicit)
    data = tomllib.loads(path.read_text()) if path else {}
    d, a, pol = data.get("daemon", {}), data.get("agent", {}), data.get("policy", {})
    al, mo = data.get("alerts", {}), data.get("monitors", {})
    cfg = Config(
        interval_seconds=int(d.get("interval_seconds", 900)),
        state_dir=d.get("state_dir", "/var/lib/idraulik"),
        skills_dir=d.get("skills_dir", "/etc/idraulik/skills"),
        agent_provider=a.get("provider", "local"),
        fallback_providers=list(a.get("fallback", [])),
        max_steps=int(a.get("max_steps", 8)),
        approval=pol.get("approval", "required"),
        command_denylist=list(pol.get("command_denylist", [])),
        alerts_file=al.get("file", "/var/log/idraulik/alerts.log"),
        monitor_interval=int(mo.get("interval_seconds", 60)),
        bluetooth_scan_seconds=int(mo.get("bluetooth_scan_seconds", 5)),
    )
    if "enabled" in mo:
        cfg.monitors_enabled = list(mo["enabled"])
    cfg.watch_paths = list(mo.get("watch_paths", DEFAULT_WATCH_PATHS)) + list(mo.get("extra_watch_paths", []))
    for name, p in data.get("providers", {}).items():
        base = {**PRESETS.get(name, {}), **p}
        cfg.providers[name] = ProviderConfig(name=name, **base)
    cfg.providers.setdefault("local", ProviderConfig(name="local"))
    # Any preset whose API key is set becomes an automatic fallback.
    if a.get("auto_remote_fallback", True):
        for name, preset in PRESETS.items():
            if os.environ.get(preset["api_key_env"]) and name != cfg.agent_provider \
                    and name not in cfg.fallback_providers:
                cfg.providers.setdefault(name, ProviderConfig(name=name, **preset))
                cfg.fallback_providers.append(name)
    for n in [cfg.agent_provider, *cfg.fallback_providers]:
        if n not in cfg.providers:
            raise ValueError(f"provider '{n}' not defined in [providers]")
    return cfg
