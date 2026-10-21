import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from avenra.private_config import write_document
from scripts import verify_work_queue as queue


class WorkQueueEvidenceTests(unittest.TestCase):
    def fixture(self, root):
        path = root / '.local/qa/case/verification.json'
        path.parent.mkdir(parents=True, mode=0o700)
        value = {'passed': True, 'checks': [{'name': str(index), 'passed': True} for index in range(35)],
                 'application_fingerprint': 'build', 'case_pack_sha256': 'case', 'predecessor_artifact': 'old', 'candidate_artifact': 'new',
                 'payments': {'predecessor': 'original', 'automatic': 'automatic', 'exhausted': 'exhausted'}, 'created_at': 1, 'completed_at': 2}
        write_document(path, value)
        value['details'] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        return path, value

    def test_intact_attempt_is_required_and_changed_summary_is_refused(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(queue, 'ROOT', Path(directory).resolve()):
            path, value = self.fixture(Path(directory))
            self.assertTrue(queue.complete_evidence(value))
            self.assertFalse(queue.complete_evidence({**value, 'candidate_artifact': 'changed'}))
            path.write_bytes(path.read_bytes() + b' ')
            self.assertFalse(queue.complete_evidence(value))

    def test_failed_missing_or_linked_attempt_is_refused(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(queue, 'ROOT', Path(directory).resolve()):
            path, value = self.fixture(Path(directory))
            value['checks'][0]['passed'] = False
            write_document(path, {key: item for key, item in value.items() if key != 'details'})
            value['details']['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertFalse(queue.complete_evidence(value))
            alias = path.parent / 'alias.json'
            alias.symlink_to(path)
            self.assertFalse(queue.complete_evidence({**value, 'details': {**value['details'], 'path': str(alias)}}))
            path.unlink()
            self.assertFalse(queue.complete_evidence(value))

    def test_malformed_receipts_cannot_raise_or_qualify(self):
        for value in [None, [], {}, {'details': []}, {'details': {'path': None}}, {'details': {'path': '.'}}]:
            with self.subTest(value=value):
                self.assertFalse(queue.complete_evidence(value))
