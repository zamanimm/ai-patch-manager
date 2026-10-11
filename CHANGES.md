# AI Patch Manager — Bug Fixes, Regex Feature & Simplifications

## New Documentation: Architecture & Philosophy

**`ARCHITECTURE.md`** — Comprehensive explanation of why AI-Patch-Manager uses regex-based targeting instead of AST/Tree-sitter:

- **The core principle:** LLM generates precise patterns for the source it received; application validates deterministically
- **Three targeting modes:** Exact match (safest), line ranges (when known), regex (structural refactoring)
- **Safety model:** LLM proposes → app validates → user approves → app applies
- **Strict validation:** 0 matches = reject, 2+ matches = reject (never silently guess)
- **Language-agnostic:** Works for any language via LLM-generated patterns, not language grammars
- **Intentional limitations:** Regex is not a universal parser; it's sufficient for the intended workflow

---

## New Feature: Regex Target Mode ✨

**Replace-all with backreferences** for structural changes:

```xml
<change id="chg-002" path="file.py" operation="replace">
    <target>
        <regex>(\w+)_old</regex>
    </target>
    <replacement>\1_new</replacement>
    <reason>Rename all occurrences</reason>
</change>
```

- **Three target modes**: `<match>` (exact) | `<regex>` (pattern + backrefs) | `<line_start>` (range)
- Regex uses Python `re.MULTILINE`, supports `\1`, `\2`, `\g<name>` backreferences
- Applied before line-level operations, so line numbers stay valid
- Intended for: variable renames, import fixes, repeated patterns

---

## Simplifications

- **Removed template system** — XML is the standard format; templates added unnecessary complexity
- **Removed project name display** — only the root path matters

---

## Bug Fixes

### `ui/main_window.py`
- **Critical fix**: `run()` now imports `QApplication` directly, matching the
  module-level import. The original version called a local helper
  (`QApplication_instance`) whose import lived in an unreachable scope.
- `_tab_changed` now also refreshes the **Changes** tab when the user
  navigates to it, so the list is never stale after an apply or import.
- State label in the status bar is now **colour-coded** (green = applied/ok,
  blue = in-progress, red = error) for at-a-glance feedback.
- Log sync uses the same 2 000-line cap as the Controller (was 400).
- `cmd_input.returnPressed` wired to Run so Enter works in the command field.

### `ui/changes_view.py`
- **Critical fix**: `_dry_run` now calls `ctrl._backups_dir()` (always fresh)
  instead of `main.BACKUPS_DIR`, which was set once at startup and became
  stale after opening a different project.
- `reload()` preserves the current list selection so the diff panel stays
  visible after a checkbox toggle — no more diff disappearing on every click.
- Added **"Reject All"** button for convenience.

### `ui/diff_view.py`
- Diff backgrounds are now **theme-aware**: soft greens/reds in light mode,
  dark greens/reds in dark mode. The original hardcoded light-only hex values
  made the diff invisible on dark-theme desktops.

### `ui/history_view.py`
- **Python builtin shadow fix**: variable `l` renamed to `later_tx` — `l`
  shadows the built-in `list` and causes subtle bugs under some linters/runtimes.
- After a successful undo, `_sync_log()` is called so the Log dock updates.

### `ui/prompt_view.py`
- `reload()` now restores **last_task** and **last_errors** from the workspace
  config, so text typed before closing the app reappears on next launch.
- Added **Ctrl+G** keyboard shortcut for "Generate XML Prompt".

### `ui/project_view.py`
- `_sync_selection()` now calls `ctrl.save_workspace_config()` so file
  checkbox states persist across restarts (original lost them every time).
- `_mode_changed()` also saves immediately.
- Added **"Select All"** and **"Deselect All"** buttons.
- Project label uses plain text to prevent HTML injection from project paths
  containing `<`, `>`, or `&`.

### `core/controller.py`
- `_attach_workspace()` now reads back **`last_task`** and **`last_errors`**
  from the saved config so `PromptView.reload()` can restore them.

## New files
- `requirements.txt` — lists `PySide6>=6.5` and `lxml>=4.9`.
