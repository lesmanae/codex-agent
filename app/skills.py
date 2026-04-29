"""Loads SKILL.md + reference docs from on-disk skill bundles and renders them
into Gemini system instructions."""
from __future__ import annotations

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
        body = skill_md.read_text(encoding="utf-8")
        summary = _first_paragraph(body)
        refs: dict[str, str] = {}
        ref_dir = d / "references"
        if ref_dir.exists():
            for f in sorted(ref_dir.rglob("*.md")):
                rel = f.relative_to(d).as_posix()
                refs[rel] = f.read_text(encoding="utf-8")
        out.append(Skill(name=d.name, summary=summary, skill_md=body, references=refs))
    return out


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
