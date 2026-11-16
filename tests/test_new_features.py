"""New UI workflow regressions: parsed change clearing, XML contract, proposed-code edits."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lxml import etree

from core.controller import Controller, State
from core.prompt_builder import build_protocol_instruction
from models.change import Change, Target


class FeatureCoreTests(unittest.TestCase):
    def test_clear_preview_preserves_imported_xml_and_history(self):
        ctrl = Controller()
        ctrl.response_raw = '<response version="1.0" />'
        ctrl.response_summary = 'Earlier summary'
        ctrl.response_notes = 'Extra notes'
        ctrl.changes = [Change('c1', 'hello.py', 'create', content='print(1)')]
        ctrl.state = State.VALIDATED
        history = ctrl.history
        ctrl.clear_parsed_changes()
        self.assertEqual(ctrl.changes, [])
        self.assertEqual(ctrl.response_raw, '<response version="1.0" />')
        self.assertEqual(ctrl.response_summary, '')
        self.assertEqual(ctrl.response_notes, '')
        self.assertIs(ctrl.history, history)
        self.assertEqual(ctrl.state, State.RESPONSE_RECEIVED)
        ctrl.response_raw = ''
        ctrl.clear_parsed_changes()
        self.assertEqual(ctrl.state, State.DRAFT)

    def test_instruction_document_is_well_formed_and_xml_only(self):
        for include_symbol in (False, True):
            instructions = build_protocol_instruction(include_symbol)
            root = etree.fromstring(instructions.encode('utf-8'))
            self.assertEqual(root.tag, 'prompt')
            system = root.find('system')
            self.assertIsNotNone(system)
            schema = system.find('response_schema/response')
            self.assertIsNotNone(schema)
            self.assertEqual(schema.get('version'), '1.0')
            # The schema is real nested XML, not escaped text inside an instruction.
            self.assertNotIn('&lt;response', instructions)
            # One consolidated output contract; no repeated XML-only directives.
            instruction_text = system.findtext('instructions') or ''
            self.assertEqual(instruction_text.count('For code or file-editing requests'), 1)
            self.assertNotIn('Avoid nested CDATA issues', instruction_text)
            self.assertEqual(system.findall('instruction'), [])
            self.assertIn('markdown fences', instructions.lower())
            self.assertIn('ask follow-up questions', instructions.lower())
            self.assertIn('one', instructions.lower())

    def test_instruction_cdata_safely_preserves_terminator_text(self):
        from core.prompt_builder import _cdata
        sample = 'before ]]> after'
        wrapped = '<root>' + _cdata(sample) + '</root>'
        parsed = etree.fromstring(wrapped.encode('utf-8'))
        self.assertEqual(parsed.text, sample)

    def test_prompts_always_include_small_xml_only_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctrl = Controller()
            ctrl.project.root = tmp
            ctrl.project.settings.include_files = False
            ctrl.project.settings.include_system = False
            text = ctrl.build_full_xml_prompt('Edit requested code', '')
            tree = etree.fromstring(text.encode('utf-8'))
            self.assertIsNotNone(tree.find('output_contract'))
            self.assertIn('Do not include questions', tree.findtext('output_contract'))
            self.assertIsNone(tree.find('system'))
            ctrl.project.settings.include_system = True
            text = ctrl.build_full_xml_prompt('Edit requested code', '')
            tree = etree.fromstring(text.encode('utf-8'))
            self.assertIsNotNone(tree.find('system'))
            self.assertIsNone(tree.find('output_contract'))
            self.assertIsNotNone(tree.find('system/response_schema/response'))

    def test_project_tree_is_optional_and_contains_paths_without_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src" / "utils").mkdir(parents=True)
            (root / "src" / "main.py").write_text("SECRET_SOURCE_CONTENT", encoding="utf-8")
            (root / "README.md").write_text("SECRET_README_CONTENT", encoding="utf-8")
            ctrl = Controller()
            ctrl.project.root = tmp
            ctrl.project.settings.include_files = False
            ctrl.project.settings.include_project_tree = False
            ctrl.files = ["README.md", "src/main.py", "src/utils/helpers.py"]

            default_prompt = ctrl.build_full_xml_prompt("Inspect structure", "")
            default_root = etree.fromstring(default_prompt.encode("utf-8"))
            self.assertIsNone(default_root.find("project_tree"))

            ctrl.project.settings.include_project_tree = True
            tree_prompt = ctrl.build_full_xml_prompt("Inspect structure", "")
            tree_root = etree.fromstring(tree_prompt.encode("utf-8"))
            project_tree = tree_root.find("project_tree")
            self.assertIsNotNone(project_tree)
            self.assertEqual(project_tree.get("file_count"), "3")
            self.assertEqual(project_tree.find("directory[@name='src']/file[@name='main.py']").get("name"), "main.py")
            self.assertIsNotNone(project_tree.find("file[@name='README.md']"))
            self.assertNotIn("SECRET_SOURCE_CONTENT", tree_prompt)
            self.assertNotIn("SECRET_README_CONTENT", tree_prompt)

    def test_project_tree_escapes_names_and_rejects_unsafe_paths(self):
        from core.prompt_builder import build_project_tree
        rendered = build_project_tree("demo & tools", [
            "src/a&b.py", "../outside.py", "/absolute.py", "C:/outside.py", "docs/readme <draft>.md"
        ])
        parsed = etree.fromstring(rendered.encode("utf-8"))
        self.assertEqual(parsed.get("root"), "demo & tools")
        self.assertEqual(parsed.get("file_count"), "2")
        self.assertIsNotNone(parsed.find("directory[@name='src']/file[@name='a&b.py']"))
        self.assertIsNotNone(parsed.find("directory[@name='docs']/file[@name='readme <draft>.md']"))
        self.assertNotIn("outside.py", rendered)

    def test_project_tree_setting_round_trips_xml_and_dict(self):
        from models.project import Project, save_project_xml, load_project_xml
        with tempfile.TemporaryDirectory() as tmp:
            project = Project(name="tree-setting", root=tmp)
            self.assertFalse(project.settings.include_project_tree)
            project.settings.include_project_tree = True
            path = Path(tmp) / "project.xml"
            save_project_xml(str(path), project)
            loaded = load_project_xml(str(path))
            self.assertTrue(loaded.settings.include_project_tree)
            self.assertTrue(Project.from_dict(project.to_dict()).settings.include_project_tree)

    def test_safe_no_changes_xml_parses_but_empty_success_is_rejected(self):
        from core.parser import parse_response, ProtocolError
        safe_xml = ('<response version="1.0"><status>no_changes</status>'
                    '<summary>Not enough context for a safe patch</summary>'
                    '<changes /></response>')
        status, summary, notes, changes = parse_response(safe_xml)
        self.assertEqual(status, 'no_changes')
        self.assertEqual(changes, [])
        with self.assertRaises(ProtocolError):
            parse_response(safe_xml.replace('no_changes', 'success'))

    def test_edit_proposed_code_can_be_revalidated_without_disk_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / 'example.py'
            original.write_bytes(b'print("old")\n')
            ctrl = Controller()
            ctrl.project.root = tmp
            ch = Change('chg-001', 'example.py', 'replace',
                        target=Target(match='print("old")'), content='print("new")')
            ctrl.changes = [ch]
            self.assertEqual(ctrl.validate_current_changes(), [])
            ctrl.update_change_content(ch, 'print("edited")')
            self.assertEqual(ctrl.validate_current_changes(), [])
            self.assertTrue(ch.valid)
            self.assertTrue(ch.edited)
            self.assertIn('print("edited")', ch.unified_diff)
            self.assertEqual(original.read_bytes(), b'print("old")\n')


os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtWidgets import QApplication, QMessageBox
    from PySide6.QtCore import Qt
    from ui.changes_view import ChangesView
    from ui.project_view import ProjectView
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False


class _StatusBar:
    def showMessage(self, *args):
        pass


class _FakeWindow:
    def statusBar(self):
        return _StatusBar()

    def _report_ui_error(self, *args):
        raise AssertionError('Unexpected UI error: ' + str(args))

    def error_box(self, text):
        raise AssertionError('Unexpected UI error: ' + str(text))

    def _refresh_state_label(self):
        pass


@unittest.skipUnless(GUI_AVAILABLE, 'PySide6 needed for offscreen GUI test')
class FeatureGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_edit_widget_validates_and_never_modifies_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'a.py'
            path.write_text('x=1\n', encoding='utf-8')
            ctrl = Controller()
            ctrl.project.root = tmp
            ctrl.changes = [Change('c1', 'a.py', 'replace',
                                   target=Target(match='x=1'), content='x=2')]
            self.assertEqual(ctrl.validate_current_changes(), [])
            view = ChangesView(ctrl, _FakeWindow())
            view.reload()
            view.show()
            self.app.processEvents()
            view.chk_edit.setChecked(True)
            self.assertTrue(view.edit_container.isVisible())
            view.edit_code.setPlainText('x=3')
            self.assertFalse(view.btn_apply_sel.isEnabled())
            view.btn_save_edit.click()
            self.assertTrue(view.btn_apply_sel.isEnabled())
            self.assertEqual(ctrl.changes[0].content, 'x=3')
            self.assertEqual(path.read_text(), 'x=1\n')
            view.close()

    def test_project_tree_checkbox_defaults_off_and_updates_setting(self):
        from ui.prompt_view import PromptView
        ctrl = Controller()
        main = _FakeWindow()
        main.schedule_workspace_save = lambda: None
        main.patch_tab = None
        main.statusBar = lambda: _StatusBar()
        main.open_settings = lambda: None
        view = PromptView(ctrl, main)
        self.assertFalse(view.chk_project_tree.isChecked())
        view.chk_project_tree.setChecked(True)
        self.assertTrue(ctrl.project.settings.include_project_tree)
        view.close()

    def test_clear_changes_retains_original_xml(self):
        ctrl = Controller()
        ctrl.response_raw = '<response version="1.0" />'
        ctrl.changes = [Change('c1', 'a.py', 'create', content='hi')]
        view = ChangesView(ctrl, _FakeWindow())
        view.reload()
        with patch('ui.changes_view.QMessageBox.question', return_value=QMessageBox.Yes):
            view.btn_clear.click()
        self.assertEqual(view.list.count(), 0)
        self.assertEqual(ctrl.changes, [])
        self.assertTrue(ctrl.response_raw)
        view.close()

    def test_changes_double_click_uses_safe_code_editor_launcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'example.py'
            path.write_text('pass\n')
            ctrl = Controller()
            ctrl.project.root = tmp
            ctrl.changes = [Change('c1', 'example.py', 'replace',
                                   target=Target(match='pass'), content='return 42')]
            ctrl.validate_current_changes()
            view = ChangesView(ctrl, _FakeWindow())
            view.reload()
            with patch('ui.changes_view.open_in_code_editor', return_value=True) as launcher:
                view.list.itemDoubleClicked.emit(view.list.item(0))
                launcher.assert_called_once_with(str(path.resolve()), view)
            view.close()

    def test_headers_allow_user_custom_width(self):
        import unittest.mock
        ctrl = Controller()
        main = unittest.mock.Mock()
        main.update_prompt_stats = lambda: None
        main.populate_recent_menu = lambda menu: None
        view = ProjectView(ctrl, main)
        header = view.tree.header()
        self.assertEqual(header.sectionResizeMode(1), header.Interactive)
        header.resizeSection(1, 250)
        state = header.saveState()
        header.resizeSection(1, 400)
        self.assertTrue(header.restoreState(state))
        self.assertEqual(header.sectionSize(1), 250)
        view.close()


if __name__ == '__main__':
    unittest.main()
