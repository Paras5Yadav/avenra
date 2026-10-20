"""Durable participant payment simulation, with an explicitly owned lab contract.

Role boundaries, delivery and accounting effects execute here. This is not a
current NPCI wire implementation; unresolved official contracts remain blocked.
"""
import base64
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import time
from urllib.parse import parse_qsl, urlencode, urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .banking import BankingBusy, FAULTS, TERMINAL
from .errors import DomainError, require
from .simulation import business_time, integer
from .util import canonical, digest, label, money, now, reference, uid

PROFILE = {
    'id': 'avenra-upi-participants/1', 'authority': 'synthetic', 'currency': 'INR',
    'message_format': 'avenra-payment-message/1', 'message_lifetime_seconds': 300,
    'roles': ['payer_psp', 'network', 'remitter', 'beneficiary', 'payee_psp'],
    'features': ['device_enrolment', 'account_linking', 'address_resolution', 'balance_inquiry',
                 'push_payment', 'merchant_collect', 'qr_intent_subset', 'explicit_authorization',
                 'independent_actor_views', 'debit_credit_routing', 'status_recovery', 'partial_refund',
                 'key_rotation_revocation', 'delivery_faults', 'signed_evidence'],
    'rules': [
        {'id': 'UPILAB-DEVICE', 'rule': 'An active proved device may authorize only its linked customer account.'},
        {'id': 'UPILAB-AUTH', 'rule': 'Authorization binds the exact immutable intent, device, amount and destination.'},
        {'id': 'UPILAB-COLLECT', 'rule': 'Only merchants initiate collect; presentation precedes payer approval.'},
        {'id': 'UPILAB-ROUTE', 'rule': 'An authenticated participant message drives each ordered financial phase.'},
        {'id': 'UPILAB-IDENTITY', 'rule': 'Transport retries and status recovery retain original financial identities.'},
        {'id': 'UPILAB-UNKNOWN', 'rule': 'Unconfirmed bank effects stay unresolved; elapsed time cannot prove a decline.'},
        {'id': 'UPILAB-FIRST-DEBIT', 'rule': 'A revoked device, expired approval or blocked account prevents a first debit; previously attempted effects retain their recovery identity.'},
        {'id': 'UPILAB-KEY', 'rule': 'Ed25519 verification binds the key, sender, recipient, profile and environment.'},
        {'id': 'UPILAB-REFUND', 'rule': 'Linked refunds reserve the remaining original amount atomically.'},
        {'id': 'UPILAB-ORACLE', 'rule': 'Evidence checks independently read authoritative postings for each leg.'},
    ],
    'unqualified': ['Current official UPI XML/security/codes/timing and feature eligibility',
                    'Bank participant substitution using authorized UAT interfaces and acceptance packs',
                    'Production hosting, independent security and operational acceptance',
                    'Official dispute, mandate, settlement and regulatory reporting interfaces'],
    'public_references': [
        'https://www.npci.org.in/what-we-do/upi/faqs',
        'https://www.npci.org.in/circulars/upi',
        'https://www.npci.org.in/PDF/npci/certifications-zone/upi/UPI-user-manual-Bank.pdf',
    ],
    'scope': 'Executable Avenra lab behaviour. Public ecosystem references are not a current technical specification.',
}
PROFILE_HASH = digest(PROFILE)
ROUTES = {
    -1: ('collect.request', 'payee_psp', 'network'), 0: ('collect.present', 'network', 'payer_psp'),
    1: ('payment.submit', 'payer_psp', 'network'), 2: ('debit.request', 'network', 'remitter'),
    3: ('debit.response', 'remitter', 'network'), 4: ('credit.request', 'network', 'beneficiary'),
    5: ('credit.response', 'beneficiary', 'network'), 6: ('payment.result', 'network', 'payer_psp'),
    7: ('merchant.notification', 'network', 'payee_psp'),
}
FINAL = TERMINAL | {'expired', 'cancelled'}
DELIVERY_FAULTS = {'drop_once', 'duplicate_once', 'lose_ack_once', 'corrupt_once', 'delay_once'}


def postgres_migration():
    text = Path(__file__).with_name('migrations').joinpath('008_upi.sql').read_text()
    def trigger(match):
        name, operation, table, condition = match.groups()
        when = ' WHEN (OLD.evidence_json IS NOT NULL)' if condition else ''
        return f'CREATE TRIGGER {name} BEFORE {operation} ON {table} FOR EACH ROW{when} EXECUTE FUNCTION avenra_immutable_record();'
    text = re.sub(r"CREATE TRIGGER ([a-z_]+) BEFORE (UPDATE|DELETE) ON ([a-z_]+) (WHEN OLD.evidence_json IS NOT NULL )?BEGIN SELECT RAISE\(ABORT,'[^']+'\); END;", trigger, text)
    return re.sub(r'\bREAL\b', 'DOUBLE PRECISION', re.sub(r'\bINTEGER\b', 'BIGINT', text))


def fields(data, required, optional=()):
    require(isinstance(data, dict) and set(data) >= set(required) and set(data) <= set(required) | set(optional),
            'INVALID_UPI_REQUEST', 'Use exactly the supported participant API fields.')


class Upi:
    def __init__(self, app):
        self.app, self.store, self.bank = app, app.store, app.banking
        from .participant_connections import ParticipantConnections
        self.connections = ParticipantConnections(self)

    def get(self, db, table, tenant, env, identity):
        return self.bank._get(db, table, tenant, env, identity)

    def clock(self, tenant, env, db=None):
        return business_time(self.store, tenant, env, db)

    def _private_key(self, participant, epoch):
        seed = hmac.new(self.store.signing_key, ('upi-key:' + participant + ':' + str(epoch)).encode(), hashlib.sha256).digest()
        return Ed25519PrivateKey.from_private_bytes(seed)

    def _new_key(self, db, participant, epoch):
        identity = participant + '_k' + str(epoch)
        public = self._private_key(participant, epoch).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
        db.execute('INSERT INTO upi_keys VALUES(?,?,?,?,?,?)', (identity, participant, epoch, public, 0, now()))
        return identity

    def participants(self, tenant, env, data):
        fields(data, [])
        self.app.environment(tenant, env, True)
        with self.store.transaction() as db:
            banks = [dict(x) for x in db.execute("SELECT * FROM bank_institutions WHERE tenant_id=? AND environment_id=? AND status='active'", (tenant, env))]
            actors = [(None, 'network', 'Avenra Network', 'network')]
            actors += [(b['id'], r, b['name'] + ' · ' + r.replace('_', ' '), b['id'] + ':' + r) for b in banks for r in sorted(set(json.loads(b['roles_json'])))]
            for bank, role, name, key in actors:
                if db.execute('SELECT 1 FROM upi_participants WHERE tenant_id=? AND environment_id=? AND actor_key=?', (tenant, env, key)).fetchone():
                    continue
                identity = uid('actor')
                db.execute('INSERT INTO upi_participants VALUES(?,?,?,?,?,?,?,?,?,?)', (identity, tenant, env, key, bank, role, name, 'active', 1, now()))
                self._new_key(db, identity, 1)
                self.store.audit(db, tenant, env, 'upi.participant_registered', identity, {'role': role, 'bank_id': bank})
        return self.overview(tenant, env)

    def participant_action(self, tenant, env, identity, action, data):
        fields(data, [] if action in {'rotate-key', 'pause', 'resume'} else ['key_id'])
        with self.store.transaction() as db:
            actor = self.get(db, 'upi_participants', tenant, env, identity)
            if action in {'pause', 'resume'}:
                db.execute('UPDATE upi_participants SET status=? WHERE id=?', ('paused' if action == 'pause' else 'active', identity))
            elif action == 'rotate-key':
                epoch = actor['key_epoch'] + 1
                self._new_key(db, identity, epoch)
                db.execute('UPDATE upi_participants SET key_epoch=? WHERE id=?', (epoch, identity))
            elif action == 'revoke-key':
                key = db.execute('SELECT * FROM upi_keys WHERE id=? AND participant_id=?', (data['key_id'], identity)).fetchone()
                require(key, 'NOT_FOUND', 'Key does not belong to this participant.', 404)
                require(key['epoch'] != actor['key_epoch'], 'CURRENT_KEY', 'Rotate before revoking the current signing key.', 409)
                db.execute('UPDATE upi_keys SET revoked=1 WHERE id=?', (key['id'],))
            else:
                raise DomainError('NOT_FOUND', 'Participant action not found.', 404)
            self.store.audit(db, tenant, env, 'upi.participant_' + action, identity, data)
            return self.get(db, 'upi_participants', tenant, env, identity)

    def _challenge(self, identity):
        return hmac.new(self.store.signing_key, ('upi-device:' + identity).encode(), hashlib.sha256).hexdigest()

    def device(self, tenant, env, data):
        fields(data, ['customer_id', 'device_reference', 'request_key'])
        self.app.environment(tenant, env, True)
        key, device = reference(data['request_key']), label(data['device_reference'], 'Synthetic device reference', 80)
        fingerprint = digest({'customer_id': data['customer_id'], 'device_reference': device})
        with self.store.transaction() as db:
            old = self.bank._existing(db, 'upi_devices', tenant, env, key, fingerprint)
            if old:
                return {**{k: v for k, v in old.items() if k != 'challenge_hash'}, 'enrolment_challenge': self._challenge(old['id'])}
            self.get(db, 'bank_customers', tenant, env, data['customer_id'])
            require(not db.execute('SELECT 1 FROM upi_devices WHERE tenant_id=? AND environment_id=? AND customer_id=? AND device_reference=?', (tenant, env, data['customer_id'], device)).fetchone(), 'DEVICE_EXISTS', 'Choose a new device reference after revocation.', 409)
            identity, timestamp = uid('device'), now()
            challenge = self._challenge(identity)
            db.execute('INSERT INTO upi_devices VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (identity, tenant, env, data['customer_id'], device, hashlib.sha256(challenge.encode()).hexdigest(), self.clock(tenant, env, db) + 300, 'pending', key, fingerprint, timestamp, timestamp))
            self.store.audit(db, tenant, env, 'upi.device_requested', identity, {'customer_id': data['customer_id']})
            value = self.get(db, 'upi_devices', tenant, env, identity)
            value.pop('challenge_hash')
            return {**value, 'enrolment_challenge': challenge}

    def _proof(self, device, proof):
        require(isinstance(proof, str) and re.fullmatch('[0-9a-f]{64}', proof) and hmac.compare_digest(hashlib.sha256(proof.encode()).hexdigest(), device['challenge_hash']), 'DEVICE_PROOF', 'The synthetic device proof is invalid.', 403)

    def device_action(self, tenant, env, identity, action, data):
        fields(data, ['device_proof', 'synthetic_verification'] if action == 'activate' else [])
        with self.store.transaction() as db:
            device = self.get(db, 'upi_devices', tenant, env, identity)
            if action == 'activate':
                self._proof(device, data['device_proof'])
                require(data['synthetic_verification'] is True, 'DEVICE_VERIFICATION', 'An explicit synthetic verification result is required.')
                require(device['state'] != 'revoked', 'DEVICE_REVOKED', 'A revoked device cannot be reactivated.', 409)
                require(device['state'] == 'active' or self.clock(tenant, env, db) < device['expires_at'], 'DEVICE_EXPIRED', 'Device enrolment has expired.', 409)
                state = 'active'
            elif action == 'revoke':
                state = 'revoked'
            else:
                raise DomainError('NOT_FOUND', 'Device action not found.', 404)
            db.execute('UPDATE upi_devices SET state=?,updated_at=? WHERE id=?', (state, now(), identity))
            self.store.audit(db, tenant, env, 'upi.device_' + state, identity, {})
            value = self.get(db, 'upi_devices', tenant, env, identity)
            value.pop('challenge_hash')
            return value

    def link(self, tenant, env, data):
        fields(data, ['device_id', 'account_id', 'psp_id'])
        with self.store.transaction() as db:
            device = self.get(db, 'upi_devices', tenant, env, data['device_id'])
            account = self.bank._get(db, 'bank_accounts', tenant, env, data['account_id'], 'account_id')
            actor = self.get(db, 'upi_participants', tenant, env, data['psp_id'])
            require(device['state'] == 'active' and account['status'] == 'active', 'ACCOUNT_UNAVAILABLE', 'Device and account must be active.', 409)
            require(device['customer_id'] == account['customer_id'], 'ACCOUNT_OWNERSHIP', 'Device and account must belong to the same synthetic customer.', 403)
            require(actor['role'] == 'payer_psp' and actor['status'] == 'active', 'INVALID_PARTICIPANT', 'Choose an active payer PSP.')
            old = db.execute('SELECT * FROM upi_links WHERE device_id=? AND account_id=? AND psp_id=?', (device['id'], account['account_id'], actor['id'])).fetchone()
            if old:
                return dict(old)
            identity = uid('link')
            db.execute('INSERT INTO upi_links VALUES(?,?,?,?,?,?,?)', (identity, tenant, env, device['id'], account['account_id'], actor['id'], now()))
            self.store.audit(db, tenant, env, 'upi.account_linked', identity, {'account_id': account['account_id'], 'device_id': device['id']})
            return self.get(db, 'upi_links', tenant, env, identity)

    def balance(self, tenant, env, data):
        fields(data, ['link_id', 'device_proof'])
        with self.store.reader() as db:
            link = self.get(db, 'upi_links', tenant, env, data['link_id'])
            device = self.get(db, 'upi_devices', tenant, env, link['device_id'])
            self._proof(device, data['device_proof'])
            require(device['state'] == 'active', 'DEVICE_INACTIVE', 'Device is not active.', 403)
        account = next((x for x in self.app.accounts(tenant, env) if x['id'] == link['account_id']), None)
        require(account, 'NOT_FOUND', 'Linked account not found.', 404)
        return {'account_id': account['id'], 'currency': 'INR', 'balance_paise': account['balance_paise'], 'source': 'authoritative_accounting'}

    def _topology(self, db, tenant, env, payer, payee, payer_psp=None):
        accounts = self.bank._accounts(db, tenant, env, payer, payee)
        topology = {}
        for role, bank in [('network', None), ('payer_psp', accounts[0]['bank_id']), ('remitter', accounts[0]['bank_id']), ('beneficiary', accounts[1]['bank_id']), ('payee_psp', accounts[1]['bank_id'])]:
            key = bank + ':' + role if bank else 'network'
            actor = db.execute('SELECT * FROM upi_participants WHERE tenant_id=? AND environment_id=? AND actor_key=?', (tenant, env, key)).fetchone()
            if role == 'payer_psp' and payer_psp:
                actor = self.get(db, 'upi_participants', tenant, env, payer_psp)
            require(actor and actor['role'] == role, 'TOPOLOGY_INCOMPLETE', 'Install a complete participant topology for these banks.', 409)
            topology[role] = actor['id']
        return topology

    def qr(self, tenant, env, data):
        fields(data, ['payee_vpa', 'amount_paise', 'reference'])
        account = self.bank.resolve(tenant, env, {'vpa': data['payee_vpa']})
        amount, key = money(data['amount_paise']), reference(data['reference'])
        with self.store.reader() as db:
            customer = db.execute('SELECT c.kind FROM bank_accounts a JOIN bank_customers c ON c.id=a.customer_id WHERE a.account_id=?', (account['account_id'],)).fetchone()
        require(customer['kind'] == 'merchant', 'MERCHANT_REQUIRED', 'This QR subset supports registered synthetic merchants.')
        uri = 'upi://pay?' + urlencode({'pa': account['vpa'], 'pn': account['name'], 'am': f'{amount // 100}.{amount % 100:02d}', 'cu': 'INR', 'tr': key})
        return {'uri': uri, 'contract': 'Bounded QR/intent subset; full current official QR conformance is unqualified.', 'decoded': self.parse_qr(tenant, env, {'uri': uri})}

    def parse_qr(self, tenant, env, data):
        fields(data, ['uri'])
        uri = data['uri']
        require(isinstance(uri, str) and len(uri) <= 2048 and not any(ord(c) < 32 for c in uri) and not re.search(r'%(?![0-9a-fA-F]{2})', uri), 'INVALID_QR', 'Use a bounded payment URI.')
        try:
            parsed = urlsplit(uri)
        except ValueError:
            raise DomainError('INVALID_QR', 'Invalid payment URI authority.')
        require(parsed.scheme == 'upi' and parsed.netloc == 'pay' and parsed.path in {'', '/'} and not parsed.fragment, 'INVALID_QR', 'Use the supported upi://pay URI subset.')
        try:
            pairs = parse_qsl(parsed.query, strict_parsing=True, keep_blank_values=True, max_num_fields=8, errors='strict')
        except (ValueError, UnicodeError):
            raise DomainError('INVALID_QR', 'Invalid payment URI encoding.')
        values = dict(pairs)
        require(len(pairs) == len(values) and {'pa', 'am', 'cu'} <= set(values) and set(values) <= {'pa', 'pn', 'am', 'cu', 'tr', 'tn'}, 'INVALID_QR', 'Duplicate or unsupported payment URI parameters.')
        require(values['cu'] == 'INR' and re.fullmatch(r'[0-9]{1,7}(?:\.[0-9]{1,2})?', values['am']), 'INVALID_QR', 'Use INR with at most two decimal places.')
        whole, _, fraction = values['am'].partition('.')
        amount = money(int(whole) * 100 + int(fraction.ljust(2, '0') or '0'))
        account = self.bank.resolve(tenant, env, {'vpa': values['pa']})
        for key in ['pn', 'tn', 'tr']:
            if key in values:
                label(values[key], key, 160)
        return {'payee_id': account['account_id'], 'payee_vpa': account['vpa'], 'resolved_name': account['name'], 'display_name': values.get('pn'), 'amount_paise': amount, 'currency': 'INR', 'reference': values.get('tr'), 'requires_payer_authorization': True}

    def submit(self, tenant, env, data, parent=None):
        fields(data, ['request_key', 'payer_id', 'payee_id', 'amount_paise'], ['mode', 'device_id', 'expires_in_seconds', 'financial_fault', 'delivery_fault', 'qr_uri'])
        self.app.environment(tenant, env, True)
        key, amount = reference(data['request_key']), money(data['amount_paise'])
        mode = 'refund' if parent else data.get('mode', 'pay')
        require(isinstance(mode, str) and mode in {'pay', 'collect', 'qr', 'refund'} and (mode != 'refund' or parent), 'INVALID_MODE', 'Choose pay, merchant collect or QR.')
        lifetime = integer(data.get('expires_in_seconds', 300), 'Authorization lifetime', 1, 3600)
        fault = data.get('financial_fault', 'none')
        require(isinstance(fault, str) and fault in FAULTS, 'INVALID_FAULT', 'Unsupported financial fault.')
        delivery = data.get('delivery_fault', {})
        require(isinstance(delivery, dict), 'INVALID_FAULT', 'Use one bounded delivery fault.')
        if delivery:
            fields(delivery, ['step', 'type'], ['seconds'])
            integer(delivery['step'], 'Fault step', -1, 7)
            require(isinstance(delivery['type'], str) and delivery['type'] in DELIVERY_FAULTS, 'INVALID_FAULT', 'Unsupported delivery fault.')
            integer(delivery.get('seconds', 1), 'Delivery delay', 1, 3600)
        if mode == 'qr':
            decoded = self.parse_qr(tenant, env, {'uri': data.get('qr_uri')})
            require(decoded['payee_id'] == data['payee_id'] and decoded['amount_paise'] == amount, 'QR_MISMATCH', 'The authorized destination and amount must match the decoded QR.')
        else:
            require('qr_uri' not in data, 'INVALID_UPI_REQUEST', 'QR URI is only supported in QR mode.')
        fingerprint = digest({**data, 'amount_paise': amount, 'mode': mode, 'parent_id': parent, 'profile_hash': PROFILE_HASH})
        with self.store.transaction() as db:
            old = self.bank._existing(db, 'upi_payments', tenant, env, key, fingerprint)
            if old:
                return self._public(old)
            payer, payee = data['payer_id'], data['payee_id']
            device_id, psp_id = data.get('device_id'), None
            if device_id:
                device = self.get(db, 'upi_devices', tenant, env, device_id)
                linked = db.execute('SELECT * FROM upi_links WHERE device_id=? AND account_id=? ORDER BY created_at,id LIMIT 1', (device_id, payer)).fetchone()
                require(device['state'] == 'active' and linked, 'ACCOUNT_NOT_LINKED', 'Link the active device to the payer account first.', 403)
                psp_id = linked['psp_id']
            topology = self._topology(db, tenant, env, payer, payee, psp_id)
            adapters = self.connections.snapshot(db, tenant, env, topology)
            if mode == 'collect':
                customer = db.execute('SELECT c.kind FROM bank_accounts a JOIN bank_customers c ON c.id=a.customer_id WHERE a.account_id=?', (payee,)).fetchone()
                require(customer['kind'] == 'merchant', 'MERCHANT_REQUIRED', 'Person-to-person collect is not supported by this profile.')
            if parent:
                original = self.get(db, 'upi_payments', tenant, env, parent)
                require(original['state'] == 'succeeded' and original['mode'] != 'refund', 'NOT_REFUNDABLE', 'Refunds require an original succeeded payment.', 409)
                require((payer, payee) == (original['payee_id'], original['payer_id']), 'INVALID_ACCOUNT', 'Return funds only to the original payer.')
            identity, timestamp = uid('upi'), now()
            db.execute('INSERT INTO upi_payments(id,tenant_id,environment_id,request_key,request_hash,profile_hash,mode,parent_id,payer_id,payee_id,amount_paise,topology_json,device_id,expires_at,state,financial_fault,delivery_fault_json,source_fingerprint,created_at,updated_at,adapter_bindings_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (identity, tenant, env, key, fingerprint, PROFILE_HASH, mode, parent, payer, payee, amount, canonical(topology), device_id, self.clock(tenant, env, db) + lifetime, 'awaiting_authorization', fault, canonical(delivery), self.app.source_fingerprint, timestamp, timestamp, canonical(adapters)))
            payment = self.get(db, 'upi_payments', tenant, env, identity)
            self._event(db, payment, None, 'intent.accepted', {'mode': mode, 'intent_hash': fingerprint})
            self.store.enqueue(db, tenant, env, 'upi_expiry', identity, {}, 'upi-expiry:' + identity, delay=min(lifetime, 30))
            if mode == 'collect':
                self._emit(db, payment, -1)
                self._enqueue(db, payment)
            return self._public(payment)

    def expire(self, tenant, env, identity):
        token = secrets.token_hex(16)
        with self.store.transaction() as db:
            payment = self.get(db, 'upi_payments', tenant, env, identity)
            if payment['evidence_json'] or payment['state'] != 'awaiting_authorization':
                return {'pending': False}
            if self.clock(tenant, env, db) < payment['expires_at']:
                return {'pending': True}
            if payment['lease_until'] and payment['lease_until'] > time.time():
                raise BankingBusy()
            db.execute("UPDATE upi_payments SET state='expired',reason='Authorization expired before financial dispatch.',lease_token=?,lease_until=?,updated_at=? WHERE id=?", (token, time.time() + 90, now(), identity))
            db.execute("UPDATE upi_deliveries SET state='expired',error='AUTHORIZATION_EXPIRED',updated_at=? WHERE message_id IN (SELECT id FROM upi_envelopes WHERE payment_id=?) AND state='ready'", (now(), identity))
            self._event(db, payment, None, 'authorization.expired', {'financial_dispatch': False})
        try:
            self._seal(tenant, env, identity, token)
        finally:
            with self.store.transaction() as db:
                db.execute('UPDATE upi_payments SET lease_token=NULL,lease_until=NULL WHERE id=? AND lease_token=? AND evidence_json IS NULL', (identity, token))
        return {'pending': False}

    def refund(self, tenant, env, identity, data):
        fields(data, ['request_key', 'amount_paise'], ['device_id', 'financial_fault', 'delivery_fault'])
        with self.store.reader() as db:
            parent = self.get(db, 'upi_payments', tenant, env, identity)
        return self.submit(tenant, env, {**data, 'payer_id': parent['payee_id'], 'payee_id': parent['payer_id']}, identity)

    def authorize(self, tenant, env, identity, data):
        fields(data, ['device_id', 'device_proof', 'approved'])
        require(type(data['approved']) is bool, 'AUTHORIZATION_REQUIRED', 'Choose an explicit synthetic approval or rejection.')
        with self.store.transaction() as db:
            payment = self.get(db, 'upi_payments', tenant, env, identity)
            device = self.get(db, 'upi_devices', tenant, env, data['device_id'])
            self._proof(device, data['device_proof'])
            decision_hash = digest({'device_id': device['id'], 'approved': data['approved'], 'intent_hash': payment['request_hash']})
            if payment['authorization_json']:
                require(json.loads(payment['authorization_json'])['decision_hash'] == decision_hash, 'AUTHORIZATION_CONFLICT', 'This intent already has a different authorization decision.', 409)
                return self._public(payment)
            require(payment['state'] == 'awaiting_authorization', 'INVALID_STATE', 'Intent is not awaiting authorization.', 409)
            require(not payment['lease_until'] or payment['lease_until'] < time.time(), 'PAYMENT_BUSY', 'A participant presentation is still being committed; retry authorization.', 409)
            timestamp = self.clock(tenant, env, db)
            if timestamp >= payment['expires_at']:
                db.execute("UPDATE upi_payments SET state='expired',reason='Authorization expired before any financial dispatch.',updated_at=? WHERE id=?", (now(), identity))
                db.execute("UPDATE upi_deliveries SET state='expired',error='AUTHORIZATION_EXPIRED',updated_at=? WHERE message_id IN (SELECT id FROM upi_envelopes WHERE payment_id=?) AND state='ready'", (now(), identity))
                self._event(db, payment, None, 'authorization.expired', {})
                self._enqueue(db, payment)
                return self._public(self.get(db, 'upi_payments', tenant, env, identity))
            require(device['state'] == 'active' and (not payment['device_id'] or device['id'] == payment['device_id']), 'DEVICE_INACTIVE', 'Use the intent’s active synthetic device.', 403)
            topology = json.loads(payment['topology_json'])
            linked = db.execute('SELECT 1 FROM upi_links WHERE device_id=? AND account_id=? AND psp_id=?', (device['id'], payment['payer_id'], topology['payer_psp'])).fetchone()
            require(linked, 'ACCOUNT_NOT_LINKED', 'Device is not linked to this payer account and PSP.', 403)
            if payment['mode'] == 'collect':
                require(db.execute('SELECT 1 FROM upi_inbox i JOIN upi_envelopes e ON e.id=i.message_id WHERE e.payment_id=? AND e.step=0', (identity,)).fetchone(), 'COLLECT_NOT_PRESENTED', 'The payer PSP must receive the collect before authorization.', 409)
            authorization = {'decision_hash': decision_hash, 'intent_hash': payment['request_hash'], 'device_id': device['id'], 'approved': data['approved'], 'at': timestamp, 'expires_at': payment['expires_at'], 'profile_hash': PROFILE_HASH,
                             'adapter_bindings_hash': digest(json.loads(payment['adapter_bindings_json']))}
            actor = self.get(db, 'upi_participants', tenant, env, topology['payer_psp'])
            authorization['key_id'] = actor['id'] + '_k' + str(actor['key_epoch'])
            authorization['signature'] = self._private_key(actor['id'], actor['key_epoch']).sign(canonical(authorization).encode()).hex()
            if not data['approved']:
                db.execute("UPDATE upi_payments SET authorization_json=?,device_id=?,state='declined',reason='Payer rejected synthetic authorization.',updated_at=? WHERE id=?", (canonical(authorization), device['id'], now(), identity))
                self._event(db, payment, None, 'authorization.declined', {'decision_hash': decision_hash})
                self._enqueue(db, payment)
                return self._public(self.get(db, 'upi_payments', tenant, env, identity))
            parent = self.get(db, 'upi_payments', tenant, env, payment['parent_id']) if payment['parent_id'] else None
            operation = self.bank._submit(db, tenant, env, {'payer_id': payment['payer_id'], 'payee_id': payment['payee_id'], 'amount_paise': payment['amount_paise'], 'fault': payment['financial_fault'], 'request_key': 'upi:' + identity},
                                          'refund' if parent else 'transfer', parent['operation_id'] if parent else None, executor='upi')
            db.execute("UPDATE upi_payments SET authorization_json=?,device_id=?,operation_id=?,state='queued',updated_at=? WHERE id=?", (canonical(authorization), device['id'], operation['id'], now(), identity))
            payment = self.get(db, 'upi_payments', tenant, env, identity)
            self._event(db, payment, None, 'authorization.approved', {'decision_hash': decision_hash, 'operation_id': operation['id']})
            self._emit(db, payment, 1)
            self._enqueue(db, payment)
            return self._public(payment)

    def _enqueue(self, db, payment):
        # A leased job may already be returning its last result. Preserve a new
        # wakeup in that handoff window; only an unclaimed ready job can absorb it.
        if db.execute("SELECT 1 FROM outbox WHERE kind='upi_payment' AND aggregate_id=? AND state='ready'", (payment['id'],)).fetchone():
            return
        self.store.enqueue(db, payment['tenant_id'], payment['environment_id'], 'upi_payment', payment['id'], {}, 'upi-delivery:' + uid('attempt'))

    def _event(self, db, payment, message, event, details):
        self.store.audit(db, payment['tenant_id'], payment['environment_id'], 'upi.' + event, payment['id'], {'message_id': message, **details})
        sequence = db.execute('SELECT MAX(sequence) FROM audit_events').fetchone()[0]
        db.execute('INSERT INTO upi_events VALUES(?,?,?,?,?,?,?,?,?)', ('upievent_' + str(sequence).zfill(20), payment['tenant_id'], payment['environment_id'], payment['id'], message, event, canonical({**details, 'audit_sequence': sequence}), self.clock(payment['tenant_id'], payment['environment_id'], db), now()))

    def _emit(self, db, payment, step, outcome=None, generation=0):
        old = db.execute('SELECT * FROM upi_envelopes WHERE payment_id=? AND step=? AND generation=?', (payment['id'], step, generation)).fetchone()
        if old:
            return dict(old)
        topology = json.loads(payment['topology_json'])
        kind, sender_role, recipient_role = ROUTES[step]
        sender = self.get(db, 'upi_participants', payment['tenant_id'], payment['environment_id'], topology[sender_role])
        epoch, timestamp, identity = sender['key_epoch'], self.clock(payment['tenant_id'], payment['environment_id'], db), uid('upimsg')
        key_id = sender['id'] + '_k' + str(epoch)
        document = {'format': PROFILE['message_format'], 'profile_hash': payment['profile_hash'], 'tenant_id': payment['tenant_id'], 'environment_id': payment['environment_id'], 'payment_id': payment['id'], 'message_id': identity,
                    'step': step, 'generation': generation, 'sender': sender['id'], 'recipient': topology[recipient_role], 'kind': kind, 'key_id': key_id, 'issued_at': timestamp, 'expires_at': timestamp + PROFILE['message_lifetime_seconds'],
                    'payload': {'intent_hash': payment['request_hash'], 'amount_paise': payment['amount_paise'], 'currency': 'INR', 'operation_id': payment['operation_id'], 'outcome': outcome}}
        signature = self._private_key(sender['id'], epoch).sign(canonical(document).encode()).hex()
        db.execute('INSERT INTO upi_envelopes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (identity, payment['tenant_id'], payment['environment_id'], payment['id'], step, generation, sender['id'], topology[recipient_role], kind, canonical(document), digest(document), signature, key_id, document['expires_at'], now()))
        db.execute("INSERT INTO upi_deliveries(message_id,state,available_at,updated_at) VALUES(?,'ready',?,?)", (identity, timestamp, now()))
        self._event(db, payment, identity, 'message.queued', {'step': step, 'kind': kind, 'document_hash': digest(document)})
        return dict(db.execute('SELECT * FROM upi_envelopes WHERE id=?', (identity,)).fetchone())

    def _verify(self, db, payment, envelope, signature, allow_expired_receipt=True):
        require(isinstance(envelope, dict) and isinstance(signature, str) and re.fullmatch('[0-9a-f]{128}', signature), 'INVALID_SIGNATURE', 'Use a signed participant envelope.')
        message = self.get(db, 'upi_envelopes', payment['tenant_id'], payment['environment_id'], envelope.get('message_id'))
        require(message['payment_id'] == payment['id'] and envelope == json.loads(message['document_json']) and signature == message['signature'], 'MESSAGE_CONFLICT', 'Envelope content or authentication differs from its immutable transport identity.', 409)
        require(digest(envelope) == message['document_hash'] and payment['profile_hash'] == PROFILE_HASH and envelope['profile_hash'] == PROFILE_HASH, 'PROFILE_UNSUPPORTED', 'Message does not match the supported lab profile.', 409)
        old = db.execute('SELECT * FROM upi_inbox WHERE message_id=?', (message['id'],)).fetchone()
        if old and allow_expired_receipt:
            require(old['document_hash'] == message['document_hash'], 'MESSAGE_CONFLICT', 'Inbox identity differs from this message.', 409)
            return message, dict(old)
        key = db.execute('SELECT * FROM upi_keys WHERE id=? AND participant_id=?', (envelope['key_id'], envelope['sender'])).fetchone()
        require(key and not key['revoked'], 'KEY_REVOKED', 'Participant verification key is unavailable or revoked.', 403)
        try:
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(key['public_key'])).verify(bytes.fromhex(signature), canonical(envelope).encode())
        except (InvalidSignature, ValueError):
            raise DomainError('SIGNATURE_INVALID', 'Participant signature verification failed.', 403)
        timestamp = self.clock(payment['tenant_id'], payment['environment_id'], db)
        require(envelope['issued_at'] <= timestamp < envelope['expires_at'], 'MESSAGE_EXPIRED', 'Participant message is outside its validity interval.', 409)
        topology = json.loads(payment['topology_json'])
        kind, sender, recipient = ROUTES[message['step']]
        require((envelope['kind'], envelope['sender'], envelope['recipient']) == (kind, topology[sender], topology[recipient]), 'MESSAGE_ROUTE', 'Message does not match its participant route.', 403)
        return message, None

    def receive(self, tenant, env, data):
        fields(data, ['envelope', 'signature'])
        require(isinstance(data['envelope'], dict), 'INVALID_UPI_REQUEST', 'Envelope must be an object.')
        with self.store.reader() as db:
            payment = self.get(db, 'upi_payments', tenant, env, data['envelope'].get('payment_id'))
            message, old = self._verify(db, payment, data['envelope'], data['signature'])
            delivery = dict(db.execute('SELECT * FROM upi_deliveries WHERE message_id=?', (message['id'],)).fetchone())
        return {'message_id': message['id'], 'replayed': bool(old), 'receipt': json.loads(old['receipt_json']) if old else None, 'delivery': delivery}

    def reconcile(self, tenant, env, identity, data):
        fields(data, [])
        with self.store.transaction() as db:
            payment = self.get(db, 'upi_payments', tenant, env, identity)
            if payment['evidence_json']:
                return self._public(payment)
            if payment['state'] == 'expired' and not payment['operation_id']:
                self._enqueue(db, payment)
                return self._public(payment)
            require(not payment['lease_until'] or payment['lease_until'] < time.time(), 'PAYMENT_BUSY', 'A participant delivery is still in progress.', 409)
            pending = db.execute("SELECT e.*,d.state delivery_state FROM upi_envelopes e JOIN upi_deliveries d ON d.message_id=e.id WHERE e.payment_id=? AND d.state IN ('expired','rejected') AND NOT EXISTS(SELECT 1 FROM upi_envelopes newer WHERE newer.payment_id=e.payment_id AND newer.step=e.step AND newer.generation>e.generation)", (identity,)).fetchall()
            for old in pending:
                self._emit(db, payment, old['step'], json.loads(old['document_json'])['payload']['outcome'], old['generation'] + 1)
            self._event(db, payment, None, 'status.reconciliation_requested', {'fresh_transport_attempts': len(pending), 'operation_id': payment['operation_id']})
            self._enqueue(db, payment)
            return self._public(payment)

    def _handle(self, tenant, env, payment, message, token):
        document, step = json.loads(message['document_json']), message['step']
        outcome, next_steps = document['payload']['outcome'], []
        if step == 2:
            with self.store.transaction() as db:
                debit = db.execute("SELECT state FROM bank_legs WHERE operation_id=? AND kind='debit'", (payment['operation_id'],)).fetchone()
                device = self.get(db, 'upi_devices', tenant, env, payment['device_id'])
                if debit and debit['state'] == 'pending' and (device['state'] != 'active' or self.clock(tenant, env, db) >= payment['expires_at']):
                    db.execute("UPDATE bank_legs SET state='declined',reason='Synthetic approval expired or device revoked before dispatch.',updated_at=? WHERE operation_id=? AND kind='debit'", (now(), payment['operation_id']))
                    self._event(db, payment, message['id'], 'authorization.invalid_before_debit', {'device_state': device['state']})
        if step in {2, 4}:
            operation = self.bank.process(tenant, env, payment['operation_id'], 'debit' if step == 2 else 'credit', executor='upi',
                                          effect=lambda operation, kind, ledger: self.connections.financial_effect(tenant, env, payment, operation, kind, ledger))
            if operation['state'] == 'unknown':
                with self.store.transaction() as db:
                    require(db.execute('SELECT 1 FROM upi_payments WHERE id=? AND lease_token=?', (payment['id'], token)).fetchone(), 'PAYMENT_BUSY', 'Participant lease changed.', 409)
                    db.execute("UPDATE upi_payments SET state='unknown',reason='Bank outcome is unconfirmed; original financial identity retained.',updated_at=? WHERE id=?", (now(), payment['id']))
                    db.execute('UPDATE upi_deliveries SET available_at=?,error=?,updated_at=? WHERE message_id=?', (self.clock(tenant, env, db) + 1, 'BANK_OUTCOME_UNKNOWN', now(), message['id']))
                    self._event(db, payment, message['id'], 'bank.outcome_unknown', {'operation_id': payment['operation_id'], 'phase': 'debit' if step == 2 else 'credit'})
                return False
            outcome = ('declined' if operation['state'] == 'declined' else 'confirmed') if step == 2 else operation['state']
            next_steps = [3] if step == 2 else [5]
        with self.store.transaction() as db:
            payment = self.get(db, 'upi_payments', tenant, env, payment['id'])
            require(payment['lease_token'] == token, 'PAYMENT_BUSY', 'Participant lease changed.', 409)
            _, old = self._verify(db, payment, document, message['signature'])
            if old:
                return True
            if step == -1:
                next_steps = [0]
            elif step == 1:
                operation = self.get(db, 'bank_operations', tenant, env, payment['operation_id'])
                outcome = operation['state'] if operation['state'] in TERMINAL else None
                next_steps = [6] if operation['state'] in TERMINAL else [2]
            elif step == 3:
                leg = db.execute("SELECT state FROM bank_legs WHERE operation_id=? AND kind='debit'", (payment['operation_id'],)).fetchone()
                require(leg and ((outcome == 'confirmed' and leg['state'] == 'confirmed') or (outcome == 'declined' and leg['state'] == 'declined')), 'FINANCIAL_INVARIANT', 'Remitter response differs from its recorded financial result.', 409)
                next_steps = [6] if outcome == 'declined' else [4]
            elif step in {5, 6, 7}:
                operation = self.get(db, 'bank_operations', tenant, env, payment['operation_id'])
                require(operation['state'] in TERMINAL and operation['state'] == outcome, 'FINANCIAL_INVARIANT', 'Participant result differs from its authoritative financial outcome.', 409)
                if step == 5:
                    next_steps = [6, 7]
                if step == 6:
                    db.execute('UPDATE upi_payments SET state=?,reason=?,updated_at=? WHERE id=?', (outcome, operation['reason'], now(), payment['id']))
            state = {1: 'routing', 2: 'debit_pending', 3: 'debit_pending', 4: 'credit_pending', 5: 'credit_pending'}.get(step)
            if state:
                db.execute('UPDATE upi_payments SET state=?,reason=NULL,updated_at=? WHERE id=?', (state, now(), payment['id']))
            receipt = {'message_id': message['id'], 'document_hash': message['document_hash'], 'participant_id': message['recipient'], 'kind': message['kind'], 'outcome': outcome or 'accepted', 'virtual_time': self.clock(tenant, env, db)}
            db.execute('INSERT INTO upi_inbox VALUES(?,?,?,?)', (message['id'], message['document_hash'], canonical(receipt), now()))
            view = db.execute('SELECT 1 FROM upi_actor_views WHERE payment_id=? AND participant_id=?', (payment['id'], message['recipient'])).fetchone()
            if view:
                db.execute('UPDATE upi_actor_views SET state=?,last_message_id=?,updated_at=? WHERE payment_id=? AND participant_id=?', (receipt['outcome'], message['id'], now(), payment['id'], message['recipient']))
            else:
                db.execute('INSERT INTO upi_actor_views VALUES(?,?,?,?,?)', (payment['id'], message['recipient'], receipt['outcome'], message['id'], now()))
            self._event(db, payment, message['id'], 'message.accepted', receipt)
            for next_step in next_steps:
                self._emit(db, payment, next_step, outcome)
            return True

    def process(self, tenant, env, identity):
        self.app.environment(tenant, env, True)
        token = secrets.token_hex(16)
        with self.store.transaction() as db:
            payment = self.get(db, 'upi_payments', tenant, env, identity)
            if payment['evidence_json']:
                return {'pending': False, 'state': payment['state']}
            require(payment['profile_hash'] == PROFILE_HASH, 'PROFILE_UNSUPPORTED', 'This intent needs its original participant engine.', 409)
            changed = db.execute('UPDATE upi_payments SET lease_token=?,lease_until=? WHERE id=? AND (lease_until IS NULL OR lease_until<?)', (token, time.time() + 90, identity, time.time())).rowcount
            if not changed:
                raise BankingBusy()
        try:
            with self.store.transaction() as db:
                timestamp = self.clock(tenant, env, db)
                message = db.execute("SELECT e.*,d.attempts,d.fault_applied FROM upi_envelopes e JOIN upi_deliveries d ON d.message_id=e.id WHERE e.payment_id=? AND d.state='ready' AND d.available_at<=? ORDER BY e.step,e.generation LIMIT 1", (identity, timestamp)).fetchone()
                if not message:
                    pending = bool(db.execute("SELECT 1 FROM upi_envelopes e JOIN upi_deliveries d ON d.message_id=e.id WHERE e.payment_id=? AND d.state='ready'", (identity,)).fetchone())
                else:
                    message = dict(message)
                    db.execute('UPDATE upi_deliveries SET attempts=attempts+1,updated_at=? WHERE message_id=?', (now(), message['id']))
            if not message:
                self._seal(tenant, env, identity, token)
                return {'pending': pending, 'state': payment['state']}
            try:
                with self.store.reader() as db:
                    _, old = self._verify(db, payment, json.loads(message['document_json']), message['signature'])
                    actors = [self.get(db, 'upi_participants', tenant, env, message[k]) for k in ['sender', 'recipient']]
                if any(x['status'] != 'active' for x in actors):
                    return {'pending': True, 'state': payment['state'], 'waiting': 'participant_paused'}
                fault = json.loads(payment['delivery_fault_json'])
                apply_fault = bool(fault and fault['step'] == message['step'] and not message['fault_applied'])
                if apply_fault:
                    with self.store.transaction() as db:
                        db.execute('UPDATE upi_deliveries SET fault_applied=1,updated_at=? WHERE message_id=?', (now(), message['id']))
                    if fault['type'] in {'drop_once', 'delay_once', 'corrupt_once'}:
                        with self.store.transaction() as db:
                            db.execute('UPDATE upi_deliveries SET available_at=?,error=?,updated_at=? WHERE message_id=?', (self.clock(tenant, env, db) + fault.get('seconds', 1), fault['type'], now(), message['id']))
                            self._event(db, payment, message['id'], 'transport.' + fault['type'], {'delivered': False})
                        if fault['type'] == 'corrupt_once':
                            try:
                                self.receive(tenant, env, {'envelope': json.loads(message['document_json']), 'signature': '0' * 128})
                            except DomainError as error:
                                require(error.code == 'MESSAGE_CONFLICT', 'FINANCIAL_INVARIANT', 'Corrupt transport was not rejected.', 409)
                        return {'pending': True, 'state': payment['state']}
                if not old and not self.connections.deliver(tenant, env, payment, message, token):
                    return {'pending': True, 'state': 'unknown'}
                with self.store.transaction() as db:
                    if apply_fault and fault['type'] == 'lose_ack_once':
                        db.execute('UPDATE upi_deliveries SET available_at=?,error=?,updated_at=? WHERE message_id=?', (self.clock(tenant, env, db) + 1, 'ACK_LOST_AFTER_INBOX_COMMIT', now(), message['id']))
                        self._event(db, payment, message['id'], 'transport.ack_lost', {'receipt_committed': True})
                    else:
                        db.execute("UPDATE upi_deliveries SET state='delivered',error=NULL,updated_at=? WHERE message_id=?", (now(), message['id']))
                        if old:
                            self._event(db, payment, message['id'], 'transport.receipt_replayed', {'original_receipt': True})
                if apply_fault and fault['type'] == 'duplicate_once':
                    replay = self.receive(tenant, env, {'envelope': json.loads(message['document_json']), 'signature': message['signature']})
                    require(replay['replayed'], 'FINANCIAL_INVARIANT', 'Duplicate did not reuse its original participant receipt.', 409)
                    with self.store.transaction() as db:
                        self._event(db, payment, message['id'], 'transport.duplicate_acknowledged', {'original_receipt': True})
            except DomainError as error:
                if error.code not in {'MESSAGE_EXPIRED', 'KEY_REVOKED', 'SIGNATURE_INVALID', 'MESSAGE_CONFLICT'}:
                    raise
                with self.store.transaction() as db:
                    db.execute('UPDATE upi_deliveries SET state=?,error=?,updated_at=? WHERE message_id=?', ('expired' if error.code == 'MESSAGE_EXPIRED' else 'rejected', error.code, now(), message['id']))
                    if not payment['operation_id']:
                        target = 'expired' if self.clock(tenant, env, db) >= payment['expires_at'] else 'awaiting_authorization'
                    else:
                        target = 'unknown'
                    db.execute("UPDATE upi_payments SET state=?,reason=?,updated_at=? WHERE id=? AND state NOT IN ('succeeded','declined','reversed')", (target, 'Participant delivery needs status recovery: ' + error.code, now(), identity))
                    self._event(db, payment, message['id'], 'message.rejected', {'code': error.code})
            self._seal(tenant, env, identity, token)
            with self.store.reader() as db:
                pending = bool(db.execute("SELECT 1 FROM upi_envelopes e JOIN upi_deliveries d ON d.message_id=e.id WHERE e.payment_id=? AND d.state='ready'", (identity,)).fetchone())
                current = self.get(db, 'upi_payments', tenant, env, identity)
            return {'pending': pending, 'state': current['state']}
        finally:
            with self.store.transaction() as db:
                db.execute('UPDATE upi_payments SET lease_token=NULL,lease_until=NULL WHERE id=? AND lease_token=? AND evidence_json IS NULL', (identity, token))

    def _seal(self, tenant, env, identity, token):
        with self.store.reader() as db:
            payment = self.get(db, 'upi_payments', tenant, env, identity)
            if payment['evidence_json'] or payment['state'] not in FINAL:
                return
            unfinished = db.execute("SELECT 1 FROM upi_envelopes e JOIN upi_deliveries d ON d.message_id=e.id WHERE e.payment_id=? AND d.state!='delivered' AND NOT EXISTS(SELECT 1 FROM upi_envelopes newer JOIN upi_deliveries nd ON nd.message_id=newer.id WHERE newer.payment_id=e.payment_id AND newer.step=e.step AND newer.generation>e.generation AND nd.state='delivered')", (identity,)).fetchone()
            if unfinished and not (payment['state'] == 'expired' and not payment['operation_id']):
                return
        value = self.read(tenant, env, identity)
        observations, checks = [], []
        operation = value['operation']
        if operation:
            ledger = self.app.environment(tenant, env)['ledger_name']
            for leg in operation['legs']:
                observed = self.app.accounting.transactions(ledger, leg['id'])
                expected = [{'source': leg['source'], 'destination': leg['destination'], 'amount': payment['amount_paise'], 'asset': 'INR/2'}]
                valid = len(observed) == (1 if leg['state'] == 'confirmed' else 0) and all(x['postings'] == expected for x in observed)
                checks.append({'name': 'Independent journal observation: ' + leg['kind'], 'passed': valid, 'expected_count': 1 if leg['state'] == 'confirmed' else 0, 'observed_count': len(observed)})
                observations.append({'leg_id': leg['id'], 'kind': leg['kind'], 'transactions': observed})
            confirmed = {x['kind'] for x in operation['legs'] if x['state'] == 'confirmed'}
            expected_legs = {'succeeded': {'debit', 'credit'}, 'reversed': {'debit', 'reversal'}, 'declined': set()}.get(payment['state'], set())
            checks.append({'name': 'Final financial legs match authorized outcome', 'passed': confirmed == expected_legs, 'expected': sorted(expected_legs), 'observed': sorted(confirmed)})
        else:
            checks.append({'name': 'Unapproved intent has no financial operation', 'passed': payment['state'] in {'declined', 'expired', 'cancelled'}})
        bank_transport = [json.loads(x['payload_json']) for x in operation['messages'] if x['kind'].startswith('bank.transport:')] if operation else []
        if bank_transport:
            from .lab_bank_client import verify_trace
            checks.append({'name': 'Retained synthetic bank request and response signatures verify', 'passed': all(verify_trace(x) for x in bank_transport)})
        require(all(x['passed'] for x in checks), 'FINANCIAL_INVARIANT', 'Independent participant evidence found a financial discrepancy.', 409)
        manifest = {'format': 'avenra-upi-evidence/1', 'payment': self._public(payment), 'profile': PROFILE, 'profile_hash': PROFILE_HASH, 'application_fingerprint': self.app.source_fingerprint,
                    'official_conformance': False, 'checks': checks, 'journal_observations': observations, 'bank_transport_receipts': bank_transport, 'messages': value['messages'], 'actor_views': value['actor_views'], 'events': value['events'], 'sealed_at': now()}
        manifest_hash = digest(manifest)
        evidence = {'manifest_json': canonical(manifest), 'manifest_hash': manifest_hash, 'signature': hmac.new(self.store.signing_key, manifest_hash.encode(), hashlib.sha256).hexdigest()}
        with self.store.transaction() as db:
            db.execute('UPDATE upi_payments SET evidence_json=?,lease_token=NULL,lease_until=NULL WHERE id=? AND lease_token=? AND evidence_json IS NULL', (canonical(evidence), identity, token))

    @staticmethod
    def _public(payment):
        return {k: v for k, v in payment.items() if k not in {'lease_token', 'lease_until', 'evidence_json'}}

    def read(self, tenant, env, identity):
        self.app.environment(tenant, env)
        with self.store.reader() as db:
            record = self.get(db, 'upi_payments', tenant, env, identity)
            value = self._public(record)
            value['messages'] = [dict(x) for x in db.execute('SELECT e.*,d.state delivery_state,d.attempts,d.fault_applied,d.error delivery_error,i.receipt_json FROM upi_envelopes e JOIN upi_deliveries d ON d.message_id=e.id LEFT JOIN upi_inbox i ON i.message_id=e.id WHERE e.payment_id=? ORDER BY e.step,e.generation', (identity,))]
            value['actor_views'] = [dict(x) for x in db.execute('SELECT v.*,p.role,p.name FROM upi_actor_views v JOIN upi_participants p ON p.id=v.participant_id WHERE v.payment_id=? ORDER BY p.role', (identity,))]
            value['events'] = [dict(x) for x in db.execute('SELECT * FROM upi_events WHERE payment_id=? ORDER BY created_at,id', (identity,))]
            value['effective_state'] = 'expired' if record['state'] == 'awaiting_authorization' and record['expires_at'] <= self.clock(tenant, env, db) else record['state']
            value['evidence'] = self.app.verify_evidence(json.loads(record['evidence_json'])) if record['evidence_json'] else None
        value['operation'] = self.bank.operation(tenant, env, record['operation_id']) if record['operation_id'] else None
        return value

    def overview(self, tenant, env):
        self.app.environment(tenant, env)
        with self.store.reader() as db:
            participants = [dict(x) for x in db.execute('SELECT * FROM upi_participants WHERE tenant_id=? AND environment_id=? ORDER BY actor_key', (tenant, env))]
            keys = [dict(x) for x in db.execute('SELECT k.* FROM upi_keys k JOIN upi_participants p ON p.id=k.participant_id WHERE p.tenant_id=? AND p.environment_id=? ORDER BY k.participant_id,k.epoch', (tenant, env))]
            devices = [{k: v for k, v in dict(x).items() if k != 'challenge_hash'} for x in db.execute('SELECT * FROM upi_devices WHERE tenant_id=? AND environment_id=? ORDER BY created_at DESC LIMIT 100', (tenant, env))]
            links = [dict(x) for x in db.execute('SELECT * FROM upi_links WHERE tenant_id=? AND environment_id=? ORDER BY created_at DESC LIMIT 100', (tenant, env))]
            payments = [self._public(dict(x)) for x in db.execute('SELECT * FROM upi_payments WHERE tenant_id=? AND environment_id=? ORDER BY created_at DESC,id DESC LIMIT 100', (tenant, env))]
        return {'profile': PROFILE, 'profile_hash': PROFILE_HASH, 'official_conformance': False, 'participants': participants, 'keys': keys, 'devices': devices, 'links': links, 'payments': payments, 'routes': [{'step': k, 'kind': v[0], 'sender': v[1], 'recipient': v[2]} for k, v in ROUTES.items()]}
