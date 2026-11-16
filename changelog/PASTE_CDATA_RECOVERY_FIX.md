# Pasted XML / CDATA recovery — 2026-11-10

## What was wrong

The earlier `core/parser.py` recovery removed `\<...>` escapes and trailing
Markdown slashes **only outside** `<![CDATA[...]]>`. Consequently, XML could
parse as valid while the `<match>` and `<replacement>` code still contained
literal `&#x20;` indentation and extra `\` at the end of source lines. This
caused `target not found` and incorrect proposed code in the diff preview.

## What changed

- `core/parser.py` recognizes repeated encoded leading indentation (`&#x20;`,
  `&#32;`) and repeated Markdown line-ending backslashes within CDATA, in
  addition to escaped XML markup outside CDATA.
- Repair is conditional on strong copy-format evidence, not unconditional
  unescaping. Entity references within ordinary source strings, regexes,
  literal paths, and isolated intentional HTML entities are preserved.
- When an artifact pattern is present, leading encoded indentation becomes
  actual spaces and repeated presentation-only backslashes at line endings
  are removed before building change objects. Original pasted XML stays in
  the Response tab; only parsed proposals are normalized.
- Both `<match>` and `<replacement>` use the same recovery mechanism.
- The prompt instructions also ask the model not to put HTML entities into
  code indentation or append Markdown backslashes to code lines.

## Tests

`python -m unittest discover -s tests -v`

Added realistic regression tests for source snippets with both kinds of
artifacts, their exact-match location in a source file, normal XML, escaped
markup, backslashes in regexes/Windows paths, intentional HTML entities, and
normalization idempotency.

## Limitations and safety

This is an evidence-based recovery for common copy/paste presentation damage,
not a general-purpose edit-distance matcher. It does not change `locate()` or
silently apply patches with an ambiguous or missing target. If the source
file has actually changed or the supplied target was invented, validation
correctly continues to reject the change. Graphical tests still require
PySide6 on the target Windows machine.
