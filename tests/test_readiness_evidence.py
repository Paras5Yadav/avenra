"""Release admission rejects stale, substituted or incomplete recovery evidence."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from scripts.readiness_evidence import (
    COORDINATED_PROFILES, COMMON_OBSERVATIONS, PEER_OBSERVATIONS, XML_OBSERVATIONS, IDENTITY_OBSERVATIONS, coordinated_evidence,
)


class RecoveryEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.folder = self.root / '.local/qa/cold-example'
        self.folder.mkdir(parents=True)
        self.name = 'coordinated-peer-recovery-verification.json'

    def document(self, name=None):
        name = name or self.name
        profile, protocol, participants = COORDINATED_PROFILES[name]
        observations = (IDENTITY_OBSERVATIONS if participants == 'synthetic_identity' else
                        COMMON_OBSERVATIONS | (XML_OBSERVATIONS if protocol == 'xml' else set()) | (PEER_OBSERVATIONS if participants == 'independent' else set()))
        return {
            'passed': True, 'application_fingerprint': 'a' * 64, 'case_pack_sha256': 'b' * 64,
            'qualification_profile': profile, 'protocol_mode': protocol, 'participant_mode': participants,
            'created_at': 10, 'completed_at': 14, 'observed_end_to_end_seconds': 3,
            'set_id': 'c' * 64, 'artifact_id': 'd' * 64, 'second_recovery_set_id': 'e' * 64,
            'production_ready': False, 'official_conformance': False, 'directory': str(self.folder),
            'checks': [{'name': item, 'passed': True} for item in sorted(observations)],
        }

    def retain(self, value):
        path = self.folder / 'verification.json'
        path.write_text(json.dumps(value))
        return {**value, 'evidence': {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}}

    def evaluate(self, value, name=None):
        return coordinated_evidence(self.root, name or self.name, value, 'a' * 64, 'b' * 64)

    def test_all_profiles_accept_only_their_own_retained_mode(self):
        for produced in COORDINATED_PROFILES:
            value = self.retain(self.document(produced))
            for requested in COORDINATED_PROFILES:
                with self.subTest(produced=produced, requested=requested):
                    self.assertIs(self.evaluate(value, requested)['passed'], produced == requested)

    def test_legacy_report_with_no_explicit_participant_profile_is_refused(self):
        value = self.document()
        del value['participant_mode']
        self.assertFalse(self.evaluate(self.retain(value))['passed'])

    def test_stale_application_and_stale_case_pack_are_independently_refused(self):
        for key in ['application_fingerprint', 'case_pack_sha256']:
            with self.subTest(key=key):
                value = self.document()
                value[key] = 'f' * 64
                self.assertFalse(self.evaluate(self.retain(value))['passed'])

    def test_passed_flag_cannot_hide_failed_or_malformed_assertions(self):
        for checks in [[], None, {}, [True], [{'name': 'failure', 'passed': False}], [{'name': 'truthy', 'passed': 1}]]:
            with self.subTest(checks=checks):
                value = self.document()
                value['checks'] = checks
                self.assertFalse(self.evaluate(self.retain(value))['passed'])

    def test_missing_critical_observation_and_duplicate_names_are_refused(self):
        for mutate in [lambda c: c.pop(), lambda c: c.append(c[0].copy())]:
            value = self.document()
            mutate(value['checks'])
            self.assertFalse(self.evaluate(self.retain(value))['passed'])

    def test_top_level_pass_cannot_override_the_retained_attempt(self):
        value = self.document()
        value['passed'] = False
        summary = self.retain(value)
        summary['passed'] = True
        self.assertFalse(self.evaluate(summary)['passed'])

    def test_modified_or_missing_case_record_is_refused(self):
        value = self.retain(self.document())
        path = Path(value['evidence']['path'])
        path.write_bytes(path.read_bytes() + b' ')
        self.assertFalse(self.evaluate(value)['passed'])
        path.unlink()
        self.assertFalse(self.evaluate(value)['passed'])

    def test_external_symlink_hardlink_and_linked_parent_are_refused(self):
        original = self.retain(self.document())
        content = Path(original['evidence']['path']).read_bytes()
        outside = self.root / 'external.json'
        outside.write_bytes(content)
        external = copy.deepcopy(original)
        external['evidence']['path'] = str(outside)
        self.assertFalse(self.evaluate(external)['passed'])
        path = Path(original['evidence']['path'])
        path.unlink()
        path.symlink_to(outside)
        self.assertFalse(self.evaluate(original)['passed'])
        path.unlink()
        os.link(outside, path)
        self.assertFalse(self.evaluate(original)['passed'])
        path.unlink()
        retained = self.folder.with_name('retained')
        self.folder.rename(retained)
        (retained / 'verification.json').write_bytes(content)
        self.folder.symlink_to(retained, target_is_directory=True)
        self.assertFalse(self.evaluate(original)['passed'])

    def test_incomplete_or_nonfinite_execution_times_are_refused(self):
        for key, item in [('completed_at', 9), ('completed_at', float('nan')), ('completed_at', 10**400), ('created_at', True), ('observed_end_to_end_seconds', 40)]:
            with self.subTest(key=key, value=item):
                value = self.document()
                value[key] = item
                self.assertFalse(self.evaluate(self.retain(value))['passed'])

    def test_missing_or_reused_second_generation_identity_is_refused(self):
        for name in [self.name, 'identity-coordinated-recovery-verification.json']:
            for item in [None, '', 'c' * 64]:
                value = self.document(name)
                value['second_recovery_set_id'] = item
                self.assertFalse(self.evaluate(self.retain(value), name)['passed'])

    def test_official_or_whole_product_claim_cannot_be_inferred_from_this_gate(self):
        for key in ['production_ready', 'official_conformance']:
            value = self.document()
            value[key] = True
            self.assertFalse(self.evaluate(self.retain(value))['passed'])


if __name__ == '__main__':
    unittest.main()
