"""Loads SKILL.md + reference docs from on-disk skill bundles and renders them
into Codex CLI system instructions.

Two render modes are supported:

* ``render_system_section()`` — full body inline. Use only when there's a small
  number of skills and the system prompt budget allows it.
* ``render_index_entry()`` — single-line index entry (name + first paragraph).
  Use when there are many skills and the agent should load full bodies on
  demand via shell (``cat /app/skills/<name>/SKILL.md``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Skill:
    name: str
    summary: str
    skill_md: str
    references: dict[str, str]
    """{ relative_path: content }"""

    def render_system_section(self, *, include_references: bool = True) -> str:
        parts = [
            f"## Skill: {self.name}",
            self.skill_md.strip(),
        ]
        if include_references and self.references:
            parts.append("\n### Skill references (full inline)\n")
            for path, content in sorted(self.references.items()):
                parts.append(f"#### `{path}`\n\n{content.strip()}\n")
        return "\n\n".join(parts)

    def render_index_entry(self, *, container_path: str = "/app/skills") -> str:
        """Compact index entry for the system prompt."""
        return f"- **{self.name}** — {self.summary}\n  _path:_ `{container_path}/{self.name}/SKILL.md`"


def load_skills(skills_dir: Path) -> list[Skill]:
    out: list[Skill] = []
    if not skills_dir.exists():
        return out
    for d in sorted(skills_dir.iterdir()):
        if not d.is_dir():
            continue
        skill_md = d / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            body = skill_md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        summary = _extract_summary(body)
        refs: dict[str, str] = {}
        ref_dir = d / "references"
        if ref_dir.exists():
            for f in sorted(ref_dir.rglob("*.md")):
                try:
                    rel = f.relative_to(d).as_posix()
                    refs[rel] = f.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError, ValueError):
                    continue
        out.append(Skill(name=d.name, summary=summary, skill_md=body, references=refs))
    return out


def render_skills_index(
    skills: list[Skill],
    *,
    container_path: str = "/app/skills",
) -> str:
    """Render a compact index of all skills with on-disk paths.

    The system prompt instructs the agent to read the full SKILL.md when a
    skill is relevant to the user's request — this keeps the per-turn prompt
    cost bounded regardless of how many skills are loaded.
    """
    if not skills:
        return ""
    lines = [
        "## Available skills (index)",
        "",
        f"You have {len(skills)} skill bundles available on the host filesystem at",
        f"`{container_path}/<name>/`. Each entry below shows the skill name and a",
        "one-paragraph summary. **When the user's request matches a skill**, read",
        "the full SKILL.md body via `cat` (and the bundle's `references/`,",
        "`scripts/`, `assets/` subfolders as needed) BEFORE producing your answer",
        "— the index is intentionally terse so it fits in context; the body is",
        "where the actual instructions live.",
        "",
    ]
    for s in skills:
        lines.append(s.render_index_entry(container_path=container_path))
    return "\n".join(lines)


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_DESC_RE = re.compile(r"^description:\s*(.*?)\s*$", re.MULTILINE)


def _extract_summary(md: str) -> str:
    """Prefer YAML frontmatter `description` field (Claude-skills format),
    falling back to the first non-heading paragraph of the body."""
    fm = _FRONTMATTER_RE.match(md)
    if fm:
        m = _DESC_RE.search(fm.group(1))
        if m:
            desc = m.group(1).strip().strip('"').strip("'")
            if desc:
                # Truncate to 240 chars for index brevity.
                if len(desc) > 240:
                    desc = desc[:237].rstrip() + "…"
                return desc
        # Strip frontmatter before falling back.
        md = md[fm.end():]
    return _first_paragraph(md)


def _first_paragraph(md: str) -> str:
    lines: list[str] = []
    for ln in md.splitlines():
        if ln.startswith("#"):
            continue
        ln = ln.strip()
        if not ln:
            if lines:
                break
            continue
        lines.append(ln)
        if len(" ".join(lines)) > 240:
            break
    return " ".join(lines)
