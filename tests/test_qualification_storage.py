"""Observe disk admission, cross-checkout contention and actual owner death."""
import contextlib
import io
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.qualification_storage import (
    BUDGETS, FREE_FLOOR, GIB, QualificationStorage, StorageRefused, record_refusal,
)

ROOT = Path(__file__).resolve().parents[1]


class QualificationStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / 'storage'

    def guard(self, root=None):
        return QualificationStorage(root or self.root, 'coordinated_recovery', state_directory=self.state)

    def test_low_space_is_refused_before_any_qualification_case_exists(self):
        with patch.object(QualificationStorage, 'free_bytes', return_value=FREE_FLOOR + GIB):
            with self.assertRaises(StorageRefused) as rejected:
                with self.guard():
                    self.fail('Insufficient disk space allowed a rehearsal to start.')
        self.assertEqual(rejected.exception.code, 'QUALIFICATION_DISK_SPACE')
        self.assertFalse((self.root / '.local/qa').exists())
        records = list(self.state.glob('attempt-*.json'))
        self.assertEqual(len(records), 1)
        value = json.loads(records[0].read_bytes())
        self.assertEqual(value['state'], 'refused')
        self.assertIs(value['checkpoints'][0]['accepted'], False)

    def test_exact_boundary_admits_and_later_pressure_refuses_the_next_stage(self):
        boundary = FREE_FLOOR + BUDGETS['coordinated_recovery']
        with patch.object(QualificationStorage, 'free_bytes', side_effect=[boundary, boundary, boundary - 1]):
            with self.guard() as storage:
                with self.assertRaises(StorageRefused):
                    storage.checkpoint('before_next_copy')
                self.assertFalse(storage.snapshot()['checkpoints'][-1]['accepted'])
        self.assertIsNone(storage.fd)

    def test_same_volume_checkouts_cannot_overlap_the_covered_heavy_rehearsals(self):
        other = self.root / 'other-checkout'
        other.mkdir()
        with patch.object(QualificationStorage, 'free_bytes', return_value=100 * GIB):
            with self.guard():
                with self.assertRaises(StorageRefused) as rejected:
                    with self.guard(other):
                        self.fail('Competing checkout acquired an owned volume.')
                self.assertEqual(rejected.exception.code, 'QUALIFICATION_STORAGE_BUSY')
            with self.guard(other):
                pass

    def test_actual_process_death_releases_the_kernel_lease_without_deleting_its_record(self):
        code = '''
import sys, time
from scripts.qualification_storage import QualificationStorage, GIB
QualificationStorage.free_bytes=lambda self:100*GIB
with QualificationStorage(sys.argv[1], 'coordinated_recovery', state_directory=sys.argv[2]):
    print('owned',flush=True)
    time.sleep(60)
'''
        child = subprocess.Popen([sys.executable, '-B', '-c', code, str(self.root), str(self.state)],
                                 cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def reap():
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)
        self.addCleanup(reap)
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            self.assertTrue(selector.select(timeout=5), 'Child did not acquire its volume lease.')
        self.assertEqual(child.stdout.readline().strip(), 'owned')
        retained = list(self.state.glob('attempt-*.json'))
        self.assertEqual(len(retained), 1)
        with patch.object(QualificationStorage, 'free_bytes', return_value=100 * GIB):
            with self.assertRaises(StorageRefused):
                with self.guard():
                    pass
            child.kill()
            child.wait(timeout=5)
            with self.guard():
                pass
        self.assertTrue(retained[0].is_file())
        self.assertEqual(json.loads(retained[0].read_bytes())['state'], 'active')

    def test_linked_or_public_lock_state_cannot_be_used(self):
        self.state.mkdir(mode=0o700)
        lock = self.state / ('volume-' + str(self.root.stat().st_dev) + '.lock')
        outside = self.root / 'outside'
        outside.write_bytes(b'unchanged')
        lock.symlink_to(outside)
        with self.assertRaises(StorageRefused) as rejected:
            with self.guard():
                pass
        self.assertEqual(rejected.exception.code, 'QUALIFICATION_STORAGE_STATE')
        self.assertEqual(outside.read_bytes(), b'unchanged')
        lock.unlink()
        os.link(outside, lock)
        with self.assertRaises(StorageRefused):
            with self.guard():
                pass
        lock.unlink()
        lock.write_bytes(b'')
        lock.chmod(0o644)
        with self.assertRaises(StorageRefused):
            with self.guard():
                pass

    def test_admission_io_failures_remain_typed_and_release_a_partially_owned_lease(self):
        for target in ['pathlib.Path.mkdir',
                       'scripts.qualification_storage.QualificationStorage.free_bytes',
                       'scripts.qualification_storage.QualificationStorage._persist']:
            guard = self.guard()
            with self.subTest(target=target):
                with patch(target, side_effect=OSError(5, 'Storage unavailable')):
                    with self.assertRaises(StorageRefused) as rejected:
                        with guard:
                            self.fail('Unavailable storage admitted a rehearsal.')
                self.assertEqual(rejected.exception.code, 'QUALIFICATION_STORAGE_STATE')
                self.assertEqual(rejected.exception.details['errno'], 5)
                self.assertIsNone(guard.fd)
        with patch.object(QualificationStorage, 'free_bytes', return_value=100 * GIB):
            with self.guard():
                pass

    def test_free_space_floor_cannot_be_disabled_by_a_lower_or_boolean_value(self):
        for value in [0, FREE_FLOOR - 1, True]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                QualificationStorage(self.root, 'coordinated_recovery', floor_bytes=value)

    def test_refusal_replaces_only_latest_summary_and_keeps_old_case_evidence(self):
        local = self.root / '.local'
        case = local / 'qa/earlier'
        case.mkdir(parents=True)
        previous = case / 'verification.json'
        previous.write_bytes(b'previous immutable evidence')
        latest = local / 'coordinated-peer-recovery-verification.json'
        latest.write_text('{"passed":true}')
        latest.chmod(0o600)
        with contextlib.redirect_stdout(io.StringIO()):
            result = record_refusal(self.root, latest.name, StorageRefused('QUALIFICATION_DISK_SPACE', 'No room.'), 'a'*64, 'b'*64)
        self.assertEqual(result, 2)
        self.assertFalse(json.loads(latest.read_bytes())['passed'])
        self.assertEqual(previous.read_bytes(), b'previous immutable evidence')


if __name__ == '__main__':
    unittest.main()
