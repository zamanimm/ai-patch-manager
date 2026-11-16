# Selection persistence fix

## Root cause

The Phase 1 startup optimization changed `ProjectView._refresh(silent=True)` to render the existing `Controller.files` list without rescanning. That is correct after `Controller.open_project()`, which performs a scan, but not during application startup: `Controller.__init__()` restores the last project and its `selected_files` from `.ai-patch-manager/config.json` without populating `Controller.files`.

As a result, the Project tab rendered an empty tree. `_sync_selection(save=False)` then rebuilt the in-memory selection from that empty tree, making previously selected files appear unchecked or disappear after restart.

## Fixes

- Run the initial project scan in the background when the app starts with a restored project.
- Do not treat the tree as authoritative until that scan completes.
- Keep existing selections while a startup scan is pending or fails.
- Preserve selected files that exist on disk but are omitted from the current scan (for example, hidden/excluded files).
- Flush selection state unconditionally on application close and before switching projects, rather than only when the debounce timer happens to be active.
- Keep the existing debounce during normal checkbox interaction to avoid excessive disk writes.

## Verification

- Workspace config round-trip test confirms selected paths and modes survive a fresh Controller load.
- Added offscreen GUI regression tests for startup scanning, restored checked state, and preserving existing selected paths omitted from the scan.
- Run `python -m unittest discover -s tests -v` on Windows with PySide6 installed to execute the GUI tests.
