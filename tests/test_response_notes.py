"""Regression tests for optional, essential AI-supplied response notes."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from lxml import etree
from core.controller import Controller
from core.parser import ProtocolError, ResponseNote, parse_response, parse_response_with_note
from core.prompt_builder import build_protocol_instruction


SUCCESS_XML = '''<response version="1.0">
  <status>success</status>
  <summary>Add import</summary>
  <note type="warning">Requires installing a new dependency before running.</note>
  <changes>
    <change id="chg-001" path="app.py" operation="append">
      <replacement><![CDATA[import some_module]]></replacement>
    </change>
  </changes>
</response>'''


class ResponseNotesCoreTests(unittest.TestCase):
    def test_warning_note_and_existing_parse_api(self):
        status, summary, legacy, changes, note = parse_response_with_note(SUCCESS_XML)
        self.assertEqual((status, summary, legacy), ('success', 'Add import', ''))
        self.assertEqual((note.type, note.text),
                         ('warning', 'Requires installing a new dependency before running.'))
        self.assertEqual(changes[0].content, 'import some_module')
        self.assertEqual(len(parse_response(SUCCESS_XML)), 4)

    def test_info_note_is_not_automatically_a_warning(self):
        xml = ('<response version="1.0"><status>no_changes</status>'
               '<summary>Nothing</summary><note type="info">Manual step needed</note>'
               '<changes/></response>')
        self.assertEqual(parse_response_with_note(xml)[4],
                         ResponseNote('info', 'Manual step needed'))
        self.assertEqual(parse_response_with_note(xml.replace('type="info"', 'type="INFO"'))[4].type,
                         'info')  # compatibility: type matching is case-insensitive
        unknown = xml.replace('type="info"', 'type="critical"')
        self.assertEqual(parse_response_with_note(unknown)[4].type, 'info')

    def test_multiple_notes_are_rejected_to_avoid_hidden_messages(self):
        xml = ('<response version="1.0"><status>no_changes</status>'
               '<note type="warning">First</note>'
               '<note type="info">Second</note><changes/></response>')
        with self.assertRaises(ProtocolError):
            parse_response_with_note(xml)

    def test_no_note_keeps_old_behavior_and_legacy_notes(self):
        xml = ('<response version="1.0"><status>no_changes</status>'
               '<summary>Nothing</summary><notes>Legacy text</notes><changes/></response>')
        self.assertIsNone(parse_response_with_note(xml)[4])
        self.assertEqual(parse_response(xml)[2], 'Legacy text')

    def test_escaped_copy_and_cdata_remain_intact(self):
        raw = ('\\<response version="1.0">\\\n'
               '  \\<status>no_changes\\</status>\\\n'
               '  \\<note type="warning">Run pip install x\\</note>\\\n'
               '  \\<changes/>\\\n'
               '\\</response>')
        self.assertEqual(parse_response_with_note(raw)[4],
                         ResponseNote('warning', 'Run pip install x'))
        xml = ('<response version="1.0"><status>no_changes</status>'
               '<note type="info"><![CDATA[Keep \\d and &#x20; exactly]]></note>'
               '<changes/></response>')
        self.assertEqual(parse_response_with_note(xml)[4].text,
                         r'Keep \d and &#x20; exactly')

    def test_controller_clears_notice_and_does_not_modify_changes(self):
        ctrl = Controller()
        ctrl.import_response(SUCCESS_XML)
        self.assertEqual(ctrl.response_notice.type, 'warning')
        self.assertEqual(ctrl.changes[0].id, 'chg-001')
        ctrl.clear_parsed_changes()
        self.assertIsNone(ctrl.response_notice)
        self.assertEqual(ctrl.response_raw, SUCCESS_XML)
        ctrl.import_response(SUCCESS_XML)
        ctrl.reset_session()
        self.assertIsNone(ctrl.response_notice)

    def test_prompt_contract_contains_one_essential_note_rule_in_both_modes(self):
        for symbols in (True, False):
            xml = etree.fromstring(build_protocol_instruction(symbols).encode())
            instructions = xml.findtext('system/instructions')
            self.assertIn('only if there is an essential risk', instructions)
            self.assertIn('Otherwise omit <note> entirely', instructions)
            self.assertEqual(instructions.count('at most one optional response-level'), 1)
            self.assertIsNotNone(xml.find('system/response_schema/response'))
        with tempfile.TemporaryDirectory() as tmp:
            ctrl = Controller()
            ctrl.project.root = tmp
            ctrl.project.settings.include_files = False
            ctrl.project.settings.include_system = False
            prompt_xml = etree.fromstring(ctrl.build_full_xml_prompt('Do X', '').encode())
            self.assertIn('Otherwise omit <note> entirely', prompt_xml.findtext('output_contract'))


os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtWidgets import QApplication
    from ui.changes_view import ChangesView
    from ui.response_view import ResponseView
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, 'PySide6 is required for GUI tests')
class ResponseNotesGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_changes_note_is_visible_and_plain_text(self):
        ctrl = Controller()
        ctrl.response_notice = ResponseNote('warning', '<b>Do not trust markup</b>')
        view = ChangesView(ctrl, None)
        view.reload()
        self.assertFalse(view.lbl_note.isHidden())
        self.assertEqual(view.lbl_note.textFormat(), __import__('PySide6.QtCore', fromlist=['Qt']).Qt.PlainText)
        self.assertIn('<b>', view.lbl_note.text())
        ctrl.response_notice = None
        view.reload()
        self.assertTrue(view.lbl_note.isHidden())
        view.close()

    def test_dialog_only_for_warning_after_parse(self):
        class MainStub:
            def __init__(self): self.opened = 0
            def open_changes_tab(self): self.opened += 1
            def show_output(self, *args): pass
            def error_box(self, *args): pass

        ctrl, main = Controller(), MainStub()
        view = ResponseView(ctrl, main)
        with patch.object(view, '_show_response_warning') as dialog:
            view.txt_response.setPlainText(
                '<response version="1.0"><status>no_changes</status><changes/>'
                '<note type="info">Only an essential manual step</note></response>')
            view._parse()
            dialog.assert_not_called()
            view.txt_response.setPlainText(
                '<response version="1.0"><status>no_changes</status><changes/>'
                '<note type="warning">A genuine risk</note></response>')
            view._parse()
            dialog.assert_called_once()
        self.assertEqual(main.opened, 2)
        view.close()

if __name__ == '__main__':
    unittest.main()
