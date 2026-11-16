# Removal of the prompt-file content cache (2026-11-02)

## Reason

The Phase 2 in-memory LRU cache saved a few milliseconds only when generating the
same prompt from unchanged files repeatedly. For most ordinary source-file prompts,
these savings are negligible compared with building and using the full prompt.
The extra cache state, file signatures, invalidation, and fallback handling also
created an avoidable class of reliability failures.

Local microbenchmark (Linux container, warm OS cache, median of 30 repeated reads):

| Files | Warm application cache | Direct disk reads |
|---|---:|---:|
| 8 × 32 KiB | ~0.04 ms | ~0.27 ms |
| 8 × 1 MiB | ~0.04 ms | ~7.69 ms |

These are illustrative file-read times, **not** end-to-end GUI timings or Windows benchmarks.
A large project may show a bigger repeated-read difference, but direct reads favor
freshness and eliminate application-level cache complexity.

## Changes

- Deleted `core/file_cache.py` including `FileContentCache`, `PROMPT_FILE_CACHE`,
  its lock, its 64 MiB allocation limit, and cache invalidation APIs.
- Added `file_manager.read_prompt_snapshot()` which opens the selected file anew,
  captures raw SHA-256 and content from one file handle, and retries once if the
  file changes during reading. It also detects path replacement after the read.
- Maintained compact binary omission by default and explicit binary-base64 opt-in.
- Maintained `PromptFileUnavailableError` with explicit read errors. A missing
  file is **never** rendered as `[file does not exist yet]`.
- Kept `file_manager.read_text_any()` as a compatibility helper, now using a fresh read.
- Replaced cache-specific tests with direct-file-read and change-visibility regressions.
- Retained unrelated caches (history ordering, search text offsets, syntax lexers,
  Tree-Sitter parsers). Backup content deduplication is also unchanged.

## Run checks

    python -m compileall -q .
    python -m unittest discover -s tests -v

Before using on important projects, test a fresh launch, selected-file persistence,
file-edit-then-regenerate prompt, and XML Apply/Undo/Redo on a disposable project.
The previously audited transaction-safety issues are not addressed by this update.
