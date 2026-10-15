import os

from idraulik.agent import Agent
from idraulik.config import Config, ProviderConfig
from idraulik.skills import parse_skill
from idraulik.tools import Tool, build_tools, run_command


class FakeProvider:
    def __init__(self, replies):
        self.replies = list(replies)
        self.cfg = ProviderConfig(name="fake")

    def chat(self, messages, tools):
        return self.replies.pop(0)


def make_cfg(tmp_path, approval="required"):
    return Config(state_dir=str(tmp_path), skills_dir=str(tmp_path / "none"), approval=approval)


def test_read_tool_loop(tmp_path):
    tools = {"ping": Tool("ping", "d", lambda: "pong")}
    p = FakeProvider([
        {"content": "", "tool_calls": [{"id": "1", "name": "ping", "arguments": {}}]},
        {"content": "STATUS: OK", "tool_calls": []},
    ])
    assert Agent(make_cfg(tmp_path), tools, [p]).run("check") == "STATUS: OK"


def test_write_tool_queued_then_approved(tmp_path):
    tools = {"w": Tool("w", "d", lambda x: f"did {x}", {"x": {"type": "string"}}, risk="write")}
    a = Agent(make_cfg(tmp_path), tools, [FakeProvider([])])
    assert "pending approval" in a.execute_tool("w", {"x": "1"})
    (item,) = a.approvals.list("pending")
    assert a.run_approved(item["id"]) == "did 1"
    assert not a.approvals.list("pending")


def test_auto_mode_executes(tmp_path):
    tools = {"w": Tool("w", "d", lambda: "ok", risk="write")}
    assert Agent(make_cfg(tmp_path, "auto"), tools, [FakeProvider([])]).execute_tool("w", {}) == "ok"


def test_denylist():
    assert "blocked" in run_command("rm -rf /")
    assert "blocked" in run_command("reboot")


def test_tool_schemas():
    for t in build_tools().values():
        s = t.schema()["function"]["parameters"]
        assert set(s["required"]) <= set(s["properties"])


def test_parse_ss():
    from idraulik.monitors import parse_ss
    s = parse_ss('tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=1,fd=3))\n'
                 'udp UNCONN 0 0 127.0.0.53%lo:53 0.0.0.0:*\n')
    assert s == {"tcp/0.0.0.0:22": "sshd", "udp/127.0.0.53%lo:53": "?"}


def test_parse_wifi_and_bt():
    from idraulik.monitors import parse_btctl, parse_nmcli_wifi
    w = parse_nmcli_wifi("yes:Home:AA\\:BB\\:CC\\:DD\\:EE\\:FF:WPA2\nno:Cafe:11\\:22\\:33\\:44\\:55\\:66:\n")
    assert w["AA:BB:CC:DD:EE:FF"] == "Home|WPA2" and w["__connected__"].startswith("Home|")
    assert parse_btctl("Device aa:bb:cc:dd:ee:ff Phone\n", "paired:") == {"paired:AA:BB:CC:DD:EE:FF": "Phone"}


def test_monitor_baseline_then_alerts(tmp_path, monkeypatch):
    from idraulik import monitors
    from idraulik.alerts import Alerter
    cfg = make_cfg(tmp_path)
    cfg.monitors_enabled = ["ports"]
    snaps = [{"tcp/127.0.0.1:5432": "postgres"},
             {"tcp/127.0.0.1:5432": "postgres", "tcp/0.0.0.0:4444": "nc"}]
    monkeypatch.setattr(monitors, "collect_ports", lambda: snaps.pop(0))
    al = Alerter(str(tmp_path / "alerts.log"), str(tmp_path))
    r = monitors.MonitorRunner(cfg, al)
    assert r.run_once() == 0  # baseline
    assert r.run_once() == 1
    lines = al.recent(10, "WARN")
    assert len(lines) == 1 and "WARN [ports]" in lines[0] and "EXPOSED" in lines[0]


def test_file_monitor(tmp_path):
    from idraulik.monitors import classify_files, diff, make_file_collector
    f = tmp_path / "passwd"
    f.write_text("a")
    col = make_file_collector([str(f)])
    before = col()
    f.write_text("b")
    (kind, key, o, n), = diff(before, col())
    assert kind == "changed" and classify_files(kind, key, o, n, {})[0] == "CRIT"


def test_presets_enable_on_key(tmp_path, monkeypatch):
    from idraulik.config import load_config
    p = tmp_path / "c.toml"
    p.write_text("")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    cfg = load_config(str(p))
    assert cfg.agent_provider == "local" and cfg.fallback_providers == ["claude"]
    assert cfg.providers["claude"].type == "anthropic"


def test_anthropic_gemini_roundtrip(monkeypatch):
    from idraulik import providers
    monkeypatch.setenv("K", "k")
    sent = {}

    def fake_post(url, payload, headers, timeout):
        sent["p"] = payload
        if "anthropic" in url:
            return {"content": [{"type": "text", "text": "hi"}, {"type": "tool_use", "id": "t1", "name": "ping", "input": {}}]}
        return {"candidates": [{"content": {"parts": [{"text": "hi"}, {"functionCall": {"name": "ping", "args": {}}}]}}]}

    monkeypatch.setattr(providers, "_post", fake_post)
    hist = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "name": "ping", "arguments": {}}]},
            {"role": "tool", "tool_call_id": "t1", "name": "ping", "content": "pong"}]
    tools = [Tool("ping", "d", lambda: "").schema()]
    for typ, url in (("anthropic", "https://anthropic.test"), ("gemini", "https://g.test")):
        prov = providers.build_provider(ProviderConfig(name=typ, type=typ, base_url=url, api_key_env="K"))
        out = prov.chat(hist, tools)
        assert out["content"] == "hi" and out["tool_calls"][0]["name"] == "ping"


def test_skill_parse(tmp_path):

    f = tmp_path / "a.md"
    f.write_text("---\nname: x\ndescription: d\n---\nbody here\n")
    s = parse_skill(f)
    assert (s.name, s.description, s.body) == ("x", "d", "body here")
