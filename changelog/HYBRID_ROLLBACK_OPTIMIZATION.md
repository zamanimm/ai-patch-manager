# Hybrid rollback storage optimization

## Design

New transactions retain one immutable, byte-exact `.orig` snapshot per existing file. Redo data is stored using the smallest safe representation:

- `delta`: a versioned byte-edit script based on the exact `.orig` bytes;
- `original`: reuse `.orig` when the post-Apply bytes and permission mode are unchanged (typically rename-only);
- `delete`: a marker that Redo removes the file;
- `snapshot`: a complete `.new` file when a delta is not materially smaller, the file is new, the file is too large for delta generation, or delta generation is unsuitable;
- `unavailable`: backups were disabled, so persistent transaction Redo is not promised.

The delta is generated at line granularity and converted to exact byte offsets. This avoids the pathological cost of byte-wise `SequenceMatcher` on repetitive files and preserves original byte sequences, including line endings. Delta generation is attempted only for files up to 2 MiB and 20,000 lines. It is accepted only when its encoded size is at most 70% of the post-Apply file size. Otherwise the manager stores a normal `.new` snapshot.

## Redo safety

Before Redo, the manager verifies:

1. the current workspace file matches the recorded current hash and permission mode;
2. the `.orig` snapshot matches the recorded original raw SHA-256;
3. the delta artifact is a supported version and contains bounded, ordered, non-overlapping byte ranges;
4. the delta base hash/size match `.orig`;
5. the reconstructed bytes match the exact recorded post-Apply SHA-256 and size;
6. the restored file is written atomically and its final hash/mode are checked.

A missing, malformed, or tampered delta aborts Redo before the workspace is modified. The original snapshot is never replaced by a delta.

## Per-change Undo/Redo storage

Single-change Undo/Redo still reconstructs the desired file from `.orig` and the active declarative Change records. A temporary full-file guard is captured for recovery during the operation, then removed after the history update succeeds. New operations no longer persist an additional full `.current` snapshot for History previews. Partial previews are reconstructed from `.orig` and the active Change records, and their calculated text hash is checked against the recorded current hash. Legacy `.current` snapshots remain readable.

## Compatibility

- Old history entries without `redo_strategy` default to `snapshot` and continue reading their legacy `.new` snapshots.
- Existing `.orig`, `.new`, `.current`, and archived `.blobs` data are not deleted or migrated automatically.
- New delta artifacts are stored beside the transaction's original snapshot.
- Full transaction Undo continues to use the immutable `.orig` snapshot.
- New files use a `.new` snapshot; deleted files use a delete marker; rename-only files can reuse `.orig` when mode is unchanged.

## Tests

`tests/test_hybrid_rollback_storage.py` verifies compact delta creation, exact transaction Undo/Redo, corrupt-delta refusal before workspace modification, full-snapshot fallback for large replacements, reconstruction of History previews without persistent `.current` copies, and legacy snapshot defaults. Existing rollback tests continue to cover binary rename, delete/create, line endings, permissions, external edits, History write failures, and transaction-level recovery.

## Measured example

A local synthetic Python source file of approximately 310.9 KB with two small line replacements produced a 359-byte Redo delta instead of a 310.9 KB `.new` snapshot (about 99.88% less Redo-artifact storage). Including the immutable `.orig`, total per-file transaction backup data fell from about 621.8 KB to 311.2 KB (about 49.9% less). Apply plus delta generation took approximately 0.11 seconds in that run. This is an illustrative benchmark, not a guarantee: large or broadly rewritten files fall back to full snapshots.
