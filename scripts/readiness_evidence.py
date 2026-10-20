"""Validate retained cold-recovery evidence without executing a qualification."""
import hashlib
import json
import math
from pathlib import Path
import re
import stat


COORDINATED_PROFILES = {
    'coordinated-recovery-verification.json': ('avenra-cold-recovery/1', 'legacy', 'in_process'),
    'coordinated-upi-wire-recovery-verification.json': ('avenra-cold-xml-recovery/1', 'xml', 'in_process'),
    'coordinated-peer-recovery-verification.json': ('avenra-cold-independent-recovery/1', 'xml', 'independent'),
    'identity-coordinated-recovery-verification.json': ('avenra-cold-identity-recovery/1', 'xml', 'synthetic_identity'),
}

# These observations distinguish actual cold recovery from a startup smoke test.
# All other recorded assertions must pass too. The case-pack hash binds their
# implementation; these summaries are engineering evidence, not attestation.
COMMON_OBSERVATIONS = {
    'Staging rebuilds a new offline runtime and retains the inactive guard',
    'New-role imports preserve every retained table row and sequence; exact replay reuses the original new databases',
    'Restored banks confirm the same original credit once after endpoint relocation and credential rotation',
    'Refused incompatible selection preserves every restored control row, sequence and the original release pointer',
    'Qualification retained one fixed build and one fixed case pack',
    'Cold capture refuses a live synthetic issuer after the other participants have drained',
    'A staged synthetic issuer cannot start with copied source credentials',
    'A recovered synthetic issuer cannot reuse its original listener after activation',
    'An otherwise routing-compatible signed predecessor without the issuer lifetime fence is refused',
}
PEER_OBSERVATIONS = {
    'All five independent restored listeners are owned by the verified offline runtime',
    'An activated participant client refuses a missing route instead of source fallback',
    'Network restart completes the original accepted payment with valid evidence and one native posting per leg',
    'A second cold generation retains original signed inbox receipts after both preceding installations disappear',
    'New financial work completes after a second cold restore with each original native leg posted once',
    'Neither restoration contacted an original source participant address',
}
XML_OBSERVATIONS = {
    'A signed negative candidate missing retained XML contracts is refused despite supporting every runtime route and issuer fence',
    'Contract capability refusal preserves every restored control row, sequence and release pointer',
}
IDENTITY_OBSERVATIONS = {
    'Actual operator death leaves an owned inactive issuer that exact retry reuses',
    'An inactive replacement issuer proves its keys but refuses authorization and tokens',
    'A different certificate under the original CA receives no confidential request bytes',
    'Recovered sessions preserve every original subject, expiry and grant row',
    'Fresh recovered HTTPS sign-in preserves the original principal and approval identity',
    'Recovered sign-in cannot approve the same person’s original proposal',
    'Old code and interaction bindings cannot be redirected to the recovered callback',
    'Original uncertain bank credit is confirmed once through recovered HTTPS identity',
    'A genuine signed predecessor without issuer routing is refused before any control change',
    'Actual issuer death is restarted under the same authenticated recovery decision',
    'Second-generation HTTPS sign-in retains the original subject and revoked grant',
    'New financial work completes after two cold identity recoveries with each original native leg posted once',
    'Neither identity recovery contacted a preceding source listener',
    'Qualification retained one fixed build and one fixed case pack',
}


def _hash(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def _time(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value > 0
    except OverflowError:
        return False


def coordinated_evidence(root, name, value, fingerprint, case_pack_sha256):
    """Require the right mode, complete passing checks and an intact case record.

    The caller supplies the currently installed source and case-pack identities.
    A report from another recovery mode cannot satisfy this gate. Evidence is
    bounded to a regular, unlinked file in this checkout's private QA directory.
    """
    expected = COORDINATED_PROFILES[name]
    checks = value.get('checks')
    valid_checks = isinstance(checks, list) and bool(checks) and all(
        isinstance(item, dict) and isinstance(item.get('name'), str) and bool(item['name'])
        and item.get('passed') is True for item in checks)
    names = [item['name'] for item in checks] if valid_checks else []
    required = (IDENTITY_OBSERVATIONS if expected[2] == 'synthetic_identity' else
                COMMON_OBSERVATIONS | (XML_OBSERVATIONS if expected[1] == 'xml' else set()) | (PEER_OBSERVATIONS if expected[2] == 'independent' else set()))
    timings = all(_time(value.get(key)) for key in ['created_at', 'completed_at', 'observed_end_to_end_seconds'])
    result = {
        'application_matches': value.get('application_fingerprint') == fingerprint,
        'case_pack_matches': value.get('case_pack_sha256') == case_pack_sha256,
        'protocol_mode_matches': value.get('protocol_mode') == expected[1],
        'participant_mode_matches': value.get('participant_mode') == expected[2],
        'qualification_profile_matches': value.get('qualification_profile') == expected[0],
        'assertions_passed': valid_checks and len(names) == len(set(names)) and required <= set(names),
        'run_completed': timings and value['completed_at'] >= value['created_at']
            and value['observed_end_to_end_seconds'] <= value['completed_at'] - value['created_at'] + 1,
        'recovery_identity_matches': _hash(value.get('set_id')) and _hash(value.get('artifact_id')),
        'scope_matches': value.get('production_ready') is False and value.get('official_conformance') is False,
        'complete_evidence_matches': False,
    }
    if expected[2] in {'independent', 'synthetic_identity'}:
        result['second_generation_matches'] = _hash(value.get('second_recovery_set_id')) and value['second_recovery_set_id'] != value.get('set_id')
    try:
        evidence = value.get('evidence', {})
        path = Path(evidence['path'])
        qa = Path(root).resolve() / '.local/qa'
        if qa.is_symlink() or qa.parent.is_symlink():
            raise ValueError('Evidence root contains a linked path.')
        # Reject links before resolving; resolution alone conceals a linked
        # evidence file or an intermediate directory outside the retained run.
        relative = path.relative_to(qa)
        if not path.is_absolute() or '..' in relative.parts or path.name != 'verification.json':
            raise ValueError('Evidence is outside the retained case directory.')
        cursor = qa
        for part in relative.parts:
            cursor /= part
            if cursor.is_symlink():
                raise ValueError('Evidence contains a linked path.')
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 2_000_000:
            raise ValueError('Evidence must be a bounded unlinked file.')
        content = path.read_bytes()
        retained = json.loads(content)
        result['complete_evidence_matches'] = (
            hashlib.sha256(content).hexdigest() == evidence.get('sha256')
            and retained == {key: item for key, item in value.items() if key != 'evidence'}
            and Path(value.get('directory', '')).resolve() == path.parent.resolve())
    except (OSError, ValueError, TypeError, KeyError):
        pass
    result['passed'] = value.get('passed') is True and all(result.values())
    return result
