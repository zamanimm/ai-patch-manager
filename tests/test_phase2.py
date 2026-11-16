"""Regression tests for backups, fresh file reads, history, prompts and commands."""
import base64
import hashlib
import json
import os
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.backup_manager import BackupManager
from core import file_manager
from core.history_manager import HistoryManager
from core.process_runner import run_command
from core.prompt_builder import PromptFile, _render_files
from core.transaction import TransactionRunner
from models.change import Change, Target
from models.project import ProjectSettings
from models.transaction import Transaction


class Phase2BackupTests(unittest.TestCase):
    def test_snapshots_are_simple_independent_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            backups = Path(tmp) / "backups"
            root.mkdir()
            (root / "a.txt").write_bytes(b"same bytes\r\n")
            (root / "b.txt").write_bytes(b"same bytes\r\n")
            manager = BackupManager(str(backups))
            snap = manager.create_snapshot_dir("tx-test")
            a = manager.store_original(snap, str(root), "a.txt")
            b = manager.store_original(snap, str(root), "b.txt")
            self.assertNotEqual(a, b)
            self.assertEqual((Path(snap) / a).read_bytes(), b"same bytes\r\n")
            self.assertEqual((Path(snap) / b).read_bytes(), b"same bytes\r\n")
            self.assertFalse((backups / ".blobs").exists())
            if os.name != "nt":
                self.assertNotEqual((Path(snap) / a).stat().st_ino,
                                    (Path(snap) / b).stat().st_ino)

    def test_previous_flat_backup_names_remain_restorable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            source = root / "old.txt"
            source.write_bytes(b"modified")
            manager = BackupManager(str(Path(tmp) / "backups"))
            snap = manager.create_snapshot_dir("tx-old")
            (Path(snap) / "old.txt.orig").write_bytes(b"original")
            manager.restore(snap, str(root), "old.txt", True)
            self.assertEqual(source.read_bytes(), b"original")

    def test_distinct_paths_do_not_collide_in_new_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            for rel, data in (("a/b__c.txt", b"first"), ("a__b/c.txt", b"second")):
                file = root / rel
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(data)
            manager = BackupManager(str(Path(tmp) / "backups"))
            snap = manager.create_snapshot_dir("tx-distinct")
            first = manager.store_original(snap, str(root), "a/b__c.txt")
            second = manager.store_original(snap, str(root), "a__b/c.txt")
            self.assertNotEqual(first, second)
            self.assertEqual((Path(snap) / first).read_bytes(), b"first")
            self.assertEqual((Path(snap) / second).read_bytes(), b"second")

    def test_restore_is_byte_exact_for_binary_and_crlf_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            source = root / "data.bin"
            original = b"\x00\x01binary\xff\r\n"
            source.write_bytes(original)
            manager = BackupManager(str(Path(tmp) / "backups"))
            snap = manager.create_snapshot_dir("tx-restore")
            manager.store_original(snap, str(root), "data.bin")
            source.write_bytes(b"changed")
            restored_hash = manager.restore(snap, str(root), "data.bin", True)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(restored_hash, hashlib.sha256(original).hexdigest())

    def test_transaction_undo_redo_preserves_raw_line_endings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            source = root / "sample.txt"
            original = b"old\r\nline\r\n"
            source.write_bytes(original)
            settings = ProjectSettings()
            change = Change(
                id="chg-001", path="sample.txt", operation="replace",
                target=Target(match="old"), content="new", valid=True)
            report = TransactionRunner(
                str(root), settings, str(Path(tmp) / "backups")).apply([change])
            self.assertTrue(report.ok, report.error)
            tx = report.transaction
            history = HistoryManager(str(Path(tmp) / "history.json"),
                                     str(Path(tmp) / "backups"))
            history.record(tx)
            history.undo(str(root), tx.id)
            self.assertEqual(source.read_bytes(), original)
            history.redo(str(root), tx.id)
            self.assertEqual(source.read_bytes(), b"new\nline\n")

    def test_old_blob_directory_is_left_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            backups = Path(tmp) / "backups"
            old_blobs = backups / ".blobs"
            old_blobs.mkdir(parents=True)
            old_blob = old_blobs / ("0" * 64)
            old_blob.write_bytes(b"old backup data")
            BackupManager(str(backups))
            self.assertEqual(old_blob.read_bytes(), b"old backup data")


class Phase2HistoryTests(unittest.TestCase):
    def test_json_history_updates_are_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            history_path = Path(tmp) / "history.json"
            legacy = Transaction(id="tx-legacy", timestamp=1.0,
                                project="demo", status="applied")
            history_path.write_text(json.dumps({"transactions": [legacy.to_json_dict()]}),
                                    encoding="utf-8")
            manager = HistoryManager(str(history_path), str(Path(tmp) / "backups"))
            self.assertFalse((Path(tmp) / "history.jsonl").exists())
            updated = manager.get("tx-legacy")
            updated.status = "rolled_back"
            manager._set_status(updated)
            reopened = HistoryManager(str(history_path), str(Path(tmp) / "backups"))
            self.assertEqual(reopened.get("tx-legacy").status, "rolled_back")
            self.assertEqual(len(reopened.list_transactions()), 1)
            self.assertEqual(list(Path(tmp).glob('.history-*.tmp')), [])

    def test_journal_is_migrated_with_latest_status_and_retained_as_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            journal = Path(tmp) / "history.jsonl"
            old = Transaction(id="tx-1", timestamp=1.0, status="applied")
            newer = Transaction(id="tx-1", timestamp=1.0, status="rolled_back")
            added = Transaction(id="tx-2", timestamp=2.0, status="applied")
            path.write_text(json.dumps({"transactions": [old.to_json_dict()]}), encoding="utf-8")
            journal.write_text("\n".join(json.dumps(obj.to_json_dict()) for obj in
                                          (old, newer, added)) + "\n{partial-json",
                               encoding="utf-8")
            manager = HistoryManager(str(path), str(Path(tmp) / "backups"))
            self.assertEqual(manager.get("tx-1").status, "rolled_back")
            self.assertEqual(manager.transaction_count(), 2)
            self.assertFalse(journal.exists())
            self.assertEqual(len(list(Path(tmp).glob("history.jsonl.migrated-*"))), 1)
            manager.record(Transaction(id="tx-3", timestamp=3.0))
            again = HistoryManager(str(path), str(Path(tmp) / "backups"))
            self.assertEqual(again.transaction_count(), 3)
            self.assertEqual(again.get("tx-1").status, "rolled_back")

    def test_raw_hash_verification_is_strict_but_legacy_hashes_remain_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "line-endings.txt"
            path.write_bytes(b"same\n")
            manager = HistoryManager(str(Path(tmp) / "history.json"),
                                     str(Path(tmp) / "backups"))
            raw_hash = hashlib.sha256(b"same\n").hexdigest()
            legacy_hash = hashlib.sha256("same\n".encode("utf-8")).hexdigest()
            path.write_bytes(b"same\r\n")
            self.assertFalse(manager._matches_record_hash(
                str(path), raw_hash, "raw-sha256"))
            self.assertTrue(manager._matches_record_hash(
                str(path), legacy_hash, "legacy-text"))

    def test_json_history_pagination_and_previous_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = HistoryManager(str(Path(tmp) / "history.json"),
                                     str(Path(tmp) / "backups"))
            for index in range(5):
                history.record(Transaction(id=f"tx-{index}", timestamp=float(index),
                                           summary=f"transaction {index}"))
            self.assertEqual([t.id for t in history.list_transactions(limit=2)],
                             ["tx-4", "tx-3"])
            self.assertEqual([t.id for t in history.list_transactions(limit=2, offset=2)],
                             ["tx-2", "tx-1"])
            self.assertEqual(history.previous_transaction("tx-3").id, "tx-2")
            self.assertEqual(history.transaction_count(), 5)
            records = json.loads((Path(tmp) / "history.json").read_text(encoding="utf-8"))
            self.assertEqual(len(records["transactions"]), 5)


class Phase2PromptDirectReadTests(unittest.TestCase):
    def test_binary_prompt_content_is_omitted_unless_opted_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "asset.bin"
            raw = b"\x00\x01binary-data"
            path.write_bytes(raw)
            omitted = file_manager.read_prompt_snapshot(str(path))
            included = file_manager.read_prompt_snapshot(str(path), include_binary=True)
            self.assertTrue(omitted.is_binary)
            self.assertIn("binary file omitted", omitted.content)
            self.assertNotIn(base64.b64encode(raw).decode("ascii"), omitted.content)
            self.assertIn(base64.b64encode(raw).decode("ascii"), included.content)
            self.assertEqual(omitted.sha256, hashlib.sha256(raw).hexdigest())

    def test_large_binary_is_omitted_but_full_sha256_is_computed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.bin"
            raw = b"\x00" + b"a" * (2 * 1024 * 1024)
            path.write_bytes(raw)
            snap = file_manager.read_prompt_snapshot(str(path))
            self.assertTrue(snap.is_binary)
            self.assertEqual(snap.size, len(raw))
            self.assertEqual(snap.sha256, hashlib.sha256(raw).hexdigest())
            self.assertLess(len(snap.content), 200)

    def test_multiple_selected_existing_files_are_rendered_with_real_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = {
                "src/config/vehicle.py": "class VehicleConfig:\n    pass\n",
                "src/controllers/pure_pursuit.py": "def follow():\n    return 1\n",
                "src/models/kinematic_bicycle.py": "class KinematicBicycle:\n    pass\n",
                "src/simulation/integrators.py": "def integrate():\n    return None\n",
                "src/simulation/simulator.py": "class VehicleSimulator:\n    pass\n",
                "tests/test_integrators.py": "def test_integrate():\n    assert True\n",
                "tests/test_kinematic_bicycle.py": "def test_bicycle():\n    assert True\n",
                "tests/test_simulator.py": "def test_simulator():\n    assert True\n",
            }
            for rel, content in files.items():
                target = Path(tmp) / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            prompt = _render_files([PromptFile(path=rel) for rel in files], tmp)
            self.assertNotIn("[file does not exist yet]", prompt)
            for rel, content in files.items():
                self.assertIn(f'<file path="{rel}" mode="full"', prompt)
                self.assertIn(content.strip(), prompt)

    def test_prompt_builder_reports_raw_hash_and_compact_binary_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "image.dat"
            raw = b"\x00PNG-ish-binary-data"
            path.write_bytes(raw)
            hashes = {}
            prompt = _render_files([PromptFile(path="image.dat")], tmp,
                                   snapshot_hashes=hashes)
            self.assertIn('mode="binary"', prompt)
            self.assertIn("binary file omitted", prompt)
            self.assertEqual(hashes["image.dat"], hashlib.sha256(raw).hexdigest())

    def test_each_prompt_reloads_current_disk_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "code.py"
            path.write_text("print('old')", encoding="utf-8")
            first = _render_files([PromptFile(path="code.py")], tmp)
            path.write_text("print('new')", encoding="utf-8")
            second = _render_files([PromptFile(path="code.py")], tmp)
            self.assertIn("print('old')", first)
            self.assertIn("print('new')", second)
            self.assertNotIn("print('old')", second)

    def test_prompt_sees_direct_file_manager_write_without_invalidation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "code.py"
            path.write_text("old", encoding="utf-8")
            first = file_manager.read_prompt_snapshot(str(path))
            file_manager.write_text(str(path), "new")
            second = file_manager.read_prompt_snapshot(str(path))
            self.assertEqual(first.content, "old")
            self.assertEqual(second.content, "new")

    def test_binary_setting_round_trips_through_workspace_and_xml_models(self):
        from models.project import Project, save_project_xml, load_project_xml
        with tempfile.TemporaryDirectory() as tmp:
            project = Project(name="binary-test", root=tmp)
            project.settings.include_binary_files = True
            xml_path = Path(tmp) / "project.xml"
            save_project_xml(str(xml_path), project)
            loaded = load_project_xml(str(xml_path))
            self.assertTrue(loaded.settings.include_binary_files)
            restored = Project.from_dict(project.to_dict())
            self.assertTrue(restored.settings.include_binary_files)

    def test_direct_read_raises_for_missing_selected_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.py"
            with self.assertRaises(FileNotFoundError):
                file_manager.read_prompt_snapshot(str(path))

    def test_prompt_builder_does_not_label_unreadable_selected_file_as_new(self):
        from core.prompt_builder import PromptFileUnavailableError
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "existing.py"
            path.write_text("print('exists')\n", encoding="utf-8")
            with patch.object(file_manager, "read_prompt_snapshot",
                              side_effect=PermissionError("access denied")):
                with self.assertRaises(PromptFileUnavailableError) as ctx:
                    _render_files([PromptFile(path="existing.py")], tmp)
            self.assertIn("but access was denied", str(ctx.exception))
            self.assertNotIn("file does not exist yet", str(ctx.exception))

    def test_prompt_builder_reports_transient_read_error(self):
        from core.prompt_builder import PromptFileUnavailableError
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "existing.py"
            path.write_text("def run():\n    return 'ok'\n", encoding="utf-8")
            with patch.object(file_manager, "read_prompt_snapshot",
                              side_effect=OSError("disk I/O failed")):
                with self.assertRaises(PromptFileUnavailableError) as ctx:
                    _render_files([PromptFile(path="existing.py")], tmp)
            self.assertIn("disk I/O failed", str(ctx.exception))
            self.assertNotIn("file does not exist yet", str(ctx.exception))

    def test_prompt_builder_aborts_if_selected_file_disappears(self):
        from core.prompt_builder import PromptFileUnavailableError
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PromptFileUnavailableError) as ctx:
                _render_files([PromptFile(path="deleted_after_scan.py")], tmp)
            self.assertIn("not present under project root", str(ctx.exception))
            self.assertNotIn("file does not exist yet", str(ctx.exception))




class Phase2ProcessTests(unittest.TestCase):
    def test_command_streams_output_and_caps_returned_tail(self):
        code = "import sys; sys.stdout.write('x' * 12000); sys.stderr.write('err')"
        command = (f'"{sys.executable}" -c "{code}"' if os.name == "nt"
                   else f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}")
        chunks = []
        result = run_command(command, cwd=os.getcwd(), max_output_bytes=4096,
                             on_output=lambda stream, text: chunks.append((stream, text)))
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(result.output_truncated)
        self.assertEqual(len(result.stdout), 4096)
        self.assertIn(("stderr", "err"), chunks)
        self.assertEqual(sum(len(text) for stream, text in chunks if stream == "stdout"),
                         12000)


if __name__ == "__main__":
    unittest.main()
