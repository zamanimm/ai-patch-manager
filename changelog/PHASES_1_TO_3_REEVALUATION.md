# AI Patch Manager — Re-evaluation of Phases 1–3

This release starts from `AI_Patch_Manager_No_Prompt_Cache.zip`. It is a targeted
simplification, not a rewrite of the app. User-requested features (project tree,
XML-only instructions, editing proposed code, clear parsed changes, Open Folder,
layout saving, persisted selections, and diff colors) remain available.

## Decision rule

Remove a feature when its gain is small in typical usage, while its added
I/O, memory, maintenance burden, or failure modes are significant. Preserve
reliability fixes and bounded-work protections even if their benchmark savings
are small; they prevent crashes, data loss or UI stalls rather than merely
shaving milliseconds.

## Phase 1 — decisions

| Change | Decision | Reason |
|---|---|---|
| Background manual rescanning | KEEP | Prevents a visible freeze on large project trees; retain stale-result rejection and startup scan required for saved selections. |
| Per-cell mode editing instead of a widget per file | KEEP | Avoids hundreds/thousands of persistent Qt widgets in the Project tree. |
| Delayed workspace-selection saves + atomic config | KEEP | Avoids repeated disk writes during bulk selection; flushes on close/switch and protects config integrity. |
| Bounded file previews | KEEP | Memory-protection feature for large files. |
| Bulk diff text rendering | KEEP | Avoids per-line QTextCursor insertions and supports correct full-line diff colors. |
| Skip standard generated folders | KEEP | Prevents expensive scans of node_modules, virtualenv and build folders. |

## Phase 2 — decisions

| Change | Decision | Reason |
|---|---|---|
| Content-addressed backup blobs + hardlinks | REMOVE for new snapshots | Significantly more complex and slower for the tested small-file workload; storage savings matter mainly for large, repeated identical snapshots. Existing blob-based snapshots remain readable. |
| LRU content cache for prompt files | ALREADY REMOVED | Previous release replaced this with direct, fresh disk reads to avoid stale/misidentified files. |
| Approximate token estimator | REMOVE | Counting does not reduce prompt size, is model-dependent and costs an extra scan; show character count and a conservative large-prompt notice instead. |
| Binary content omitted unless user opts in | KEEP | Actually reduces prompt size and avoids accidental base64 payloads. |
| Bounded streamed command output | KEEP | Prevents unbounded memory and shows output while the command is running. |
| Append-only history journal + periodic compatibility snapshot | REMOVE | Normal workloads do not benefit visibly from the extra format and synchronization; one atomic JSON file is easier to inspect and recover. Old JSONL records are safely merged and archived. |
| History pagination (200 entries at a time) | KEEP | Avoids unnecessary rendering when histories become long; the API remains unchanged. |
| Raw-byte hash verification | KEEP | Correctness and undo/redo integrity for binary and CRLF files. |

## Phase 3 — decisions

| Change | Decision | Reason |
|---|---|---|
| Debounced search | KEEP | Avoids rescanning long text on every keystroke. |
| Cached text and Unicode offsets during search | KEEP | Important for repeated navigation in long documents, especially with UTF-16/emoji offsets. This is not the removed prompt-content cache. |
| Binary-search match navigation | KEEP | No substantial new maintenance complexity, useful with many matches. |
| `CommandStore` module + atomic writes | KEEP | Reliability/isolation improvement, not a questionable performance cache. |
| UI error diagnostics and project isolation | KEEP | Helps trace real failures and prevents cross-project confusion. |
| Regression tests | KEEP / EXPAND | Protects baseline, migration and undo/redo. |
| Lazy guide (README) rendering | REMOVE | A small Markdown file does not justify extra first-open loading state. |
| Lazy refresh of non-active project tabs | KEEP | Reduces refresh work during project switching. |

## Extra precaution when simplifying backups

The old backup naming algorithm could map two paths to the same snapshot file
(e.g. `a/b__c.py` and `a__b/c.py`). New snapshots have a readable basename
plus the full SHA-256 of the normalized relative path, eliminating that
collision. Restore and redo still search for legacy flat filenames when new
ones do not exist. **Already overwritten old snapshots cannot be repaired**.

## History migration and backward compatibility

1. Before first launch, copy the entire existing `.ai-patch-manager/` folder.
2. If `history.jsonl` exists, load the current `history.json` and replay the
   journal (latest record for each id wins, including undo/redo statuses).
3. Write a complete `history.json` using a same-directory temporary file,
   `fsync`, and atomic `os.replace`.
4. Only then rename the journal to `history.jsonl.migrated-<id>`; no archival
   history is automatically deleted. New events only update `history.json`.
5. Existing `.orig`/`.new` hard-linked snapshots and old `.blobs` remain
   untouched. New snapshots use simple direct copies (no new blob directory).

Do not manually delete old `.blobs` or archived journals until the entire
project and its undo/redo history have been separately backed up and verified.

## Benchmarks (synthetic Linux environment, not Windows UI benchmarks)

- **250 files (~4 KB each), 3 snapshots (750 file copies)**:
  Phase 2 shared blobs 0.816 s / about 1.03 MB physical data;
  original Phase 1 direct copies 0.139 s / about 3.08 MB;
  simplified direct copies 0.097 s / about 3.08 MB.
- **120 history records**:
  previous JSONL journal 0.058 s; simple atomic JSON 0.166 s.
  The ~0.1 s difference over 120 writes is generally not visible; extremely
  large histories may favor the previous format.
- **425,000-character sample prompt**:
  approximate token estimation median 30.9 ms per call; `len()` is effectively
  instantaneous and shows an unambiguous character count.

Timings are environment-specific single runs; they do not establish universal
speedups. Safety and UI tests on Windows are still needed before production use.

## Regression checks

Run from the extracted source directory:

```bash
python -m compileall -q .
python -m unittest discover -s tests -v
```

Key extra tests: old journal status migration, a complete old snapshot
undo/redo after migration, old flat-backup compatibility, distinct-path backup
names, backups stored without blob hardlinks, preserving old `.blobs`,
atomic-write failure handling, and protection against corrupt history overwrites.

## Unresolved earlier audit findings (not fixed by this performance cleanup)

- Undo or redo may still overwrite newer manual edits in normal patched files.
- Multi-file rollback is not guaranteed if `Backup Before Apply` is disabled.
- Pure rename can rewrite binary data instead of preserving source bytes.
- Apply may overwrite a file created between validation and execution.
- A baseline that failed to back up some files can still be considered complete.
- Normal text edits may normalize CRLF and change executable permissions.
- GUI tests are not executed here because PySide6 is unavailable.

**Use a copy of the project for testing and keep an independent Git or filesystem
backup; this build has not been certified as safe for production patching.**
