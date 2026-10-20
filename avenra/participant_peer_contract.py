"""Owned independent participant inbox transport, separate from financial legs."""
from .util import digest

ADAPTER = 'avenra-xml-tls-participant/1'
ROLES = ['payer_psp', 'network', 'payee_psp']
CONTRACT = {
    'id': 'avenra-participant-inbox/1', 'authority': 'synthetic',
    'roles': ROLES, 'transport': 'mutual_tls', 'authentication': 'Ed25519',
    'commands': ['probe', 'deliver'], 'request_lifetime_seconds': 30,
    'maximum_bytes': 32768, 'maximum_inbox_messages': 50000, 'maximum_registry_keys': 128,
    'rules': ['original_xml_and_message_identity', 'scoped_pinned_public_registry',
              'durable_receipt_before_ack', 'stable_receipt_replay',
              'conflicting_phase_refused', 'signed_synthetic_scenario_clock',
              'original_transport_response_replay', 'no_financial_authority',
              'unknown_is_not_declined', 'no_implicit_local_fallback'],
}
CONTRACT_HASH = digest(CONTRACT)
SCOPE_FIELDS = ['worker_id', 'tenant_id', 'environment_id', 'participant_id', 'role']

# Deliberately owned literal receiver policy, not imported from the coordinator.
# A receiver observes its inbound messages; it cannot infer a bank balance or
# prove a money movement merely because a notification was accepted.
RECEIVERS = {
    'network': {-1: ('payee_psp', 'collect.request', 'collect_received'),
                1: ('payer_psp', 'payment.submit', 'submission_received'),
                3: ('remitter', 'debit.response', 'debit_report_received'),
                5: ('beneficiary', 'credit.response', 'credit_report_received')},
    'payer_psp': {0: ('network', 'collect.present', 'collect_presented'),
                  6: ('network', 'payment.result', 'result_received')},
    'payee_psp': {7: ('network', 'merchant.notification', 'merchant_notified')},
}
