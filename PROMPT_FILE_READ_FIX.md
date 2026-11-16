> **Historical note:** Prompt-file caching was removed in the 2026-11-02 update. See `PROMPT_CACHE_REMOVAL.md`.

# Prompt file read fix

## Problem

The prompt renderer treated every `None` returned by the prompt file cache as if the selected file did not exist yet. The cache returns `None` for several unrelated failures (for example: missing path, permission denied, file changed during reading, or an I/O error). This could silently replace the contents of a selected existing file with the misleading placeholder `[file does not exist yet]`.

## Fix

- Prompt generation now requests strict cache reads.
- Missing files, permission errors, unsafe paths, and other I/O failures produce a clear `PromptFileUnavailableError`.
- Prompt generation stops rather than sending an incomplete or misleading prompt.
- Error messages identify the selected relative path and, where applicable, the project root or absolute file path.
- The default cache API remains backward-compatible for non-prompt callers (`strict=False`).

## User action

If a selected file cannot be read, the prompt is not generated. Refresh the Project tab and select the file again. If the error reports access denied or another I/O failure, check file permissions, locks, and the project root.

## Regression tests

Added tests for strict cache behavior, a selected file disappearing after scan, and an existing but unreadable file. The tests ensure the application no longer labels all read failures as a not-yet-created file.
