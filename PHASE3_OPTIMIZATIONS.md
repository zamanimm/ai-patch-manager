> **Historical release note.** Some Phase 2/3 optimizations described here were later removed. See `PHASES_1_TO_3_REEVALUATION.md` for the current implementation.

# Phase 3 — reliability, maintainability and startup

Built **on top of Phase 2**, preserving its backup deduplication, prompt cache, streamed command output and append-only transaction history.

## Implemented

### 9. Search responsiveness
- Added 200 ms debounce for find text, case/whole-word/regex toggles and document changes.
- F3, Shift+F3 and Find Next/Previous run pending searches immediately; no delayed navigation.
- Cache plain text and UTF-16 offset mapping until the editor content changes.
- Use binary search rather than linear search to locate the next, previous and selected match.
- Existing 20,000 navigable-match / 2,000-highlight limits are retained.

### 12. Code structure
- Added `core/command_store.py`, a Qt-independent service for per-workspace saved commands and last-run command.
- Command saves use same-directory temporary files, `fsync` and atomic `os.replace`. Corrupted JSON is not overwritten silently.
- Existing `.ai-patch-manager/saved_commands.json` remains compatible, including unknown fields.
- Centralized tab refresh dispatch in `MainWindow._refresh_tab`.

### 13. Error handling and workspace isolation
- Added `core/diagnostics.py` for per-user, bounded traceback logging at `~/.ai-patch-manager/ui_errors.log` (or the `AI_PATCH_MANAGER_HOME` directory).
- Tab refresh errors are now logged and surfaced in the status bar rather than discarded.
- Failures when loading/saving saved commands, exporting a prompt/instructions, importing a response file or opening a project receive visible errors.
- Scan failures are logged and displayed.
- On switching projects, old task/error/response/prompt/Run input is cleared to prevent accidental reuse; only the new workspace's stored command list loads.
- A background Run command captures the project directory at launch. Switching projects does **not** redirect it to a new folder. Stale output from the previous workspace is ignored. The original command can continue to run in the original directory until it completes or reaches its timeout.

### 14. Regression tests
- Added `tests/test_phase3.py`: commands (atomicity, failure preservation, legacy compatibility, workspace isolation, capped list), diagnostics (traceback and log bound), batch patch rollback, captured command cwd, GUI find debounce and unicode replace, and lazy guide loading.
- Run all suites: `python -m unittest discover -s tests -v`.

### 15. Startup and project switching
- The How-to-Use tab no longer opens/parses/renders README Markdown at window creation; it loads on first visit.
- Opening a project refreshes the visible tab and Project file list. Other project-dependent tabs are refreshed when they become visible rather than all at once.
- **Intentionally unchanged:** initial safety-critical baseline backups and initial scans remain synchronous. This avoids changing patch safety assumptions; very large projects may still briefly block during initial opening.

## Verification in the build environment
- `python -m compileall -q core ui main.py`: passed.
- `python -m unittest discover -s tests -q`: **32 tests total, 24 passed, 8 skipped** because PySide6 was not installed here.
- Batch apply rollback test explicitly simulates the second file failing and checks that original CRLF bytes of both files are preserved.
- No claim of a measured speedup is made without on-device benchmarking.

## Windows smoke tests before replacing the current app
1. Extract this archive into a **new folder** and run `pip install -r requirements.txt`.
2. Start `AI Patch Manager.pyw`. Confirm the How-to-Use tab loads only when selected.
3. Open a **copy** of a real project; switch among Project, Prompt, Response, Changes, History and Run tabs.
4. Search a long document. Type rapidly, use F3 and Shift+F3; confirm results, replacements and the Wrap Text setting still work.
5. Save/delete a Run command, close and restart the app. Confirm saved and previous commands are scoped to their own project.
6. While a long-running command is in progress, switch to another project. Confirm old command output does not appear in the new project's Run tab, while the old command may continue in its original directory.
7. Apply a patch to a throwaway project, then Undo and Redo. Confirm the files are byte-for-byte restored as expected.
8. Check the log dock and the per-user `ui_errors.log` if a recoverable error appears.

## Compatibility / limitations
- No workspace schema or transaction format migration is required for Phase 3.
- Phase 1 and 2 functionality is included.
- PySide6 GUI tests and an actual graphical Windows run were **not** performed in the build environment; they are included for local execution.
