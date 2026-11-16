# Latest update: remove prompt-content cache (2026-11-02)

- Removed `core/file_cache.py`, its in-memory LRU and all cache-invalidation hooks.
- Prompt builder reads actual file bytes on every generation and calculates SHA-256
  from the same open handle. No cached content can appear in newly generated prompts.
- File-not-found, access-denied, and concurrent-change errors remain explicit.
- Binary file detection uses the actual NUL byte, and large binary files are hashed
  in chunks without adding their contents to the prompt unless base64 is requested.
- Project file selection persistence, Project Tree option, UI controls, and
  unrelated optimizations are unchanged.

Earlier update notes follow (historical):

# Phase 3 — reliability, maintainability and startup optimization

See `PHASE3_OPTIMIZATIONS.md` for implementation details, test results, and Windows smoke tests.

Highlights:
- Debounced text search with reusable text/UTF-16 offset caches and faster navigation.
- Qt-independent saved-command storage with atomic writes and workspace isolation.
- Recoverable UI error reporting with bounded traceback logs.
- Delayed guide loading and on-demand project tab refresh; no eager refresh of inactive tabs.
- Project-specific inputs reset on project switches; running commands keep the directory captured at launch.
- Additional regression coverage, including simulated multi-file rollback on write failure.

Phase 1 and Phase 2 improvements and all original features are included.


## Project tree prompt option (2026-11-01)

- Added a workspace-persisted `include_project_tree` setting, unchecked by default.
- Added Prompt tab checkbox: **Project tree (paths only)**.
- When enabled, generated XML includes a hierarchical `<project_tree>` block with folder and file names only. It does not read or include file contents and is independent of the `<files>` content checkbox.
- Tree uses the controller's current scanned file list, so the project's include/exclude rules are respected. Refresh the Project tab before generating if the filesystem structure has changed since the last scan.
- Added XML/dictionary settings persistence and regression tests for default-off behavior, escaping, unsafe path rejection, and no-content guarantees.
- The **Clear Parsed Changes** button already existed in the Changes tab; it remains available with its confirmation dialog and preserves the original Response XML and applied history.

## Prompt instruction cleanup

- Consolidated the code/file-edit XML-only behavior into one rule; removed repeated output-only/no-change instructions.
- Changed the Instructions tab protocol from many escaped `<instruction>` entries to one readable CDATA `<instructions>` block.
- Rendered the response schema as real nested XML under `<response_schema>` instead of escaped `&lt;...&gt;` text.
- Kept one output contract in generated prompts: full system instructions when enabled, compact contract when disabled.
- Updated Instructions tab guidance and added XML/CDATA regression tests.


## Prompt file read reliability fix

- Fixed misleading `[file does not exist yet]` output for selected existing files when the prompt cache encounters an I/O failure.
- Prompt generation now uses strict reads and aborts with a path-specific error instead of silently omitting source content.
- Added regression coverage for missing files and read-permission failures.


## Prompt file-read fallback (2026-11-02)

Compared the supplied known-good `ai-patch-manager.zip` with the optimized build.
The working version reads selected files directly; the optimized build used the
prompt cache and could turn a cache miss into a misleading not-yet-created placeholder.
Prompt generation now uses the cache first and falls back to a direct, uncached read.
Actual missing or inaccessible files still stop prompt generation with a specific error.
See `PROMPT_FILE_READ_FALLBACK.md`.

# Transaction and single-change rollback (2026-11-16)

- History now shows each recorded `chg-...` item beneath its file row.
- Added **Undo Selected Change** and **Redo Selected Change** alongside full **Undo Transaction** and **Redo Transaction**.
- New Apply records persist the declarative Change payload and the matching settings needed to rebuild a file from its original snapshot while excluding/reintroducing one selected change.
- Partial rollback updates `partially_rolled_back` / `rolled_back` state and records per-change events. The latest hybrid storage update reconstructs History previews from `.orig` plus active Change records rather than storing a persistent full-file `.current` copy.
- Every transaction and per-change undo/redo verifies current SHA-256, file permission mode, backup integrity, and destination collisions before writing. Writes are guarded by exact recovery copies; if file or history persistence fails, the pre-operation state is restored.
- Apply now uses an ephemeral raw-byte guard when persistent backups are disabled, so a mid-batch failure can still be rolled back. If rollback itself fails, the report is marked `CRITICAL`, snapshots are retained, and the app does not claim success.
- Rename-only file-level operations no longer decode and rewrite binary files.
- Legacy history remains readable. Older transactions without saved Change definitions support whole-transaction undo/redo only; individual Change rollback is intentionally disabled.
- Added core regression coverage for multiple changes per file, multiple files, create/delete/rename, binary rename, partial/full undo-redo combinations, manual edits, permission changes, backup absence, history-write failures, and incomplete Apply rollback.
- See `SINGLE_CHANGE_ROLLBACK.md` for behavior and safety limits.

Verification for the 2026-11-16 rollback update:
- `python -m compileall -q .` passed.
- `python -m unittest discover -s tests -q`: 115 tests discovered, 96 passed, 19 skipped (PySide6 GUI dependency unavailable in this build environment).
- The skipped tests include the new History tree/action UI checks; the UI module compiled successfully but needs Windows/PySide6 smoke testing.


# Hybrid rollback storage optimization (2026-11-16)

- Retained one immutable, byte-exact original snapshot per existing file.
- Added a verified line-based byte delta for Redo when the delta is materially
  smaller than a full post-Apply snapshot; otherwise uses the full `.new` fallback.
- Reuses the original snapshot for unchanged rename-only files and uses a delete
  marker for deleted files. New files still receive a `.new` snapshot.
- Removed persistent full-file `.current` snapshots for new per-change Undo/Redo
  operations. Partial History previews are reconstructed from original snapshots
  and active Change records; temporary guard snapshots remain for failure recovery.
- Preserved compatibility with older `.new` and `.current` snapshots and older
  history records without `redo_strategy`.
- Added tests for exact delta Redo, tampered-delta refusal before writes, snapshot
  fallback, partial-preview reconstruction, and legacy compatibility.
- See `HYBRID_ROLLBACK_OPTIMIZATION.md` for details and measured storage example.

Verification for the hybrid rollback update:
- `python -m compileall -q .` passed.
- `python -m unittest discover -s tests -q`: 121 tests discovered, 102 passed, 19 skipped (PySide6 GUI dependency unavailable).
- Synthetic ~310.9 KB source example: 359-byte delta, approximately 49.9% less total per-file transaction backup data than `.orig` + `.new`; exact Undo/Redo hash verification passed.
- Corrupt delta is rejected before any workspace write; large replacements fall back to a full snapshot.


## 2026-11-16 — Optional Tree-Sitter and QtPy provider support

- Removed Tree-Sitter and all language grammar distributions from the mandatory `requirements.txt`.
- Added `requirements-symbols.txt`; Symbol targeting is disabled with an install hint unless the Tree-Sitter core and at least one supported grammar can actually construct a parser. The saved per-project preference is preserved while unavailable.
- Prompt generation now advertises only grammars available in the active environment. If Symbol was requested but unavailable, generated prompts explicitly tell the model not to emit `<symbol>` targets and retain match / regex / line targeting.
- Replaced all application UI imports from PySide6 with QtPy. PySide6 is preferred when both providers are installed; PyQt6 is selected when it is the only installed provider. `QT_API=pyside6` / `QT_API=pyqt6` can explicitly select a provider before startup. The loader does not switch providers after Qt import begins.
- Added provider-specific install bundles (`requirements-pyside6.txt`, `requirements-pyqt6.txt`), shared `requirements.txt`, and optional `requirements-highlighting.txt`.
- Updated startup diagnostics, icon generation, tests, README and dependency documentation.
- Validation: 138 tests discovered; 119 passed and 19 GUI tests skipped because this environment has neither PySide6 nor PyQt6/QtPy installed. Python compilation passed. The missing-Qt startup path displays actionable install instructions.
- Actual desktop GUI verification under both Qt providers remains required on separate Windows environments.


## Startup compatibility correction

- Fixed a regression where an existing PySide6 installation was rejected if the separate `qtpy` package was absent.
- PySide6 is now loaded directly when QtPy is unavailable; an in-memory `qtpy.*` import namespace preserves the existing UI imports.
- `requirements.txt` no longer installs or requires QtPy.
- `requirements-pyside6.txt` installs PySide6 without QtPy.
- The PyQt6 bundle continues to include QtPy for scoped-enum and signal compatibility.
- If both bindings are installed, PySide6 remains the default. Explicit `QT_API=pyqt6` remains respected and is not silently overridden.
- Added regression tests for existing PySide6 + no QtPy, broken QtPy fallback, and provider-specific requirements.
