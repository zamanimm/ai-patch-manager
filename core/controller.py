"""Application state machine + orchestration (section 67).

All UI code goes through this controller; the controller talks to the
core modules only. Workflow states:

    DRAFT -> PROMPT_READY -> SENT -> RESPONSE_RECEIVED -> PARSED
          -> VALIDATED -> PREVIEW -> APPROVED -> BACKED_UP -> APPLIED

Failure states: INVALID / CONFLICT / FAILED / REJECTED / ROLLED_BACK.
"""
from __future__ import annotations

import os
import tempfile
import time
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

from core import file_manager, prompt_builder, scanner
from core.diff_engine import fill_change_diffs, unified_diff
from core.git_manager import GitManager
from core.history_manager import HistoryManager
from core.parser import ProtocolError, ResponseNote, parse_response_with_note, strip_code_fences
from core.process_runner import RunResult, run_command
from core.recent_projects import RecentProjects
from core.transaction import ApplyReport, TransactionRunner
from core.validator import validate_changes
from models.change import Change
from models.project import Project, load_project_xml, save_project_xml
from models.transaction import Transaction

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_XML = os.path.join(APP_DIR, "config", "app.xml")
STORAGE_DIR = os.path.join(APP_DIR, "storage")

# Per-workspace data directory (created inside every opened project):
#   .ai-patch-manager/config.json    -> all configs, auto-reloaded on open
#   .ai-patch-manager/history.json   -> transaction history (undo / redo)
#   .ai-patch-manager/backups/       -> byte-exact file snapshots
WORKSPACE_DIR_NAME = ".ai-patch-manager"


def workspace_dir(project_root: str) -> str:
    return os.path.join(project_root, WORKSPACE_DIR_NAME)


def _migrate_legacy_history(legacy_dir: str, new_path: str) -> None:
    """Fold old per-file JSON history into the single history.json once."""
    if os.path.isfile(new_path) or not os.path.isdir(legacy_dir):
        return
    records = []
    for name in sorted(os.listdir(legacy_dir)):
        if name.endswith(".json"):
            try:
                import json
                with open(os.path.join(legacy_dir, name), "r", encoding="utf-8") as fh:
                    records.append(json.load(fh))
            except (OSError, ValueError):
                continue
    if records:
        import json
        with open(new_path, "w", encoding="utf-8") as fh:
            json.dump({"transactions": records}, fh, ensure_ascii=False, indent=2)


class State(str, Enum):
    DRAFT = "DRAFT"
    PROMPT_READY = "PROMPT_READY"
    SENT = "SENT"
    RESPONSE_RECEIVED = "RESPONSE_RECEIVED"
    PARSED = "PARSED"
    VALIDATED = "VALIDATED"
    PREVIEW = "PREVIEW"
    APPROVED = "APPROVED"
    APPLIED = "APPLIED"
    INVALID = "INVALID"
    CONFLICT = "CONFLICT"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    ROLLED_BACK = "ROLLED_BACK"


def _indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(pad + line for line in text.splitlines())


class Controller:
    def __init__(self):
        self.project = load_project_xml(CONFIG_XML) or Project()
        self.state = State.DRAFT
        self.log_lines: List[str] = []
        self.prompt_text: str = ""
        self.response_raw: str = ""          # last imported LLM response (for the Patch tab)
        self.snapshot_hashes: Dict[str, str] = {}
        self.response_summary: str = ""
        self.response_notes: str = ""
        self.response_notice: Optional[ResponseNote] = None
        self.changes: List[Change] = []
        self.files: List[str] = []            # scanned project files
        self.selected_files: Dict[str, str] = {}   # path -> mode (full/diff/...)
        # path -> list of 1-based inclusive line ranges chosen in the code viewer
        # (used by 'selection' and 'context' prompt modes). Persisted per workspace.
        self.file_selections: Dict[str, List[Tuple[int, int]]] = {}
        # per-workspace storage under <root>/.ai-patch-manager/ (config.json +
        # history.json + backups/); falls back to app-level dirs when no
        # project is open yet.
        self._workspace_root: str = ""
        self.history = None
        self.git = None
        self.recent = RecentProjects()
        if self.project.is_open():
            self._attach_workspace(self.project.root, reload_config=True)
            if not self.recent.contains(self.project.root):      # existing last project
                self.recent.add(self.project.root)
        else:
            self.history = HistoryManager(os.path.join(STORAGE_DIR, "history"),
                                          os.path.join(STORAGE_DIR, "backups"))
            self.log("no workspace config found - open a project to create "
                     f"{WORKSPACE_DIR_NAME}/")

    # -- logging (section 52) --------------------------------------------------
    def log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_lines.append(f"[{stamp}] {message}")
        if len(self.log_lines) > 2000:
            self.log_lines = self.log_lines[-1500:]

    # -- workspace (.ai-patch-manager/) ----------------------------------------
    def _attach_workspace(self, root: str, reload_config: bool) -> None:
        """Create/point at <root>/.ai-patch-manager and (re)load its files."""
        import json
        ws = workspace_dir(root)
        os.makedirs(os.path.join(ws, "backups"), exist_ok=True)
        cfg_path = os.path.join(ws, "config.json")
        hist_path = os.path.join(ws, "history.json")
        _migrate_legacy_history(os.path.join(ws, "history"), hist_path)

        self.history = HistoryManager(hist_path, os.path.join(ws, "backups"))
        self._workspace_root = root

        if reload_config and os.path.isfile(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                loaded = Project.from_dict(data.get("project", data))
                # keep the root we just opened
                loaded.root = root
                old_settings = self.project.settings
                self.project = loaded
                # UI views hold a reference to the OLD settings object -
                # mutate in place so checkboxes/inputs stay in sync.
                if old_settings is not self.project.settings:
                    for k in old_settings.__dataclass_fields__:
                        setattr(old_settings, k, getattr(self.project.settings, k))
                    self.project.settings = old_settings
                # restore file selections (path -> mode) saved earlier
                sel = data.get("selected_files")
                if isinstance(sel, dict):
                    self.selected_files = {k: str(v) for k, v in sel.items()}
                # FIX: restore last task / errors so the Prompt tab can
                # repopulate its text fields after a restart
                self.last_task = data.get("last_task", "")
                self.last_errors = data.get("last_errors", "")
                self.log(f"workspace config reloaded from {cfg_path}")
            except (OSError, ValueError, KeyError) as exc:
                self.log(f"workspace config unreadable ({exc}) - using defaults")
        # line-range selections (selection / context modes)
        self.load_file_selections(root)
        self.save_workspace_config()

    def save_workspace_config(self) -> None:
        """Persist ALL configs into .ai-patch-manager/config.json (auto-loaded
        next time this workspace is opened)."""
        import json
        if not self.project.is_open():
            save_project_xml(CONFIG_XML, self.project)   # fallback: app-level
            return
        cfg_path = os.path.join(workspace_dir(self.project.root), "config.json")
        data = {
            "app": "ai-patch-manager",
            "version": 1,
            "project": self.project.to_dict(),
            "selected_files": self.selected_files,
            "file_selections": {p: [list(r) for r in rng]
                                for p, rng in self.file_selections.items()},
            "last_task": getattr(self, "last_task", ""),
            "last_errors": getattr(self, "last_errors", ""),
        }
        temp_path = None
        try:
            os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                    dir=os.path.dirname(cfg_path), prefix=".config-",
                    suffix=".tmp", delete=False) as fh:
                temp_path = fh.name
                json.dump(data, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temp_path, cfg_path)
        except OSError as exc:
            self.log(f"could not write workspace config: {exc}")
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    def restore_session_state(self) -> dict:
        """Extra UI state stored in the workspace config (selections etc.)."""
        import json
        if not self.project.is_open():
            return {}
        cfg_path = os.path.join(workspace_dir(self.project.root), "config.json")
        if not os.path.isfile(cfg_path):
            return {}
        try:
            with open(cfg_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    # -- project ----------------------------------------------------------------
    def open_project(self, root: str, name: Optional[str] = None) -> None:
        new_root = os.path.abspath(root)
        previous = os.path.abspath(self.project.root) if self.project.root else ""
        if previous and os.path.normcase(previous) != os.path.normcase(new_root):
            # Switching to ANOTHER project: nothing of the old one may leak over
            # (pending changes, pasted response, prompt, file / line selections),
            # otherwise a patch meant for project A could be applied to project B.
            if self._workspace_root:
                try:
                    self.save_workspace_config()          # keep A's own selections
                except OSError:
                    pass
            self.reset_session()
            self.selected_files = {}
            self.file_selections = {}
            self.last_task = ""
            self.last_errors = ""
        self.project.root = new_root
        self.git = GitManager(self.project.root)
        # auto-load saved workspace config (name/include/exclude/rules/settings)
        self._attach_workspace(self.project.root, reload_config=True)
        if name:
            self.project.name = name
        save_project_xml(CONFIG_XML, self.project)      # remember last project
        self.save_workspace_config()
        self.refresh_files()
        self.ensure_baseline_backup()                   # backup BEFORE any patch
        self.state = State.DRAFT
        self.recent.add(self.project.root)               # shows up in Open Recent
        if self.recent.last_error:
            self.log(self.recent.last_error)
        self.log(f"project opened: {self.project.root} "
                 f"(workspace dir: {WORKSPACE_DIR_NAME}/)")

    def ensure_baseline_backup(self) -> None:
        """Create the one-time 'baseline' transaction right after connecting
        to the workspace: a byte-exact snapshot of every scanned file. This
        guarantees History diffs and future reverts always have a real
        pre-change state to compare against."""
        if not self.project.is_open():
            return
        try:
            tx = self.history.ensure_baseline(self.project.root, self.files)
        except OSError as exc:
            self.log(f"baseline backup failed: {exc}")
            return
        if tx is not None:
            self.log(f"baseline backup created ({len(tx.files)} files) "
                     f"in .ai-patch-manager/backups/")
        else:
            self.log("baseline backup already present - skipped")

    def refresh_files(self) -> None:
        if not self.project.is_open():
            self.files = []
            return
        exclude = list(self.project.exclude)
        if WORKSPACE_DIR_NAME not in " ".join(exclude):
            exclude = exclude + [f"**/{WORKSPACE_DIR_NAME}/**"]
        self.files = scanner.scan_project(self.project.root,
                                          self.project.include, exclude)
        if not self.files:
            self.log("scan found 0 files - check include/exclude patterns "
                     f"(include={self.project.include}, exclude={self.project.exclude})")
        else:
            self.log(f"scanned {len(self.files)} files")

    def save_settings(self) -> None:
        save_project_xml(CONFIG_XML, self.project)
        self.save_workspace_config()

    # -- prompt generation (sections 45-48) --------------------------------------
    def _symbol_capabilities(self):
        """Return effective Symbol availability and installed grammar names."""
        from core.optional_dependencies import tree_sitter_status
        status = tree_sitter_status()
        requested = bool(getattr(self.project.settings, "include_symbol", False))
        return requested and status.available, status.languages, status

    def instructions_text(self) -> str:
        """The pure patch-system instructions for the LLM (no files/task)."""
        include_symbol, languages, _status = self._symbol_capabilities()
        return prompt_builder.build_protocol_instruction(include_symbol, languages)

    # -- per-file selections (line ranges for selection / context modes) --------
    def load_file_selections(self, root: str) -> None:
        """Read <root>/.ai-patch-manager/selections.json into memory."""
        import json
        self.file_selections = {}
        path = os.path.join(workspace_dir(root), "selections.json")
        if not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                for p, rng in data.items():
                    try:
                        self.file_selections[p] = [tuple(r) for r in rng]
                    except (TypeError, ValueError):
                        continue
        except (OSError, ValueError):
            pass

    def save_file_selections(self) -> None:
        """Persist line-range selections to the workspace directory."""
        import json
        if not self.project.is_open():
            return
        path = os.path.join(workspace_dir(self.project.root), "selections.json")
        data = {p: [list(r) for r in rng] for p, rng in self.file_selections.items()}
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
        except OSError as exc:
            self.log(f"could not write selections: {exc}")

    def set_file_ranges(self, rel_path: str, ranges) -> None:
        """Store 1-based inclusive line ranges chosen in the code viewer."""
        ranges = [tuple(r) for r in ranges]
        if ranges:
            self.file_selections[rel_path] = ranges
        else:
            self.file_selections.pop(rel_path, None)
        self.save_file_selections()
        self.save_workspace_config()

    def get_file_ranges(self, rel_path: str):
        return list(self.file_selections.get(rel_path, []))

    def _build_prompt_files(self):
        """Turn selected_files {path: mode} + file_selections into PromptFile list."""
        pad = int(getattr(self.project.settings, "context_lines", 3) or 3)
        out = []
        for p, m in sorted(self.selected_files.items()):
            # 'diff' mode = what changed since the workspace was connected.
            # Binary files have no useful textual diff, so avoid decoding a
            # potentially large baseline binary just to discard it later.
            base = None
            if m == "diff":
                try:
                    abs_path = file_manager.safe_join(self.project.root, p)
                    is_binary = os.path.isfile(abs_path) and file_manager.is_binary_file(abs_path)
                except Exception:
                    is_binary = False
                if not is_binary:
                    base = self.history.baseline_text(p)
            out.append(prompt_builder.PromptFile(
                path=p, mode=m,
                ranges=list(self.file_selections.get(p, [])),
                context_lines=pad, base_content=base,
                include_binary=getattr(self.project.settings, "include_binary_files", False)))
        return out

    def build_full_xml_prompt(self, task: str, errors: str) -> str:
        """Build the complete XML protocol prompt (<prompt><system>...<files>).

        Includes selected text files and compact binary metadata by default.
        Base64 binary payloads are included only when explicitly enabled.

        Optional blocks (checkboxes in the Prompt tab, remembered per workspace):
          <system>            settings.include_system     (default OFF)
          <user_rules>        settings.include_user_rules (default on; also left out when no rules are set)
          <errors>            settings.include_errors     (default OFF; also left out when the box is empty)
          <files>             settings.include_files      (default on)
          <symbol_targeting>  settings.include_symbol     (default OFF)
          <project_tree>      settings.include_project_tree (default OFF; paths only)
        Unchecked blocks are omitted entirely to save tokens.
        """
        if not self.project.is_open():
            raise RuntimeError("No project is open.")
        from core.prompt_builder import PromptFile, _render_files
        s = self.project.settings
        self.last_task = task or ""
        self.last_errors = errors or ""
        files = self._build_prompt_files()
        snaps = {}
        files_block = _render_files(files, self.project.root, snapshot_hashes=snaps) if s.include_files \
            else "<!-- files omitted to save tokens -->"
        rules = [r.strip() for r in (self.project.rules or []) if r and r.strip()]
        errors_text = (errors or "").strip()
        symbol_requested = bool(getattr(s, "include_symbol", False))
        include_symbol, symbol_languages, symbol_status = self._symbol_capabilities()
        include_rules = getattr(s, "include_user_rules", True) and bool(rules)
        include_errors = getattr(s, "include_errors", False) and bool(errors_text)
        parts = ["<prompt>"]
        if s.include_system:
            parts.append(prompt_builder.render_system_block(
                include_symbol, "    ", symbol_languages))
        else:
            parts.append("    <!-- system instructions omitted to save tokens: "
                         "already known to the LLM -->")
        # If the full system block is omitted, retain its concise output rule.
        # When <system> is present, the same rule is already included there.
        if not s.include_system:
            parts.append(
                "    <output_contract><![CDATA["
                + prompt_builder.OUTPUT_CONTRACT_TEXT
                + "]]></output_contract>")
        if include_rules:
            parts.append("    <user_rules>\n        " + "\n        ".join(rules)
                         + "\n    </user_rules>")
        parts.append(f"    <task>\n        {(task or '').strip()}\n    </task>")
        if include_errors:
            parts.append(f"    <errors>\n        {errors_text}\n    </errors>")
        if s.include_files:
            parts += ["    <files>", files_block, "    </files>"]
        else:
            parts.append("    <!-- files omitted to save tokens: "
                         "previously provided files still in context -->")

        # Optional path-only project tree. This is intentionally independent
        # of <files>: users can provide structure without sending source code.
        include_project_tree = getattr(s, "include_project_tree", False)
        if include_project_tree:
            tree_xml = prompt_builder.build_project_tree(self.project.root, self.files)
            parts.extend("    " + line for line in tree_xml.splitlines())

        # Symbol targeting is included only when at least one grammar is usable.
        if include_symbol:
            parts += [
                "    <symbol_targeting available=\"true\">",
                "        <description>Tree-Sitter structural targeting for semantic precision</description>",
                "        <available_languages>" + ", ".join(symbol_languages) + "</available_languages>",
                "        <usage>Use &lt;symbol&gt; only for the available languages; otherwise use match, regex, or line targeting.</usage>",
                "    </symbol_targeting>"
            ]
        elif symbol_requested:
            parts += [
                "    <symbol_targeting available=\"false\">",
                "        <description>Tree-Sitter is not available in this installation.</description>",
                "        <usage>Do not emit symbol targets. Use match, regex, or line targeting instead.</usage>",
                "        <install_command>python -m pip install -r requirements-symbols.txt</install_command>",
                "    </symbol_targeting>"
            ]
        
        parts.append("</prompt>")
        text = "\n".join(parts)
        self.prompt_text = text
        self.snapshot_hashes = snaps
        self.state = State.PROMPT_READY
        self.save_workspace_config()
        binary_omitted = text.count("[binary file omitted:")
        self.log(f"XML prompt generated ({len(files)} files, system={s.include_system}, "
                 f"user_rules={include_rules}, errors={include_errors}, "
                 f"files_block={s.include_files}, binary_omitted={binary_omitted}, "
                 f"symbol={include_symbol}, project_tree={include_project_tree}, "
                 f"characters={len(text):,})")
        return text

    def mark_sent(self) -> None:
        if self.state == State.PROMPT_READY:
            self.state = State.SENT
            self.log("prompt copied - waiting for response")

    # -- response import (sections 49, 23) ---------------------------------------
    def import_response(self, raw_text: str) -> int:
        try:
            status, summary, notes, changes, notice = parse_response_with_note(raw_text)
        except ProtocolError as exc:
            self.state = State.INVALID
            self.log(f"parse FAILED: {exc}")
            raise
        _, fences = strip_code_fences(raw_text)
        if fences:
            self.log(f"removed {fences} markdown code fence(s) (```) from the response before parsing")
        self.response_raw = raw_text
        self.response_summary = summary
        self.response_notes = notes
        self.response_notice = notice
        self.changes = changes
        self.state = State.RESPONSE_RECEIVED
        self.log(f"response parsed: status={status}, {len(changes)} changes")
        return len(changes)

    def update_change_content(self, ch: Change, new_content: str, mark_edited: bool = True) -> None:
        """Edit the PROPOSED code of one change and refresh just its diff.

        Unlike a full re-validation this keeps every change's ticked / rejected
        state exactly as the user left it. The target (anchor) and the file are
        never touched - only what would be written."""
        from core.validator import indent_warnings
        ch.content = new_content
        ch.edited = mark_edited

        def read_original(rel: str):
            abs_path = file_manager.safe_join(self.project.root, rel,
                                              allow_external=self.project.settings.external_paths)
            return file_manager.read_text(abs_path) or ""

        if ch.valid and ch.operation not in ("delete", "rename"):
            fill_change_diffs([ch], read_original)
            if ch.is_anchored_insert:
                ch.warnings = indent_warnings(ch, read_original(ch.path))
        elif not ch.valid and ch.is_region_op:
            proposed = "\n".join("+ " + l for l in (new_content or "").splitlines())
            ch.unified_diff = ("# NOT APPLICABLE - target not located in "
                               f"{ch.path}\n# Proposed content:\n{proposed}\n")

    def validate_current_changes(self) -> List[str]:
        """Validation pipeline; returns list of human-readable problems."""
        result = validate_changes(self.changes, self.project.root,
                                  self.project.settings, self.snapshot_hashes)

        def read_original(rel: str):
            abs_path = file_manager.safe_join(self.project.root, rel,
                                              allow_external=self.project.settings.external_paths)
            return file_manager.read_text(abs_path) or ""

        fill_change_diffs(self.changes, read_original)
        # recompute diffs for changes the validator/diff engine could not
        # resolve (e.g. binary files or unlocatable targets) so the Changes
        # tab ALWAYS shows something instead of an empty preview
        for ch in self.changes:
            if ch.unified_diff:
                continue
            old = ch.old_text or read_original(ch.path)
            if ch.is_region_op and not ch.valid:
                # Target could not be located: do NOT show a misleading
                # "whole file replaced" diff - show the proposal only.
                ch.old_text = ch.new_text = old
                proposed = "\n".join("+ " + l for l in (ch.content or "").splitlines())
                ch.unified_diff = ("# NOT APPLICABLE - target not located in "
                                   f"{ch.path}\n# Proposed content:\n{proposed}\n")
                continue
            if ch.operation == "create":
                new = (ch.content or "") + "\n"
            elif ch.operation == "delete":
                new = ""
            else:
                new = ch.new_text or (ch.content or old)
            ch.old_text, ch.new_text = old, new
            ch.unified_diff = unified_diff(old, new, ch.path)
        problems = result.errors + [f"{cid}: conflict" for cid in result.conflicts]
        if result.binary_skipped:
            problems.append(
                f"NOTE: {len(result.binary_skipped)} change(s) target binary "
                "files and were skipped (only delete/rename are supported "
                "for binaries): " + ", ".join(result.binary_skipped))
        if result.conflicts:
            self.state = State.CONFLICT
        elif problems:
            self.state = State.INVALID
        else:
            self.state = State.VALIDATED
        self.log(f"validated: {len(result.ok_changes)} OK, {len(problems)} problems")
        return problems

    def clear_parsed_changes(self) -> None:
        """Discard parsed proposals/preview without deleting the pasted response.

        Never touch project files, committed history, or the original XML text.
        The original response can be parsed again from the Response tab.
        """
        self.changes.clear()
        self.response_summary = ""
        self.response_notes = ""
        self.response_notice = None
        self.state = State.RESPONSE_RECEIVED if self.response_raw else State.DRAFT
        self.log("cleared parsed changes and diff previews (response XML retained)")

    # -- preview / selection (sections 24-27) -------------------------------------
    def set_selected(self, change_id: str, selected: bool) -> None:
        for ch in self.changes:
            if ch.id == change_id:
                ch.selected = selected and ch.valid
                if not selected:
                    ch.status = "rejected" if not ch.valid else ch.status
                return

    def reject(self, change_id: str) -> None:
        for ch in self.changes:
            if ch.id == change_id:
                ch.selected = False
                ch.status = "rejected"
                self.log(f"{change_id} rejected")

    def apply_all(self) -> None:
        for ch in self.changes:
            if ch.valid:
                ch.selected = True

    def select_all(self) -> int:
        """Tick every valid change (also re-enables ones rejected earlier).
        Returns how many changes are selected afterwards."""
        n = 0
        for ch in self.changes:
            if ch.valid:
                ch.selected = True
                if ch.status == "rejected":
                    ch.status = "valid"
                n += 1
        return n

    def reject_all(self) -> None:
        for ch in self.changes:
            ch.selected = False
            if ch.valid:
                ch.status = "rejected"

    # -- apply (sections 25-29, 51) -----------------------------------------------
    def apply_selected(self, task: str = "") -> ApplyReport:
        if self.state not in (State.VALIDATED, State.PREVIEW, State.CONFLICT,
                              State.INVALID):
            # be forgiving: allow retry after fixes
            pass
        runner = TransactionRunner(self.project.root, self.project.settings,
                                   self._backups_dir(), log=self.log)
        report = runner.apply(self.changes, task=task,
                              summary=self.response_summary)
        if report.ok:
            self.state = State.APPLIED
            tx = report.transaction
            assert tx is not None
            if self.response_notice is not None:
                tx.response_note = {"type": self.response_notice.type,
                                    "text": self.response_notice.text}
            tx.response_notes = self.response_notes
            self.history.record(tx)
            self.log(f"transaction {tx.id} applied "
                     f"({len(report.applied_changes)} changes)")
        else:
            self.state = State.FAILED
            self.log(f"apply failed: {report.error}")
        return report

    def _backups_dir(self) -> str:
        if self.project.is_open():
            return os.path.join(workspace_dir(self.project.root), "backups")
        return os.path.join(STORAGE_DIR, "backups")

    # -- history / undo (sections 30-33, 60) ----------------------------------------
    def list_history(self, limit=None, offset: int = 0) -> List[Transaction]:
        return self.history.list_transactions(limit=limit, offset=offset)

    def undo_transaction(self, tx_id: str) -> Transaction:
        tx = self.history.undo(self.project.root, tx_id)
        self.state = State.ROLLED_BACK
        self.log(f"undo OK: transaction {tx_id} rolled back")
        return tx

    def redo_transaction(self, tx_id: str) -> Transaction:
        tx = self.history.redo(self.project.root, tx_id)
        self.state = State.APPLIED
        self.log(f"redo OK: transaction {tx_id} re-applied")
        return tx

    def undo_change(self, tx_id: str, change_id: str) -> Transaction:
        tx = self.history.undo_change(self.project.root, tx_id, change_id)
        self.state = State.ROLLED_BACK if tx.status == "rolled_back" else State.APPLIED
        self.log(f"single-change undo OK: {change_id} in transaction {tx_id}")
        return tx

    def redo_change(self, tx_id: str, change_id: str) -> Transaction:
        tx = self.history.redo_change(self.project.root, tx_id, change_id)
        self.state = State.APPLIED
        self.log(f"single-change redo OK: {change_id} in transaction {tx_id}")
        return tx

    def baseline_vs_current_diff(self) -> str:
        """Baseline snapshot vs the workspace as it is right now."""
        # fresh quiet scan: files may have been created / renamed since the
        # last (logged) refresh_files()
        try:
            exclude = list(self.project.exclude)
            if WORKSPACE_DIR_NAME not in " ".join(exclude):
                exclude = exclude + [f"**/{WORKSPACE_DIR_NAME}/**"]
            current = scanner.scan_project(self.project.root,
                                           self.project.include, exclude)
        except Exception:
            current = list(self.files)
        return self.history.diff_baseline_vs_current(self.project.root, current)

    def clear_log(self) -> None:
        self.log_lines.clear()

    def history_diff(self, earlier_id: str, later_id: str) -> str:
        e = self.history.get(earlier_id)
        l = self.history.get(later_id)
        if not e or not l:
            return "[transaction not found]"
        return self.history.diff_between(self.project.root, e, l)

    # -- git (section 34) ------------------------------------------------------------
    def git_status(self) -> str:
        if not self.git or not self.git.is_repo():
            return "[not a git repository]"
        return self.git.status() or ""

    # -- run command (section 61) ------------------------------------------------------
    def run(self, command: str, on_output=None, *, cwd: Optional[str] = None) -> RunResult:
        # The UI captures cwd when starting a background command. A project
        # switch must never redirect an already-queued command to a new folder.
        result = run_command(command, cwd=cwd or self.project.root or APP_DIR,
                             on_output=on_output)
        self.log(f"ran `{command}` -> exit {result.exit_code} "
                 f"({result.duration_seconds:.1f}s)")
        return result

    # -- reset -----------------------------------------------------------------------
    def reset_session(self) -> None:
        self.changes = []
        self.prompt_text = ""
        self.response_raw = ""
        self.snapshot_hashes = {}
        self.response_summary = ""
        self.response_notes = ""
        self.response_notice = None
        self.state = State.DRAFT
        self.log("session reset")
