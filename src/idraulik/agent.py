"""Agent loop: provider + skills + tools + approval policy."""
from __future__ import annotations

import logging

from .approvals import ApprovalStore
from .config import Config
from .providers import ProviderError, build_provider
from .skills import load_skills, skills_prompt
from .tools import Tool, build_tools

log = logging.getLogger("idraulik")

SYSTEM_PROMPT = """You are Idraulik Shield, an autonomous sysadmin agent running on a Debian-based Linux server.
Your job: keep the system healthy, secure and available.
Rules:
- Investigate with read tools first; never guess. Cite evidence from tool output.
- Prefer the smallest, reversible action. Write actions may be queued for human approval; if a tool
  reports 'pending approval', do not retry it, just mention it in your report.
- Never touch data, users, firewall or SSH config unless a skill explicitly allows it.
- End with a short report: STATUS (OK/WARN/CRIT), findings, actions taken, actions awaiting approval.

{skills}"""


class Agent:
    def __init__(self, cfg: Config, tools: dict[str, Tool] | None = None, providers=None, alerter=None):
        self.cfg = cfg
        self.tools = tools or build_tools(cfg.command_denylist, alerter)
        self.approvals = ApprovalStore(cfg.state_dir)
        names = [cfg.agent_provider, *cfg.fallback_providers]
        self.providers = providers or [build_provider(cfg.providers[n]) for n in names]
        self.system = SYSTEM_PROMPT.format(skills=skills_prompt(load_skills(cfg.skills_dir)))

    def execute_tool(self, name: str, args: dict, reason: str = "") -> str:
        tool = self.tools.get(name)
        if not tool:
            return f"error: unknown tool {name}"
        if tool.risk == "write" and self.cfg.approval != "auto":
            item = self.approvals.add(name, args, reason)
            return f"pending approval (id {item['id']}); a human must run: idraulik approvals approve {item['id']}"
        return self._call(tool, args)

    @staticmethod
    def _call(tool: Tool, args: dict) -> str:
        try:
            return tool.fn(**{k: v for k, v in args.items() if k in tool.params})
        except Exception as e:
            return f"error: {type(e).__name__}: {e}"

    def run_approved(self, id_: str) -> str:
        item = next((i for i in self.approvals.list("pending") if i["id"] == id_), None)
        if not item:
            return f"no pending approval {id_}"
        tool = self.tools[item["tool"]]
        result = self._call(tool, item["arguments"])
        self.approvals.update(id_, status="executed", result=result)
        return result

    def _chat(self, messages, schemas):
        last = None
        for p in self.providers:
            try:
                return p.chat(messages, schemas)
            except ProviderError as e:
                log.warning("provider %s failed: %s", p.cfg.name, e)
                last = e
        raise ProviderError(f"all providers failed: {last}")

    def run(self, task: str) -> str:
        messages = [{"role": "system", "content": self.system}, {"role": "user", "content": task}]
        schemas = [t.schema() for t in self.tools.values()]
        for _ in range(self.cfg.max_steps):
            reply = self._chat(messages, schemas)
            calls = reply["tool_calls"]
            messages.append({"role": "assistant", "content": reply["content"], "tool_calls": calls})
            if not calls:
                return reply["content"].strip()
            for c in calls:
                log.info("tool %s %s", c["name"], c["arguments"])
                result = self.execute_tool(c["name"], c["arguments"], reason=task[:120])
                messages.append({"role": "tool", "tool_call_id": c["id"], "name": c["name"], "content": result})
        messages.append({"role": "user", "content": "Step limit reached. Give your final report now without tools."})
        return self._chat(messages, [])["content"].strip()
