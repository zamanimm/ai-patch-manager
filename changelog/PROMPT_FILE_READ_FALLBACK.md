> **Historical note:** Prompt-file caching was removed in the 2026-11-02 update. See `PROMPT_CACHE_REMOVAL.md`.

# Prompt file-read fallback fix

## What changed

The known-good `ai-patch-manager.zip` reads selected source files directly from disk.
The newer optimized builds introduced `PROMPT_FILE_CACHE`; earlier prompt rendering
treated a cache miss as if a selected file did not exist and inserted the misleading
`[file does not exist yet]` placeholder.

The updated build keeps the cache for normal operation, but prompt generation now
falls back to a direct, uncached read whenever the cache returns no snapshot or
raises a read/metadata error.

## Safety behavior

- Existing, readable files are included from their actual contents even after a cache miss.
- The fallback reads from a single open file handle and retries once if metadata changes
  during the read.
- SHA-256, byte size, binary detection, and the binary opt-in behavior are preserved.
- A genuinely missing, inaccessible, unsafe, or repeatedly changing file produces a
  specific `PromptFileUnavailableError`.
- No selected file is silently replaced with a fake "not yet created" placeholder.

## Regression coverage

- Eight selected project files are rendered correctly when the cache is forced to miss.
- A transient cache error falls back to direct reading.
- A real permission failure remains an explicit error.
- A file deleted after scanning remains an explicit missing-file error.
- Existing binary, hashing, and cache-invalidation tests remain in place.

## Verification

- Python compilation: passed.
- Automated tests: 53 tests discovered; 39 passed and 14 GUI-dependent tests skipped.
- GUI tests require PySide6 on Windows and were not run in this environment.
