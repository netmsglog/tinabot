"""Skills loader - scans ~/.agents/skills/ for SKILL.md files."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from loguru import logger


class SkillsLoader:
    """Load and manage agent skills from the filesystem.

    Each skill is a directory containing a SKILL.md file with optional
    YAML frontmatter for metadata (name, description, allowed-tools, requires).
    """

    def __init__(self, skills_dir: str | Path):
        self.skills_dir = Path(skills_dir).expanduser()
        self._cache: dict[str, dict] = {}

    def list_skills(self, filter_unavailable: bool = True) -> list[dict[str, str]]:
        """List all discovered skills."""
        skills = []
        if not self.skills_dir.exists():
            return skills

        for skill_dir in sorted(self.skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue

            meta = self._get_metadata(skill_dir.name)
            if filter_unavailable and not self._check_requirements(meta):
                continue

            skills.append({
                "name": skill_dir.name,
                "path": str(skill_file),
                "description": meta.get("description", skill_dir.name),
            })

        return skills

    def load_skill(self, name: str) -> str | None:
        """Load the raw content of a skill by name."""
        skill_file = self.skills_dir / name / "SKILL.md"
        if skill_file.exists():
            return skill_file.read_text(encoding="utf-8")
        return None

    def build_system_prompt_section(self) -> str:
        """Build a system prompt section with skill summaries.

        Small skills (<2000 chars body) are included inline.
        Large skills include only a summary with a path for the agent to Read.
        """
        skills = self.list_skills(filter_unavailable=True)
        if not skills:
            return ""

        parts = ["<skills>"]
        always_skills_content = []

        for skill in skills:
            name = _escape_xml(skill["name"])
            desc = _escape_xml(skill["description"])
            path = skill["path"]
            meta = self._get_metadata(skill["name"])
            body = self._strip_frontmatter(self.load_skill(skill["name"]) or "")

            # Always-on skills get their full content included
            if meta.get("always"):
                always_skills_content.append(
                    f"### Skill: {skill['name']}\n\n{body}"
                )
                continue

            # Progressive loading: summary for large skills, inline for small
            if len(body) < 2000:
                parts.append(f'  <skill name="{name}">')
                parts.append(f"    <description>{desc}</description>")
                parts.append(f"    <content>{_escape_xml(body)}</content>")
                parts.append("  </skill>")
            else:
                parts.append(f'  <skill name="{name}">')
                parts.append(f"    <description>{desc}</description>")
                parts.append(f"    <location>{path}</location>")
                parts.append("  </skill>")

        parts.append("</skills>")

        sections = []
        if always_skills_content:
            sections.append("\n\n---\n\n".join(always_skills_content))
        if len(parts) > 2:  # More than just <skills></skills>
            sections.append("\n".join(parts))

        return "\n\n".join(sections)

    def get_all_allowed_tools(self) -> list[str]:
        """Collect allowed-tools from all available skills."""
        tools: set[str] = set()
        for skill in self.list_skills(filter_unavailable=True):
            meta = self._get_metadata(skill["name"])
            skill_tools = meta.get("allowed-tools", "")
            if skill_tools:
                tools.update(t.strip() for t in skill_tools.split(",") if t.strip())
        return sorted(tools)

    def _get_metadata(self, name: str) -> dict[str, str]:
        """Parse YAML frontmatter metadata from a skill."""
        if name in self._cache:
            return self._cache[name]

        content = self.load_skill(name)
        meta: dict[str, str] = {}
        if content and content.startswith("---"):
            match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
            if match:
                for line in match.group(1).split("\n"):
                    if ":" in line:
                        key, value = line.split(":", 1)
                        meta[key.strip()] = value.strip().strip("\"'")

        self._cache[name] = meta
        return meta

    def _strip_frontmatter(self, content: str) -> str:
        """Remove YAML frontmatter from content."""
        if content.startswith("---"):
            match = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
            if match:
                return content[match.end():].strip()
        return content

    def _check_requirements(self, meta: dict) -> bool:
        """Check if skill requirements (bins, env) are met."""
        requires_str = meta.get("requires", "")
        if not requires_str:
            return True

        # Simple format: requires: bin1,bin2 or env:VAR1,VAR2
        for req in requires_str.split(","):
            req = req.strip()
            if req.startswith("env:"):
                if not os.environ.get(req[4:]):
                    return False
            elif req and not shutil.which(req):
                return False
        return True


def _escape_xml(s: str) -> str:
    """Escape XML special characters."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
