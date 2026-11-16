"""QtPy provider selection and optional Tree-Sitter feature tests."""
from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from core import qt_binding
from core.qt_binding import (QtBindingError, choose_qt_api, installed_qt_bindings)
from core.optional_dependencies import (
    OptionalFeatureStatus, reset_optional_dependency_cache_for_tests, tree_sitter_status,
)
from core.controller import Controller
from lxml import etree


class QtBindingSelectionTests(unittest.TestCase):
    def test_prefers_pyside6_when_both_are_installed(self):
        self.assertEqual(choose_qt_api(("pyqt6", "pyside6")), "pyside6")

    def test_automatically_uses_pyqt6_when_it_is_the_only_binding(self):
        self.assertEqual(choose_qt_api(("pyqt6",)), "pyqt6")

    def test_explicit_qt_api_overrides_default_preference(self):
        self.assertEqual(choose_qt_api(("pyside6", "pyqt6"), "pyqt6"), "pyqt6")

    def test_invalid_or_missing_explicit_binding_is_actionable(self):
        with self.assertRaises(QtBindingError):
            choose_qt_api(("pyside6",), "pyqt6")
        with self.assertRaises(QtBindingError):
            choose_qt_api(("pyside6",), "pyqt5")

    def test_installed_bindings_are_detected_without_importing_them(self):
        specs = {"PySide6": object(), "PyQt6": None}
        self.assertEqual(installed_qt_bindings(lambda name: specs.get(name)), ("pyside6",))

    def test_loader_uses_pyqt6_when_it_is_the_only_installed_provider(self):
        from types import SimpleNamespace
        modules = {
            "qtpy": SimpleNamespace(API_NAME="PyQt6"),
            "qtpy.QtCore": object(),
            "qtpy.QtGui": object(),
            "qtpy.QtWidgets": object(),
        }

        qt_binding.reset_qt_binding_for_tests()
        with patch.object(qt_binding.importlib.util, "find_spec", return_value=object()), \
             patch.object(qt_binding, "installed_qt_bindings", return_value=("pyqt6",)), \
             patch.object(qt_binding, "_purge_qtpy_modules"), \
             patch.object(qt_binding.importlib, "import_module", side_effect=lambda name: modules[name]):
            loaded = qt_binding.load_qt_modules()
        self.assertEqual(loaded.api, "pyqt6")
        self.assertEqual(qt_binding.selected_qt_api(), "pyqt6")
        qt_binding.reset_qt_binding_for_tests()

    def test_existing_pyside6_loads_without_qtpy(self):
        from types import ModuleType
        native = {}
        for name in ("QtCore", "QtGui", "QtWidgets"):
            module = ModuleType(f"PySide6.{name}")
            if name == "QtCore":
                module.qVersion = lambda: "6.test"
            native[f"PySide6.{name}"] = module

        def import_native(name):
            if name in native:
                return native[name]
            raise ImportError(name)

        qt_binding.reset_qt_binding_for_tests()
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(qt_binding.importlib.util, "find_spec",
                              side_effect=lambda name: None if name == "qtpy" else object()), \
                 patch.object(qt_binding, "installed_qt_bindings", return_value=("pyside6",)), \
                 patch.object(qt_binding.importlib, "import_module", side_effect=import_native):
                loaded = qt_binding.load_qt_modules()
                self.assertEqual(loaded.api, "pyside6")
                self.assertIs(sys.modules["qtpy.QtCore"], native["PySide6.QtCore"])
                self.assertEqual(sys.modules["qtpy"].API_NAME, "PySide6")
        qt_binding.reset_qt_binding_for_tests()

    def test_pyqt6_without_qtpy_reports_only_pyqt_compat_requirement(self):
        qt_binding.reset_qt_binding_for_tests()
        with patch.dict(os.environ, {"QT_API": "pyqt6"}, clear=True):
            with patch.object(qt_binding.importlib.util, "find_spec",
                              side_effect=lambda name: None if name == "qtpy" else object()), \
                 patch.object(qt_binding, "installed_qt_bindings", return_value=("pyqt6",)):
                with self.assertRaisesRegex(QtBindingError, "python -m pip install qtpy"):
                    qt_binding.load_qt_modules()
        qt_binding.reset_qt_binding_for_tests()

    def test_broken_qtpy_does_not_block_working_pyside6(self):
        from types import ModuleType
        native = {f"PySide6.{name}": ModuleType(f"PySide6.{name}")
                  for name in ("QtCore", "QtGui", "QtWidgets")}
        native["PySide6.QtCore"].qVersion = lambda: "6.test"

        def import_module(name):
            if name.startswith("PySide6."):
                if name in native:
                    return native[name]
                raise ImportError(name)
            raise ImportError("broken QtPy")

        qt_binding.reset_qt_binding_for_tests()
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(qt_binding.importlib.util, "find_spec", return_value=object()), \
                 patch.object(qt_binding, "installed_qt_bindings", return_value=("pyside6", "pyqt6")), \
                 patch.object(qt_binding.importlib, "import_module", side_effect=import_module):
                loaded = qt_binding.load_qt_modules()
                self.assertEqual(loaded.api, "pyside6")
                self.assertIs(loaded.QtWidgets, native["PySide6.QtWidgets"])
        qt_binding.reset_qt_binding_for_tests()

    def test_explicit_provider_does_not_silently_switch(self):
        qt_binding.reset_qt_binding_for_tests()
        with patch.dict(os.environ, {"QT_API": "pyqt6"}, clear=True):
            with patch.object(qt_binding.importlib.util, "find_spec",
                              side_effect=lambda name: None if name == "qtpy" else object()), \
                 patch.object(qt_binding, "installed_qt_bindings", return_value=("pyside6", "pyqt6")):
                with self.assertRaises(QtBindingError):
                    qt_binding.load_qt_modules()
        qt_binding.reset_qt_binding_for_tests()

    def test_pyside6_requirements_do_not_pull_qtpy(self):
        project = Path(__file__).resolve().parents[1]
        shared = (project / "requirements.txt").read_text(encoding="utf-8").lower()
        pyside = (project / "requirements-pyside6.txt").read_text(encoding="utf-8").lower()
        pyqt = (project / "requirements-pyqt6.txt").read_text(encoding="utf-8").lower()
        shared_requirements = "\n".join(
            line for line in shared.splitlines() if line.strip() and not line.lstrip().startswith("#"))
        pyside_requirements = "\n".join(
            line for line in pyside.splitlines() if line.strip() and not line.lstrip().startswith("#"))
        pyqt_requirements = "\n".join(
            line for line in pyqt.splitlines() if line.strip() and not line.lstrip().startswith("#"))
        self.assertNotIn("qtpy", shared_requirements)
        self.assertNotIn("qtpy", pyside_requirements)
        self.assertIn("qtpy", pyqt_requirements)

    def test_application_sources_do_not_import_a_binding_directly(self):
        project = Path(__file__).resolve().parents[1]
        checked = [project / "main.py", project / "core" / "startup.py",
                   project / "assets" / "make_icons.py"]
        checked.extend((project / "ui").glob("*.py"))
        for path in checked:
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("from PySide6", source, str(path))
            self.assertNotIn("from PyQt6", source, str(path))



class OptionalTreeSitterTests(unittest.TestCase):
    def tearDown(self):
        reset_optional_dependency_cache_for_tests()

    def test_missing_tree_sitter_is_a_normal_unavailable_state(self):
        with patch.dict(sys.modules, {"tree_sitter": None}):
            reset_optional_dependency_cache_for_tests()
            status = tree_sitter_status(refresh=True)
        self.assertFalse(status.available)
        self.assertIn("requirements-symbols.txt", status.install_command)

    def test_core_without_any_working_grammar_is_unavailable(self):
        fake_core = types.ModuleType("tree_sitter")
        with patch.dict(sys.modules, {"tree_sitter": fake_core}):
            with patch("core.tree_sitter_engine.TreeSitterEngine.available_languages", return_value=()):
                reset_optional_dependency_cache_for_tests()
                status = tree_sitter_status(refresh=True)
        self.assertFalse(status.available)
        self.assertIn("no supported language grammar", status.detail.lower())

    def test_partial_grammar_installation_reports_only_working_languages(self):
        fake_core = types.ModuleType("tree_sitter")
        with patch.dict(sys.modules, {"tree_sitter": fake_core}):
            with patch("core.tree_sitter_engine.TreeSitterEngine.available_languages",
                       return_value=("python", "javascript")):
                reset_optional_dependency_cache_for_tests()
                status = tree_sitter_status(refresh=True)
        self.assertTrue(status.available)
        self.assertEqual(status.languages, ("python", "javascript"))

    def test_requested_symbol_mode_is_omitted_when_dependency_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctrl = Controller()
            ctrl.project.root = tmp
            ctrl.project.settings.include_files = False
            ctrl.project.settings.include_system = True
            ctrl.project.settings.include_symbol = True
            unavailable = OptionalFeatureStatus(
                False, "Tree-Sitter core is missing",
                "python -m pip install -r requirements-symbols.txt", ())
            with patch("core.optional_dependencies.tree_sitter_status", return_value=unavailable):
                prompt = ctrl.build_full_xml_prompt("Inspect", "")
                root = etree.fromstring(prompt.encode("utf-8"))
                instructions = ctrl.instructions_text()
            self.assertEqual(root.find("symbol_targeting").get("available"), "false")
            self.assertIsNone(root.find("system/response_schema/response/changes/change/target/symbol"))
            self.assertNotIn("SYMBOL (Tree-Sitter) TARGETING", instructions)
            self.assertIn("Do not emit symbol targets", root.findtext("symbol_targeting/usage"))

    def test_requested_symbol_mode_lists_only_available_grammars(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctrl = Controller()
            ctrl.project.root = tmp
            ctrl.project.settings.include_files = False
            ctrl.project.settings.include_system = True
            ctrl.project.settings.include_symbol = True
            available = OptionalFeatureStatus(True, "ok", "", ("python", "javascript"))
            with patch("core.optional_dependencies.tree_sitter_status", return_value=available):
                prompt = ctrl.build_full_xml_prompt("Inspect", "")
            root = etree.fromstring(prompt.encode("utf-8"))
            self.assertEqual(root.find("symbol_targeting").get("available"), "true")
            self.assertEqual(root.findtext("symbol_targeting/available_languages"), "python, javascript")
            self.assertIn("Available grammar names in this installation: python, javascript",
                          root.findtext("system/instructions"))
            self.assertIn('path="example.py"', prompt)

        with tempfile.TemporaryDirectory() as tmp:
            ctrl = Controller()
            ctrl.project.root = tmp
            ctrl.project.settings.include_files = False
            ctrl.project.settings.include_system = True
            ctrl.project.settings.include_symbol = True
            javascript_only = OptionalFeatureStatus(True, "ok", "", ("javascript",))
            with patch("core.optional_dependencies.tree_sitter_status", return_value=javascript_only):
                prompt = ctrl.build_full_xml_prompt("Inspect", "")
            self.assertIn('path="example.js"', prompt)
            self.assertNotIn('path="example.py"', prompt)

    def test_tree_sitter_is_not_a_required_dependency(self):
        requirements = Path(__file__).resolve().parents[1] / "requirements.txt"
        symbols = Path(__file__).resolve().parents[1] / "requirements-symbols.txt"
        self.assertNotIn("tree-sitter", requirements.read_text(encoding="utf-8").lower())
        self.assertIn("tree-sitter", symbols.read_text(encoding="utf-8").lower())


if __name__ == "__main__":
    unittest.main()
