"""Explicit simulator rules and evidence provenance, never an official wire spec."""
import hashlib
from pathlib import Path

from avenra.util import application_fingerprint, digest

PROFILE = {
    'id': 'avenra-lifecycle-lab/1', 'authority': 'synthetic', 'currency': 'INR/2',
    'rules': {'challenge_seconds': 120, 'challenge_attempts': 3, 'pin_digits': 6,
              'pin_attempts': 3, 'pin_lock_seconds': 300, 'reset_cooling_seconds': 60,
              'per_payment_paise': 200_000, 'daily_paise': 500_000, 'daily_count': 10,
              'day_offset_seconds': 19800, 'complaint_seconds': 172800,
              'minimum_mandate_interval_seconds': 60, 'maximum_occurrences': 366,
              'maximum_report_items': 5000},
    'sources': [
        {'id': 'PUBLIC-CZ', 'url': 'https://www.npcisupport.org.in/portal/en/kb/certification/3-cz-certification-testing-tool-for-upi-aeps-imps-faqs',
         'checked_on': '2026-10-18', 'use': 'Access and acknowledgement concepts only. The current technical specification is not supplied by this page.'},
        {'id': 'PUBLIC-OPERATIONS', 'url': 'https://www.npcisupport.org.in/portal/en/kb/upi/upi',
         'checked_on': '2026-10-18', 'use': 'Public operations access route. No settlement file or deadline is inferred from this page.'},
    ],
    'scope': 'Avenra synthetic lifecycle and synthetic settlement reports. All numeric rules and message names below are simulator choices, not official UPI limits or certification vectors.',
}
PROFILE_HASH = digest(PROFILE)

REQUIREMENTS = [
    ('PROFILE', 'Immutable scoped profile and recorded synthetic assumptions'),
    ('DISCOVERY', 'Only the proved device customer can discover active accounts'),
    ('CHALLENGE', 'Setup/reset challenges expire, have bounded attempts and cannot be substituted'),
    ('CREDENTIAL', 'Synthetic PINs use salted scrypt; wrong attempts persist and lock'),
    ('RESET', 'Reset increments the credential epoch, starts cooling and invalidates unattempted approvals'),
    ('AUTH', 'Credential approval binds the exact intent and cannot be bypassed through the core route'),
    ('LIMIT', 'Concurrent approvals reserve daily amount/count; unresolved effects retain reservations'),
    ('RECOVERY', 'A restart/retry preserves the original intent, guard and financial identities'),
    ('MANDATE', 'Recurring agreements bind merchant, amount, device, validity and approval'),
    ('OCCURRENCE', 'Due occurrence identity, notifications and replay protection govern participant payments'),
    ('REVOCATION', 'Pause/revocation prevents a first mandate debit without abandoning uncertain effects'),
    ('COMPLAINT', 'Scoped disputes retain history, deadlines, evidence and original payment'),
    ('RESOLUTION', 'A second authenticated principal approves a resolution supported by actual financial evidence'),
    ('CYCLE', 'Cutoffs allocate each completed payment once, retaining unresolved and late items'),
    ('RECONCILE', 'Independent observations produce retained reconciliation exceptions'),
    ('RELEASE', 'A second authenticated principal releases only an unchanged matched synthetic report'),
    ('EVIDENCE', 'Report hashes/signatures bind source, profile, rules and observed journal identities'),
    ('SCOPE', 'Every lifecycle operation enforces workspace, environment and resource scope'),
    ('HTTP', 'Serving enforces authentication, CSRF, origin, framing and request budgets'),
    ('PRESERVE', 'Existing financial engines and workflows remain intact; new modules have additive registration points'),
    ('RESTORE', 'Extension state and credentials survive an isolated backup/restore'),
    ('ARCHIVE', 'Completed recovery files encrypt with authenticated encryption and reject changed ciphertext or keys'),
]
EXTERNAL = [
    {'id': 'OFFICIAL-WIRE', 'state': 'unqualified', 'requires': 'Selected current role-specific messages, security, codes, eligibility, timing and authoritative acceptance cases.'},
    {'id': 'BANK-UAT', 'state': 'unqualified', 'requires': 'Implemented authorized partner adapter and observed interoperability; test identities/connectivity alone are insufficient.'},
    {'id': 'HOSTING', 'state': 'unqualified', 'requires': 'Institutional deployment, SSO policy acceptance, all-service trust, managed keys, independent review, load, HA and off-host recovery qualification.'},
    {'id': 'VM', 'state': 'unqualified', 'requires': 'Actual Linux/KVM worker deployment, isolation, scoped credentials and recovery.'},
    {'id': 'AA', 'state': 'unqualified', 'requires': 'Separate role/version-specific AA interoperability implementation and independent acceptance.'},
    {'id': 'UPI-VARIANTS', 'state': 'unsupported', 'requires': 'Separate selected profiles for specialized products, including stored value, delegated, credit and cross-border journeys.'},
]


def fingerprint():
    return application_fingerprint()


def case_pack_hash():
    path = Path(__file__).resolve().parents[1] / 'scripts/verify_fidelity.py'
    return hashlib.sha256(path.read_bytes()).hexdigest()
