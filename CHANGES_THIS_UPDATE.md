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
