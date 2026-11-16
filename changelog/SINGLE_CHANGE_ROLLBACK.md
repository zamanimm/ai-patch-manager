# Safe single-change rollback

## What is added

The History tab now distinguishes two levels of rollback:

- **Undo Transaction / Redo Transaction**: restore or reapply every file in one Apply transaction.
- **Undo Selected Change / Redo Selected Change**: remove or reapply one `chg-...` entry while keeping the other changes from the same response active.

Expand a transaction, expand a file, and select the individual change row. Selecting the transaction row continues to target the complete transaction.

## How single-change rollback works

A transaction created by this version stores the declarative details of each applied change (operation, path, target, replacement content, rename destination and the matching settings used during Apply). It also keeps the original file snapshot and the expected post-Apply SHA-256.

To undo one change, the app:

1. Confirms that the transaction has saved change details and an original backup where required.
2. Verifies the current file path, raw SHA-256 and recorded permission mode against the last state recorded by the app.
3. Verifies the original backup and its hash.
4. Rebuilds the affected file from the original snapshot, replaying every change in that transaction except the selected change and any changes already rolled back.
5. Verifies the rebuilt state. When all changes are active again, the result must match the original post-Apply hash; when all are rolled back, it must match the original file hash.
6. Captures a temporary recovery copy of the current file, writes the rebuilt file, and atomically records the new transaction state. History previews reconstruct partial states from the original snapshot plus active Change records; new transactions do not persist another full-file `.current` copy.
7. If writing the file or history fails, restores the exact pre-operation file and leaves the previous history record intact.

A single-change redo uses the same process with the selected change included again. Transaction-level undo/redo also verifies every affected file before modifying any file and uses a recovery snapshot to restore the pre-operation state if a later step fails.

## Safety limits

- A file edited externally after the last recorded operation is not overwritten: hash or permission-mode mismatch stops the operation. The app also verifies the recovery copy and rechecks the live file immediately before writing.
- A destination path that already exists is never overwritten.
- If backup-before-apply was disabled and an original snapshot is needed, single-change rollback is refused. Apply still takes temporary raw-byte snapshots while it runs so a mid-batch failure can be recovered; those temporary copies are discarded after a successful Apply.
- Binary files can be single-change rolled back only for file-level operations such as delete/rename; text reconstruction is refused for binary content.
- Transactions created by older versions did not save the original `Change` definitions. Their individual changes are shown as legacy entries and cannot be rolled back separately. Whole-transaction undo remains available when its original backup exists and its current-state checks pass.
- File permission mode is recorded and checked in addition to content hashes; the original and post-apply backup modes are verified before restore.
- `change_records` are written only for future transactions. Existing history is not rewritten to fabricate missing change details.

## Status model

- `applied`: all changes are active.
- `partially_rolled_back`: at least one change is rolled back and at least one remains active.
- `rolled_back`: all changes are rolled back, either individually or as a full transaction.

Each single-change undo/redo is recorded in `change_rollback_events`; `rolled_back_change_ids` tracks the current per-change state. The original `new_hash` remains immutable. New transactions use a hybrid Redo artifact: a compact verified byte delta when it is materially smaller, the original snapshot when content and mode are unchanged (for example, a rename), a delete marker for deleted files, or a full `.new` snapshot as fallback. Older transactions continue to use their existing `.new` snapshots.

## Tests

`tests/test_single_change_rollback.py` covers independent changes in one file, multiple files, create/delete/rename, binary rename preservation, partial undo followed by full transaction undo/redo, manual edits and permission changes, missing backups, history-write failure recovery, and legacy history restrictions. `tests/test_history_single_change_ui.py` checks History tree selection and action enablement when PySide6 is available.

## Windows GUI smoke test

1. Open a disposable project with a text file containing two distinct target blocks.
2. Apply one response containing two changes to that same file.
3. Open History, expand the transaction and file, select the first `chg-...` row, then click **Undo Selected Change**. Confirm only that change disappears from the file and the other remains.
4. Select the rolled-back change and click **Redo Selected Change**. Confirm the exact post-Apply file is restored.
5. Undo one change again, then click **Undo Transaction** on the parent transaction. Confirm the original file is restored. Click **Redo Transaction** and confirm the complete response is reapplied.
6. Modify a touched file manually outside the app and try a single-change undo. It must refuse without overwriting the manual edit.
7. Open an older history entry created before this feature. Its change rows must be marked legacy and individual undo/redo must be disabled, while whole-transaction undo remains available when snapshots and hashes verify.
