"""Acceptance and resume preserve the mandatory cold-recovery evidence boundary."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from avenra.private_config import write_document
from scripts import verify_acceptance as acceptance
from scripts.qualification_storage import CASE_PACK_HELPERS, harness_case_pack_hash
from scripts.readiness_evidence import COMMON_OBSERVATIONS, PEER_OBSERVATIONS


class AcceptanceEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        scripts = self.root / 'scripts'
        scripts.mkdir()
        self.script = 'verify_coordinated_recovery.py'
        for name in [self.script, *CASE_PACK_HELPERS[self.script], 'qualification_storage.py']:
            (scripts / name).write_text('retained fixture source: ' + name)
        self.name = 'coordinated-peer-recovery-verification.json'
        self.cases = [('Independent cold recovery', self.script, ['--independent'], self.name)]
        self.folder = self.root / '.local/qa/cold-example'
        self.folder.mkdir(parents=True)
        self.manifest = self.root / '.local/verification-attempts/example/manifest.json'
        self.manifest.parent.mkdir(parents=True)
        for name, value in [('ROOT', self.root), ('CASES', self.cases)]:
            patched = patch.object(acceptance, name, value)
            patched.start()
            self.addCleanup(patched.stop)

    def retain(self, changes=None):
        value = {
            'passed': True, 'application_fingerprint': 'a' * 64,
            'case_pack_sha256': harness_case_pack_hash(self.root, self.script),
            'qualification_profile': 'avenra-cold-independent-recovery/1',
            'protocol_mode': 'xml', 'participant_mode': 'independent',
            'created_at': 10, 'completed_at': 14, 'observed_end_to_end_seconds': 3,
            'set_id': 'c' * 64, 'artifact_id': 'd' * 64, 'second_recovery_set_id': 'e' * 64,
            'production_ready': False, 'official_conformance': False,
            'directory': str(self.folder),
            'checks': [{'name': name, 'passed': True} for name in sorted(COMMON_OBSERVATIONS | PEER_OBSERVATIONS)],
            **(changes or {}),
        }
        full = self.folder / 'verification.json'
        write_document(full, value)
        summary = {**value, 'evidence': {'path': str(full), 'sha256': hashlib.sha256(full.read_bytes()).hexdigest()}}
        receipt = self.root / '.local' / self.name
        write_document(receipt, summary)
        write_document(self.manifest, {
            'application_fingerprint': 'a' * 64,
            'matrix_definition_sha256': acceptance.matrix_definition_hash(),
            'cases': [{'name': self.cases[0][0], 'passed': True,
                       'case_pack_sha256': value['case_pack_sha256'],
                       'report': str(receipt), 'checks': len(value['checks'])}],
        })
        return summary

    def resume(self):
        return acceptance.resume_prefix(self.manifest, 'a' * 64)

    def test_intact_exact_mode_receipt_can_resume(self):
        value = self.retain()
        self.assertTrue(acceptance.qualified_receipt(self.script, self.name, value, 'a' * 64, value['case_pack_sha256']))
        prefix, provenance = self.resume()
        self.assertEqual(len(prefix), 1)
        self.assertEqual(provenance['reused_cases'], 1)

    def test_changed_guard_or_validation_helper_refuses_resume(self):
        self.retain()
        for name in ['qualification_storage.py', 'readiness_evidence.py']:
            path = self.root / 'scripts' / name
            original = path.read_bytes()
            with self.subTest(helper=name):
                path.write_bytes(original + b'\nchanged')
                with self.assertRaises(ValueError):
                    self.resume()
                path.write_bytes(original)
        self.assertEqual(len(self.resume()[0]), 1)

    def test_passing_other_mode_cannot_resume_an_independent_case(self):
        value = self.retain({'participant_mode': 'in_process', 'qualification_profile': 'avenra-cold-xml-recovery/1'})
        self.assertFalse(acceptance.qualified_receipt(self.script, self.name, value, 'a' * 64, value['case_pack_sha256']))
        with self.assertRaises(ValueError):
            self.resume()

    def test_missing_full_record_cannot_resume_from_a_passing_summary(self):
        value = self.retain()
        (self.folder / 'verification.json').unlink()
        self.assertFalse(acceptance.qualified_receipt(self.script, self.name, value, 'a' * 64, value['case_pack_sha256']))
        with self.assertRaises(ValueError):
            self.resume()


class AcceptanceMatrixTests(unittest.TestCase):
    def test_every_mandatory_cold_recovery_mode_is_scheduled_exactly_once(self):
        scheduled = [(tuple(args), report) for _, script, args, report in acceptance.CASES
                     if script == 'verify_coordinated_recovery.py']
        self.assertCountEqual(scheduled, [
            ((), 'coordinated-recovery-verification.json'),
            (('--xml',), 'coordinated-upi-wire-recovery-verification.json'),
            (('--independent',), 'coordinated-peer-recovery-verification.json'),
        ])


if __name__ == '__main__':
    unittest.main()
