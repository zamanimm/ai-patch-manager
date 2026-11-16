"""Regression tests for simplifying Phase 2/3 storage without losing old data."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.backup_manager import BackupManager
from core.history_manager import HistoryError, HistoryManager
from models.transaction import FileRecord, Transaction


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class SimplificationSafetyTests(unittest.TestCase):
    def test_previous_journal_and_backup_restore_through_undo_redo(self):
        """Migrate Phase 2 data and restore from previous flat .orig/.new names."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / 'project'
            project.mkdir()
            work = project / '.ai-patch-manager'
            work.mkdir()
            backups = work / 'backups'
            backups.mkdir()
            snap = backups / 'old-tx'
            snap.mkdir()
            (snap / 'code.py.orig').write_bytes(b'old\r\n')
            (snap / 'code.py.new').write_bytes(b'new\n')
            path = project / 'code.py'
            path.write_bytes(b'new\n')
            tx = Transaction(id='tx-old', timestamp=1.0, project='project',
                             status='applied', backup_dir=str(snap),
                             files=[FileRecord(path='code.py', operation='replace',
                                               change_ids=['chg-001'],
                                               old_hash=digest(b'old\r\n'),
                                               new_hash=digest(b'new\n'),
                                               existed_before=True,
                                               backup_file='code.py.orig',
                                               hash_format='raw-sha256')])
            journal = work / 'history.jsonl'
            journal.write_text(json.dumps(tx.to_json_dict()) + '\n', encoding='utf-8')
            manager = HistoryManager(str(work / 'history.json'), str(backups))
            self.assertEqual(manager.transaction_count(), 1)
            self.assertFalse(journal.exists())
            manager.undo(str(project), 'tx-old')
            self.assertEqual(path.read_bytes(), b'old\r\n')
            manager.redo(str(project), 'tx-old')
            self.assertEqual(path.read_bytes(), b'new\n')
            reread = HistoryManager(str(work / 'history.json'), str(backups))
            self.assertEqual(reread.get('tx-old').status, 'applied')

    def test_new_backup_names_support_two_colliding_old_paths_in_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'src'
            paths = {'a/b__c.py': b'one\n', 'a__b/c.py': b'two\n'}
            for rel, content in paths.items():
                file = root / rel
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(content)
            backup = BackupManager(str(Path(tmp) / 'backups'))
            snap = backup.create_snapshot_dir('two-files')
            for rel in paths:
                backup.store_original(snap, str(root), rel)
                (root / rel).write_bytes(b'changed\n')
            for rel, content in paths.items():
                backup.restore(snap, str(root), rel, True)
                self.assertEqual((root / rel).read_bytes(), content)

    def test_history_write_failure_does_not_modify_existing_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'history.json'
            m = HistoryManager(str(source), str(Path(tmp) / 'backups'))
            m.record(Transaction(id='kept', timestamp=1.0))
            before = source.read_bytes()
            original_replace = os.replace

            def fail_history_replace(src, dst):
                if str(dst) == str(source):
                    raise OSError('simulated disk error')
                return original_replace(src, dst)

            with patch('core.history_manager.os.replace', side_effect=fail_history_replace):
                with self.assertRaises(OSError):
                    m.record(Transaction(id='lost', timestamp=2.0))
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(HistoryManager(str(source), str(Path(tmp) / 'backups')).transaction_count(), 1)
            self.assertFalse(list(Path(tmp).glob('.history-*.tmp')))

    def test_corrupt_json_never_silently_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'history.json'
            source.write_text('{broken', encoding='utf-8')
            manager = HistoryManager(str(source), str(Path(tmp) / 'backups'))
            with self.assertRaises(HistoryError):
                manager.record(Transaction(id='new'))
            self.assertEqual(source.read_text(encoding='utf-8'), '{broken')

    def test_archived_blob_files_are_never_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            backups = Path(tmp) / 'backups'
            blob = backups / '.blobs' / ('a' * 64)
            blob.parent.mkdir(parents=True)
            blob.write_bytes(b'old-data')
            BackupManager(str(backups)).create_snapshot_dir('new')
            self.assertEqual(blob.read_bytes(), b'old-data')


if __name__ == '__main__':
    unittest.main()
