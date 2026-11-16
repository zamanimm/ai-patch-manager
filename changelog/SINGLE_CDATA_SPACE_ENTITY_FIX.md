# Single `&#x20;` inside CDATA — parser recovery

## Fixed behavior

The previous paste recovery only decoded encoded indentation when the reply
contained escaped XML tags, repeated `&#x20;` indentation, or repeated copied
line-ending backslashes. Consequently, a valid XML reply with just **one**
copy-formatted space entity inside CDATA was not cleaned.

The parser now also detects either:

- one `&#x20;` or `&#32;` in a line's indentation position followed by two or
  more existing spaces or a tab; or
- a CDATA line (including the entire CDATA payload) consisting solely of
  numeric space entity/ies and optional horizontal whitespace.

These markers are normalized to a real space before constructing the parsed
change and before exact-match validation. The original Response text is not
edited. Literal strings such as `entity = "&#x20;"`, regexes, Windows paths,
and documentation prose such as `&#x20; is a space entity` are preserved.

## Example

Input:

```xml
<replacement><![CDATA[def run():
&#x20;   return 42]]></replacement>
```

Parsed replacement:

```python
def run():
    return 42
```

Even `<replacement><![CDATA[&#x20;]]></replacement>` is now parsed as one
literal space.

## Safety limitation

An entity that occupies a whole line (or a whole CDATA payload) is ambiguous:
it might be a deliberate literal HTML entity rather than copied formatting.
The recovery interprets it as a space, to satisfy the paste-repair workflow.
If exact literal HTML entities are needed there, inspect the Changes preview
before applying. The parser otherwise does not perform unrestricted HTML
unescaping inside code or change target matching rules.

## Verification

`python -m unittest discover -s tests -q`

2026-11-10: 93 tests, all successful, 18 GUI-related skipped due to missing
PySide6 in this environment. All Python files compile.
