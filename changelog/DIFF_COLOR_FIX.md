# Diff color consistency fix

The diff highlighter previously painted red/green backgrounds and then allowed the generic syntax highlighter to replace the entire character formatting for keywords, imports, and other syntax tokens. That removed the line backgrounds only under highlighted words, causing the patchy green boxes shown in the screenshot.

`ui/highlight.py` now merges syntax foreground/font formatting into the base diff-line formatting, retaining the background. Unicode/UTF-16 token offsets remain handled. An offscreen GUI regression test is included in `tests/test_diff_coloring.py`.

No patch application, backup or undo/redo logic was changed. The serious data-safety issues identified in the Phase 3 audit are **not** fixed by this UI-only update.
