> **Historical release note.** Some Phase 2/3 optimizations described here were later removed. See `PHASES_1_TO_3_REEVALUATION.md` for the current implementation.

# Phase 2 optimization update

Built on `AI_Patch_Manager_Phase1_Optimized.zip`. This update focuses on storage efficiency, repeated file reads, prompt size, command output, and long histories.

## Implemented

### 1. Content-addressed backups
- Original (`.orig`) and redo (`.new`) snapshots are still available at the same per-transaction paths expected by undo/redo.
- New snapshot content is stored once under `backups/.blobs/<sha256>` and linked into snapshots when hard links are supported. Filesystems without hard-link support fall back to byte copies.
- Baseline snapshots and apply-time backups share identical blobs.
- Added `storage_stats()` and `cleanup_unreferenced_blobs(dry_run=True)`. Cleanup is dry-run by default; no automatic backup deletion is performed.
- Compression is intentionally not enabled: shared raw-byte blobs preserve fast, byte-exact restore and avoid CPU cost.

### 2. Prompt file cache (removed in latest version)

> Historical notes below describe the original Phase 2 implementation. The prompt-file
> cache was subsequently removed. See `PROMPT_CACHE_REMOVAL.md`.

- Added a thread-safe LRU cache (64 MiB total, 4 MiB per entry) keyed by normalized path and file metadata (size, modified time, changed time, inode).
- Prompt generation reuses file text and SHA-256 hashes rather than reading selected files twice.
- Normal file writes, copies, deletes and renames invalidate affected cache entries. Metadata changes also invalidate cached entries.
- The cache is deliberately not used by validation, apply, undo or redo; safety-critical paths continue reading the live filesystem.

### 3. Prompt/token optimization
- Binary files are represented by a compact size + SHA-256 marker by default; their full contents are not base64-expanded into prompts.
- Prompt tab includes an explicit **Binary files (base64)** opt-in for workflows that genuinely need binary payloads. The setting persists per workspace and in app XML settings.
- Added a dependency-free token estimator and a warning for prompts estimated at 80,000 tokens or more. Counts are approximate and model/tokenizer dependent.
- Binary files in `diff`, `selection` and `context` modes are handled as binary assets rather than decoded as text.

### 4. Command output streaming
- stdout and stderr are drained concurrently from pipes and forwarded progressively to the Run tab.
- Only the last 1 MiB per stream is retained in the returned result. The live text view is capped at approximately 2 MiB and trims older content in batches.
- Reader threads use a bounded queue; output is not accumulated in unbounded temporary log files.
- Command exit code and elapsed time are displayed after completion.

### 5. History storage and retrieval
- Existing `history.json` or legacy per-transaction history is imported automatically into `history.jsonl`; original data is not deleted.
- New transaction records and undo/redo status changes append to the journal instead of rewriting the entire history file on each operation. A partial trailing journal line is ignored safely on read.
- An in-memory id index and cached timestamp ordering speed repeated lookup/list operations.
- A compact `history.json` compatibility snapshot is written after the first record, every 25 unique transactions, and after undo/redo status changes.
- History tab loads 200 recent transactions at a time; **Load Older** fetches earlier pages. Diff selection asks the index for the immediately older transaction rather than materializing the full history list.

### 6. Raw-byte integrity correction
- New transactions record original and resulting hashes from raw file bytes, not decoded text, and mark the hash format in each file record. This makes undo/redo integrity checks correct for CRLF files and binary files.
- Existing transactions without a hash-format marker are treated as legacy text-normalized records and retain a compatibility verification path; new raw hashes are verified strictly.

## Verification

Commands run from the project root:

```bash
python -m compileall -q .
python -m unittest discover -s tests -v
```

Results in this build environment: **15 core tests passed** (Phase 1 + Phase 2); **5 GUI tests skipped** because PySide6 is not installed here. Phase 2 tests cover blob deduplication, byte-exact restore, CRLF undo/redo, dry-run cleanup, JSON-to-JSONL migration, journal updates, pagination, prompt caching/invalidation, binary prompt omission/opt-in, token estimation, settings persistence, and progressive bounded command output.

## Windows smoke test recommended

1. Open a copy of a real project and confirm the baseline is created. Check that `backups/.blobs/` appears under `.ai-patch-manager/`.
2. Apply a small patch, undo it, then redo it. Confirm the file bytes are restored exactly.
3. Generate a prompt with a `.png`, `.pdf` or other binary file selected. Confirm the prompt contains size/hash metadata, not a base64 payload. Enable **Binary files (base64)** and confirm it is included only when checked.
4. Generate the same prompt twice without changing files; confirm it remains identical and the prompt cache is reused. Modify a file and regenerate; confirm the updated content is used.
5. Run a command that prints output gradually and another that prints several megabytes. Confirm output appears while running and the Run tab remains responsive with old output trimmed.
6. Open a workspace with more than 200 history entries. Confirm the newest page loads first and **Load Older** fetches earlier entries.
7. Open a workspace with an existing `history.json`. Confirm it is migrated to `history.jsonl` and all transactions remain visible.

## Notes

- GUI interaction still needs validation on Windows with the installed PySide6 runtime.
- The initial baseline capture and apply-time backup remain synchronous for safety; Phase 2 optimizes their storage footprint, not their execution thread.
- The compatibility `history.json` snapshot may lag the journal between snapshot intervals. The current application treats `history.jsonl` as the source of truth after migration.
- Do not manually delete `.blobs`, `.orig`, `.new`, or journal files. The cleanup helper is conservative and dry-run by default.
