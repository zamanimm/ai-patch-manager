# Escaped XML paste compatibility

The Response tab now accepts the XML layout produced by some chat copy/paste
interfaces: escaped structural tags (`\<response>`, `\</status>`), trailing
backslashes after XML lines, and `&#x20;` indentation.

- The change is implemented in `core/parser.py`, so both Clipboard paste and
  imported text files use the same logic.
- Normal XML is parsed unchanged. Copied escaped XML is detected and fixed
  before XML parsing, even if its backslashes would otherwise be treated as
  valid text nodes.
- Only markup outside CDATA is normalized. Backslashes, XML-looking strings,
  and entity references within `<![CDATA[...]]>` remain untouched.
- The original input stays visible in the Response tab. No project files
  change during the import/parse step. All existing change validation applies.
- Malformed or unsafe instructions are not made valid by the normalizer.

## Regression tests

`python -m unittest tests.test_escaped_xml_paste -v`

The tests cover the exact reported format, `no_changes`, backslashes and
`&#x20;` in code, normal XML, and invalid operations.
