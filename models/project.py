"""Project model + settings persistence (config/app.xml)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

from lxml import etree

DEFAULT_INCLUDE: List[str] = []   # empty = detect ALL file types (text + binary)
DEFAULT_EXCLUDE = ["**/.git/**", "**/__pycache__/**", "**/data/**", "**/cache/**",
                   "**/node_modules/**", "**/backups/**", "**/storage/**"]


@dataclass
class ProjectSettings:
    # matching strategy (section 72)
    exact_match: bool = True
    hash_validation: bool = True
    context_match: bool = True
    fuzzy_match: bool = False
    fuzzy_threshold: float = 0.90
    # safety (section 73) - everything ON for the first release
    require_preview: bool = True
    require_confirmation: bool = True
    backup_before_apply: bool = True
    conflict_detection: bool = True
    external_paths: bool = False
    auto_apply: bool = False
    # prompt token saving: skip repeating blocks in generated prompts
    include_system: bool = True     # False => omit <system> from next prompt
    include_files: bool = True      # False => omit <files> from next prompt
    include_symbol: bool = True     # False => no Tree-Sitter <symbol> targeting in prompt / instructions
    context_lines: int = 3          # padding lines around picked ranges in 'context' mode


@dataclass
class Project:
    name: str = "Untitled"
    root: str = ""
    include: List[str] = field(default_factory=lambda: list(DEFAULT_INCLUDE))
    exclude: List[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE))
    rules: List[str] = field(default_factory=list)          # user-defined rules (section 44)
    settings: ProjectSettings = field(default_factory=ProjectSettings)

    def is_open(self) -> bool:
        return bool(self.root) and os.path.isdir(self.root)

    # -- JSON (de)serialisation for .ai-patch-manager/config.json -------------
    def to_dict(self) -> dict:
        s = self.settings
        return {
            "name": self.name,
            "root": self.root,
            "include": list(self.include),
            "exclude": list(self.exclude),
            "rules": list(self.rules),
            "settings": {k: getattr(s, k) for k in s.__dataclass_fields__},
        }

    @staticmethod
    def from_dict(d: dict) -> "Project":
        p = Project(
            name=d.get("name") or "Untitled",
            root=d.get("root") or "",
            include=list(d.get("include", DEFAULT_INCLUDE)),
            exclude=list(d.get("exclude", DEFAULT_EXCLUDE)),
            rules=list(d.get("rules", [])),
        )
        sd = d.get("settings") or {}
        for key, value in sd.items():
            if hasattr(p.settings, key):
                cur = getattr(p.settings, key)
                try:
                    setattr(p.settings, key, type(cur)(value))
                except (TypeError, ValueError):
                    pass
        return p


# ---------------------------------------------------------------------------
# XML persistence (section 6)
# ---------------------------------------------------------------------------

def save_project_xml(path: str, project: Project) -> None:
    root = etree.Element("project")
    etree.SubElement(root, "name").text = project.name
    etree.SubElement(root, "root").text = project.root

    inc = etree.SubElement(root, "include")
    for pat in project.include:
        etree.SubElement(inc, "pattern").text = pat

    exc = etree.SubElement(root, "exclude")
    for pat in project.exclude:
        etree.SubElement(exc, "pattern").text = pat

    if project.rules:
        rules_el = etree.SubElement(root, "rules")
        for r in project.rules:
            etree.SubElement(rules_el, "rule").text = r

    s = project.settings
    set_el = etree.SubElement(root, "settings")
    for key in ("exact_match", "hash_validation", "context_match", "fuzzy_match",
                "require_preview", "require_confirmation", "backup_before_apply",
                "conflict_detection", "external_paths", "auto_apply",
                "include_system", "include_files", "include_symbol"):
        etree.SubElement(set_el, key).text = "true" if getattr(s, key) else "false"
    etree.SubElement(set_el, "fuzzy_threshold").text = str(s.fuzzy_threshold)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(etree.tostring(root, pretty_print=True, xml_declaration=True,
                                encoding="UTF-8"))


def load_project_xml(path: str) -> Optional[Project]:
    if not os.path.isfile(path):
        return None
    parser = etree.XMLParser(resolve_entities=False, no_network=True, dtd_validation=False,
                             load_dtd=False, huge_tree=False)
    tree = etree.parse(path, parser)
    root = tree.getroot()

    def text(tag):
        el = root.find(tag)
        return el.text.strip() if el is not None and el.text else None

    project = Project(name=text("name") or "Untitled", root=text("root") or "")
    inc = root.find("include")
    if inc is not None:
        project.include = [p.text.strip() for p in inc.findall("pattern") if p.text]
    exc = root.find("exclude")
    if exc is not None:
        project.exclude = [p.text.strip() for p in exc.findall("pattern") if p.text]
    rules = root.find("rules")
    if rules is not None:
        project.rules = [r.text.strip() for r in rules.findall("rule") if r.text]

    s = project.settings
    set_el = root.find("settings")
    if set_el is not None:
        flags = {"true": True, "false": False}
        for key in ("exact_match", "hash_validation", "context_match", "fuzzy_match",
                    "require_preview", "require_confirmation", "backup_before_apply",
                    "conflict_detection", "external_paths", "auto_apply",
                    "include_system", "include_files", "include_symbol"):
            el = set_el.find(key)
            if el is not None and el.text:
                setattr(s, key, flags.get(el.text.strip().lower(), getattr(s, key)))
            elif el is not None:
                setattr(s, key, True)  # empty element == enabled
        ft = set_el.find("fuzzy_threshold")
        if ft is not None and ft.text:
            try:
                s.fuzzy_threshold = float(ft.text)
            except ValueError:
                pass
    return project
