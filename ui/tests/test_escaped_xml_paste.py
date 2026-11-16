"""Regression tests for XML copied from rich-text/Markdown chat messages."""
from __future__ import annotations

import unittest

from core.parser import ProtocolError, normalize_escaped_xml_copy, parse_response


# This is the pasted layout reported by the user, including literal backslashes,
# &#x20; indentation and CDATA containing real source code.
PASTED_EXAMPLE = (
    '\\<response version="1.0">\\\n'
    '&#x20;   \\<status>success\\</status>\\\n'
    '&#x20;   \\<summary>Example patch: add a new import directly after an '
    'existing import in app.py.\\</summary>\\\n'
    '&#x20;   \\<changes>\\\n'
    '&#x20;       \\<change id="chg-001" path="app.py" operation="append">\\\n'
    '&#x20;           \\<target>\\\n'
    '&#x20;               \\<match>\\<![CDATA[import os]]>\\</match>\\\n'
    '&#x20;           \\</target>\\\n'
    '&#x20;           \\<replacement>\\<![CDATA[import sys]]>\\</replacement>\\\n'
    '&#x20;           \\<reason>Add the sys module import immediately below '
    'import os while preserving the original code.\\</reason>\\\n'
    '&#x20;           \\<notes>Requires exactly one occurrence of the '
    'target in the source file.\\</notes>\\\n'
    '&#x20;           \\<confidence>0.95\\</confidence>\\\n'
    '&#x20;       \\</change>\\\n'
    '&#x20;   \\</changes>\\\n'
    '\\</response>'
)


class EscapedXMLPasteTests(unittest.TestCase):
    def test_exact_reported_paste_format(self):
        status, summary, notes, changes = parse_response(PASTED_EXAMPLE)
        self.assertEqual(status, 'success')
        self.assertEqual(summary, 'Example patch: add a new import directly after an existing import in app.py.')
        self.assertEqual(notes, '')
        self.assertEqual(len(changes), 1)
        change = changes[0]
        self.assertEqual(change.id, 'chg-001')
        self.assertEqual(change.path, 'app.py')
        self.assertEqual(change.operation, 'append')
        self.assertEqual(change.target.match, 'import os')
        self.assertEqual(change.content, 'import sys')
        self.assertEqual(change.confidence, 0.95)
        self.assertEqual(change.reason, 'Add the sys module import immediately below import os while preserving the original code.')

    def test_cdata_backslashes_and_entities_are_not_modified(self):
        code = 'pattern = r"\\<tag>"\nentity = "&#x20;"\npath = r"C:\\src\\app.py"\n' \
               'print("\\</response>")\nlast = "\\\\"'
        raw = ('\\<response version="1.0">\\\n'
               '  \\<status>success\\</status>\\\n'
               '  \\<changes>\\\n'
               '    \\<change id="a" path="app.py" operation="create">\\\n'
               '      \\<replacement>\\<![CDATA[' + code + ']]>\\</replacement>\\\n'
               '    \\</change>\\\n'
               '  \\</changes>\\\n'
               '\\</response>')
        _, _, _, changes = parse_response(raw)
        self.assertEqual(changes[0].content, code)

    def test_normal_valid_xml_preserves_text(self):
        code = 'r"\\<tag>"\nliteral = "&#x20;"\nprint("\\</response>")'
        raw = ('<response version="1.0"><status>success</status><changes>'
               '<change id="a" path="app.py" operation="create">'
               '<replacement><![CDATA[' + code + ']]></replacement>'
               '</change></changes></response>')
        self.assertEqual(parse_response(raw)[3][0].content, code)

    def test_escaped_no_changes_response(self):
        raw = ('\\<response version="1.0">\\\n'
               '  \\<status>no_changes\\</status>\\\n'
               '  \\<summary>No safe edit\\</summary>\\\n'
               '  \\<changes>\\</changes>\\\n'
               '\\</response>')
        status, summary, _, changes = parse_response(raw)
        self.assertEqual((status, summary, changes), ('no_changes', 'No safe edit', []))

    def test_unescaped_root_but_escaped_children(self):
        raw = ('<response version="1.0">\\\n'
               '  \\<status>no_changes\\</status>\\\n'
               '  \\<changes>\\</changes>\\\n'
               '</response>')
        self.assertEqual(parse_response(raw)[0], 'no_changes')

    def test_normalizer_idempotent_for_valid_xml(self):
        xml = ('<response version="1.0"><status>no_changes</status>'
               '<changes></changes></response>')
        self.assertEqual(normalize_escaped_xml_copy(xml), xml)

    def test_does_not_accept_invalid_operation(self):
        raw = PASTED_EXAMPLE.replace('operation="append"', 'operation="execute"')
        with self.assertRaises(ProtocolError):
            parse_response(raw)

    def test_does_not_fabricate_missing_closing_elements_inside_cdata(self):
        raw = PASTED_EXAMPLE.replace('<![CDATA[import sys]]>', '<![CDATA[import sys')
        # Existing parser supports truncated replies; this one has a CDATA
        # delimiter that is missing mid-document and must remain invalid.
        with self.assertRaises(ProtocolError):
            parse_response(raw)


class RealWorldCdataCopyTests(unittest.TestCase):
    """The real bug was in CDATA, so ordinary XML parsing succeeded anyway."""

    def test_powershell_csharp_snippet_with_cdata_copy_decoration(self):
        from core.validator import locate
        from types import SimpleNamespace

        original = (
            "if (id != 11 && (hadSelection || !selectionKnown)) {\n"
            "    initiallySelected = CopyHighlightedText();\n"
            "    clipboardExpectedSequence = Native.GetClipboardSequenceNumber();\n"
            "    if (initiallySelected != null) hadSelection = true;\n"
            "}"
        )
        # Model-produced pasted XML can be well-formed, but the CDATA source
        # is still corrupted by &#x20; and Markdown trailing backslashes.
        mangled = (
            'if (id != 11 && (hadSelection || !selectionKnown)) {\\\n'
            '&#x20;   initiallySelected = CopyHighlightedText();\\\n'
            '&#x20;   clipboardExpectedSequence = Native.GetClipboardSequenceNumber();\\\n'
            '&#x20;   if (initiallySelected != null) hadSelection = true;\\\n'
            '}'
        )
        pasted = (f'<response version="1.0"><status>success</status><changes>'
                  f'<change id="chg-001" path="LangFlip.ps1" operation="replace">'
                  f'<target><match><![CDATA[{mangled}]]></match></target>'
                  f'<replacement><![CDATA[{mangled}]]></replacement>'
                  f'</change></changes></response>')
        _, _, _, changes = parse_response(pasted)
        self.assertEqual(changes[0].target.match, original)
        self.assertEqual(changes[0].content, original)
        settings = SimpleNamespace(exact_match=True, fuzzy_match=False,
                                   context_match=False, hash_validation=False)
        span, kind = locate(changes[0], '// test header\n' + original + '\n', settings)
        self.assertEqual((span, kind), ((1, 6), 'exact'))

    def test_single_encoded_indent_inside_valid_cdata_is_repaired(self):
        """Only one &#x20; in CDATA, without tag escapes or trailing slashes."""
        source = 'def run():\n    return 42'
        mangled = 'def run():\n&#x20;   return 42'
        raw = (
            '<response version="1.0"><status>success</status><changes>'
            '<change id="x" path="app.py" operation="replace">'
            '<target><match><![CDATA[' + mangled + ']]></match></target>'
            '<replacement><![CDATA[' + mangled.replace('42', '43') + ']]></replacement>'
            '</change></changes></response>'
        )
        _, _, _, changes = parse_response(raw)
        self.assertEqual(changes[0].target.match, source)
        self.assertEqual(changes[0].content, source.replace('42', '43'))
        clean = normalize_escaped_xml_copy(raw)
        self.assertEqual(clean, normalize_escaped_xml_copy(clean))

    def test_only_cdata_payload_is_space_entity(self):
        raw = ('<response version="1.0"><status>success</status><changes>'
               '<change id="x" path="app.py" operation="replace">'
               '<target><match><![CDATA[&#x20;]]></match></target>'
               '<replacement><![CDATA[&#x20;]]></replacement>'
               '</change></changes></response>')
        _, _, _, changes = parse_response(raw)
        self.assertEqual(changes[0].target.match, ' ')
        self.assertEqual(changes[0].content, ' ')
        self.assertEqual(normalize_escaped_xml_copy(normalize_escaped_xml_copy(raw)),
                         normalize_escaped_xml_copy(raw))

    def test_lone_entity_on_own_line_inside_cdata(self):
        code = 'first\n&#x20;\nlast'
        raw = ('<response version="1.0"><status>success</status><changes>'
               '<change id="x" path="app.py" operation="create">'
               '<replacement><![CDATA[' + code + ']]></replacement>'
               '</change></changes></response>')
        self.assertEqual(parse_response(raw)[3][0].content, 'first\n \nlast')

    def test_single_encoded_indent_does_not_alter_literal_in_code(self):
        code = 'def f():\n    entity = "&#x20;"\n    return entity'
        raw = ('<response version="1.0"><status>success</status><changes>'
               '<change id="x" path="app.py" operation="create">'
               '<replacement><![CDATA[' + code + ']]></replacement>'
               '</change></changes></response>')
        self.assertEqual(parse_response(raw)[3][0].content, code)

    def test_escaped_xml_tags_plus_mangled_cdata(self):
        raw = (
            '\\<response version="1.0">\\\n'
            '\\<status>success\\</status>\\\n'
            '\\<changes>\\\n'
            '\\<change id="x" path="hello.py" operation="replace">\\\n'
            '\\<target>\\<match>\\<![CDATA[def f():\\\n'
            '&#x20;   return "yes"\\\n'
            ']]>\\</match>\\</target>\\\n'
            '\\<replacement>\\<![CDATA[def f():\\\n'
            '&#x20;   return "no"\\\n'
            ']]>\\</replacement>\\\n'
            '\\</change>\\</changes>\\</response>'
        )
        _, _, _, changes = parse_response(raw)
        self.assertEqual(changes[0].target.match, 'def f():\n    return "yes"')
        self.assertEqual(changes[0].content, 'def f():\n    return "no"')

    def test_single_backslash_for_real_line_continuation_not_removed(self):
        original = 'x = 1 + \\\n    2\ny = r"C:\\path\\file"'
        raw = ('\\<response version="1.0">\\\n'
               '\\<status>success\\</status>\\\n'
               '\\<changes><change id="x" path="a.py" operation="create">'
               '\\<replacement>\\<![CDATA[' + original + ']]>\\</replacement>'
               '</change>\\</changes>\\</response>')
        self.assertEqual(parse_response(raw)[3][0].content, original)

    def test_normal_code_entity_and_regex_remain_literal(self):
        code = ('entity = "&#x20;"\n'
                'regex = r"\\<tag>\\d+"\n'
                'data = r"C:\\user\\folder"')
        raw = ('<response version="1.0"><status>success</status><changes>'
               '<change id="x" path="a.py" operation="create">'
               '<replacement><![CDATA[' + code + ']]></replacement>'
               '</change></changes></response>')
        self.assertEqual(parse_response(raw)[3][0].content, code)

    def test_single_literal_leading_html_entity_without_copy_signature(self):
        code = '&#x20; is documented as a space entity\nnot code indentation'
        raw = ('<response version="1.0"><status>success</status><changes>'
               '<change id="x" path="README.md" operation="create">'
               '<replacement><![CDATA[' + code + ']]></replacement>'
               '</change></changes></response>')
        self.assertEqual(parse_response(raw)[3][0].content, code)

    def test_escaped_markup_does_not_rewrite_single_literal_html_entity(self):
        code = '&#x20; is intentionally literal\nother text'
        raw = ('\\<response version="1.0">\\\n'
               '\\<status>success\\</status>\\\n'
               '\\<changes><change id="x" path="README.md" operation="create">'
               '\\<replacement>\\<![CDATA[' + code + ']]>\\</replacement>'
               '</change>\\</changes>\\</response>')
        self.assertEqual(parse_response(raw)[3][0].content, code)

    def test_copy_normalizer_idempotent(self):
        raw = ('\\<response version="1.0">\\\n'
               '\\<status>success\\</status>\\\n'
               '\\<changes><change id="x" path="a.py" operation="create">'
               '\\<replacement>\\<![CDATA[def x():\\\n'
               '&#x20;   pass\\\n]]>\\</replacement>'
               '</change>\\</changes>\\</response>')
        clean = normalize_escaped_xml_copy(raw)
        self.assertEqual(clean, normalize_escaped_xml_copy(clean))
        self.assertIn('\n    pass', clean)

    def test_literal_backslashes_inside_regex_cdata_unmodified(self):
        code = r'^\\d+\\s+\\w+$'
        raw = ('\\<response version="1.0">\\\n'
               '\\<status>success\\</status>\\\n'
               '\\<changes><change id="x" path="a.py" operation="replace">'
               '\\<target><regex>\\<![CDATA[' + code + ']]>\\</regex>\\</target>'
               '\\<replacement>\\<![CDATA[done]]>\\</replacement>'
               '</change>\\</changes>\\</response>')
        self.assertEqual(parse_response(raw)[3][0].target.regex, code)


if __name__ == '__main__':
    unittest.main()
