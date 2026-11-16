> **Historical release note.** Some Phase 2/3 optimizations described here were later removed. See `PHASES_1_TO_3_REEVALUATION.md` for the current implementation.

# Phase 1 optimization changes

This release is based on `AI_Patch_Manager_Wrap_Layout_Fix.zip`.

## Implemented

- **Non-blocking manual file rescans**: `Rescan Files` runs `scanner.scan_project()` in a Python worker thread, emits the result to the Qt GUI thread, disables the button until completion, and rejects stale results from a previous project. Project opening and the *mandatory pre-patch baseline backup* remain synchronous and unmodified for correctness.
- **Lighter project explorer**: replaced a persistent `QComboBox` per file with a `QStyledItemDelegate` editor instantiated only when editing the Mode cell. Kept the existing tree, selection semantics, and double-click actions. Batched tree UI repaints and enabled uniform row heights.
- **Debounced settings writes**: file selection/mode changes and prompt/Instructions option toggles are coalesced with 450 ms Qt single-shot timers; pending changes are flushed on project switch and application exit. Workspace `config.json` now uses a same-directory temporary file, flush/fsync and atomic `os.replace()` to protect against partial writes.
- **Bounded file previews**: project-file previews read a limited prefix rather than loading the entire file into RAM, retain UTF-8 replacement behavior, detect binary files, and display a truncation notice. Underlying file reads needed for patch application, validation and prompt building remain unmodified.
- **Batch diff rendering**: diff display now performs one `setPlainText()` rather than one `QTextCursor.insertText()` per line. A `QSyntaxHighlighter` provides unified-diff line colouring and retains optional syntax-token coloring.
- **Scanner cleanup**: directory traversal now applies the previously declared `ALWAYS_SKIP_DIRS` (including `node_modules`, venvs, etc.).

## Verification and limits

- `python -m compileall -q .`: pass.
- `python -m unittest discover -s tests -v`: 2 non-GUI tests passed; 5 GUI tests skipped because PySide6 is not installed in the build environment.
- Desktop UI integration and timing need verification on a Windows machine with `pip install -r requirements.txt`.
- Large code-index/model replacement, fully threaded initial project-open/backup/validation, and streaming diff computation are **not** included. Only the manual scanning step is off the UI thread; baseline snapshot behavior is intentionally unchanged.

## Suggested smoke check (Windows)

1. Start the app and open a small test project; verify backup was created before applying any patches.
2. Select files, change mode by clicking the Mode cell, and restart; verify selections and modes persisted.
3. Click `Rescan Files`, including on a directory with many files; ensure the UI stays interactive and selection remains intact.
4. Open a multi-megabyte text file from the Project tab; verify truncated preview and usable controls.
5. View a unified diff and verify green additions, red removals and syntax highlights.
6. Open another project during a manual rescan; confirm stale results do not overwrite the new tree.
