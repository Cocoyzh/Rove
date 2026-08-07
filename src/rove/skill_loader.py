from typing import Any
from pathlib import Path
import re

import yaml


class SkillLoader:
    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self.skills: dict[str, dict[str, Any]] = {}
        self._load_all()

    def _load_all(self) -> None:
        if not self.skills_dir.exists():
            return

        for skill_file in sorted(self.skills_dir.rglob("SKILL.md")):
            text = skill_file.read_text(encoding="utf-8")
            meta, body = self._parse_frontmatter(text)

            name = meta.get("name", skill_file.parent.name)
            self.skills[name] = {
                "meta": meta,
                "body": body,
                "path": str(skill_file)
            }

    def _parse_frontmatter(self, text: str) -> tuple[dict[str, Any], str]:
        """Parse YAML frontmatter between --- delimiters."""
        match = re.match(r"^---\n(.*?)\n---\n(.*)", text, flags=re.DOTALL)

        if not match:
            return {}, text.strip()

        try:
            meta = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            meta = {}

        body = match.group(2).strip()
        return meta, body

    def get_descriptions(self) -> str:
        if not self.skills:
            return "(no skills available)"
        lines = []
        for name, skill in self.skills.items():
            meta = skill["meta"]
            description = meta.get("description", "no description")
            tags = meta.get("tags", "")

            line = f" -{name}: {description}"
            if tags:
                line += f" [{tags}]"

            lines.append(line)

        return "\n".join(lines)

    def get_content(self, name: str) -> str:
        skill = self.skills.get(name)

        if not skill:
            available = ", ".join(self.skills.keys())
            return f"Error: Unknown skill {name}. Available: {available}"

        return (
            f"<skill name=\"{name}\">\n"
            f"{skill['body']}\n"
            f"</skill>"
        )