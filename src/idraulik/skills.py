"""Skills: Markdown files with optional frontmatter, injected into the agent's system prompt.

    ---
    name: disk-pressure
    description: When and how to handle low disk space
    ---
    Instructions...
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Skill:
    name: str
    description: str
    body: str


def parse_skill(path: Path) -> Skill:
    text = path.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        _, _, rest = text.partition("---")
        head, sep, body = rest.partition("\n---")
        if sep:
            for line in head.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
        else:
            body = text
    return Skill(meta.get("name", path.stem), meta.get("description", ""), body.strip())


def load_skills(directory: str) -> list[Skill]:
    d = Path(directory)
    if not d.is_dir():
        return []
    return [parse_skill(p) for p in sorted(d.glob("*.md"))]


def skills_prompt(skills: list[Skill]) -> str:
    return "\n\n".join(f"### Skill: {s.name}\n{s.description}\n\n{s.body}" for s in skills)
