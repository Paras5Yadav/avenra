"""Persistent recurring agreements executing through the participant payment engine."""
import json
import math
import time

from avenra.errors import DomainError, require
from avenra.simulation import integer
from avenra.upi import fields
from avenra.util import canonical, digest, money, now, reference, uid
from .profile import PROFILE, PROFILE_HASH


class Recurring:
    def notification(self, db, tenant, env, resource, kind, details):
        old = db.execute('SELECT * FROM fidelity_notifications WHERE resource_id=? AND kind=?', (resource, kind)).fetchone()
        if old: return dict(old)
        document = {'resource_id': resource, 'kind': kind, 'tenant_id': tenant, 'environment_id': env,
                    'profile_hash': PROFILE_HASH, 'business_time': self.time(tenant, env, db), 'details': details}
        hashed, signature = self.sign(document)
        identity = uid('notice')
        db.execute('INSERT INTO fidelity_notifications VALUES(?,?,?,?,?,?,?,?,?)', (identity, tenant, env, resource, kind, canonical(document), hashed, signature, now()))
        return dict(db.execute('SELECT * FROM fidelity_notifications WHERE id=?', (identity,)).fetchone())

    def mandate(self, tenant, env, data):
        fields(data, ['payer_id', 'payee_id', 'device_id', 'maximum_paise', 'interval_seconds', 'starts_at', 'occurrences', 'request_key'])
        amount, key = money(data['maximum_paise']), reference(data['request_key'])
        interval = integer(data['interval_seconds'], 'Interval', PROFILE['rules']['minimum_mandate_interval_seconds'], 31_536_000)
        count = integer(data['occurrences'], 'Occurrences', 1, PROFILE['rules']['maximum_occurrences'])
        require(type(data['starts_at']) in {int, float} and math.isfinite(data['starts_at']), 'INVALID_TIME', 'Use a finite synthetic business timestamp.')
        fingerprint = digest({**data, 'profile_hash': PROFILE_HASH})
        with self.store.transaction() as db:
            self.require_enabled(tenant, env, db)
            old = self.bank._existing(db, 'fidelity_mandates', tenant, env, key, fingerprint)
            if old: return old
            payer, payee = self.bank._accounts(db, tenant, env, data['payer_id'], data['payee_id'])
            device = self.get(db, 'upi_devices', tenant, env, data['device_id'])
            require(device['customer_id'] == payer['customer_id'] and device['state'] == 'active', 'ACCOUNT_OWNERSHIP', 'Use the payer’s active linked device.', 403)
            merchant = self.get(db, 'bank_customers', tenant, env, payee['customer_id'])
            require(merchant['kind'] == 'merchant', 'MERCHANT_REQUIRED', 'This recurring profile supports registered synthetic merchants.')
            require(self.time(tenant, env, db) <= data['starts_at'] <= self.time(tenant, env, db) + 31_536_000, 'INVALID_TIME', 'Start must be within the next synthetic year.')
            require(amount <= PROFILE['rules']['per_payment_paise'], 'PAYMENT_LIMIT', 'Mandate maximum exceeds the synthetic payment limit.', 409)
            identity = uid('agreement')
            db.execute('INSERT INTO fidelity_mandates VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)', (identity, tenant, env, data['payer_id'], data['payee_id'], data['device_id'], amount, interval, data['starts_at'], data['starts_at'] + interval * count, count, 'pending', None, key, fingerprint, now()))
            self.event(db, tenant, env, identity, 'mandate.created', 'merchant', {'request_hash': fingerprint})
            return self.get(db, 'fidelity_mandates', tenant, env, identity)

    def mandate_action(self, tenant, env, identity, action, data):
        fields(data, ['device_proof', 'synthetic_pin'])
        require(action in {'approve', 'pause', 'resume', 'revoke'}, 'INVALID_ACTION', 'Choose a supported mandate action.')
        error = None
        with self.store.transaction() as db:
            self.require_enabled(tenant, env, db)
            mandate = self.get(db, 'fidelity_mandates', tenant, env, identity)
            self._device(db, tenant, env, mandate['device_id'], mandate['payer_id'], data['device_proof'])
            credential, error = self._check_pin(db, tenant, env, mandate['payer_id'], mandate['device_id'], data['synthetic_pin'])
            if not error:
                target = {'approve': 'active', 'pause': 'paused', 'resume': 'active', 'revoke': 'revoked'}[action]
                if mandate['state'] != target:
                    require(mandate['state'] in {'pending'} if action == 'approve' else mandate['state'] in {'active'} if action == 'pause' else mandate['state'] in {'paused'} if action == 'resume' else mandate['state'] in {'pending', 'active', 'paused'}, 'MANDATE_STATE', 'Mandate transition is not permitted.', 409)
                    if action in {'approve', 'resume'}:
                        require(self.time(tenant, env, db) < mandate['ends_at'], 'MANDATE_EXPIRED', 'Agreement has expired.', 409)
                    authorization = json.loads(mandate['authorization_json']) if mandate['authorization_json'] else {'mandate_hash': mandate['request_hash'], 'credential_epoch': credential['epoch'], 'device_id': mandate['device_id'], 'approved_at': self.time(tenant, env, db), 'profile_hash': PROFILE_HASH}
                    hashed, signature = self.sign(authorization)
                    db.execute('UPDATE fidelity_mandates SET state=?,authorization_json=?,action_version=action_version+1 WHERE id=?', (target, canonical(authorization), identity))
                    self.notification(db, tenant, env, identity, 'mandate.' + action + ':' + str(mandate['action_version'] + 1), {'state': target, 'authorization_hash': hashed, 'signature': signature})
                    self.event(db, tenant, env, identity, 'mandate.' + action, 'synthetic-payer', {'state': target})
            result = self.get(db, 'fidelity_mandates', tenant, env, identity)
        require(not error, error or 'CREDENTIAL', 'Synthetic credential verification was refused.', 403)
        return result

    def schedule(self, tenant, env, identity, data):
        fields(data, ['occurrence', 'amount_paise', 'request_key'])
        occurrence = integer(data['occurrence'], 'Occurrence', 0, PROFILE['rules']['maximum_occurrences'] - 1)
        amount, key = money(data['amount_paise']), reference(data['request_key'])
        fingerprint = digest({'mandate': identity, **data})
        with self.store.transaction() as db:
            self.require_enabled(tenant, env, db)
            mandate = self.get(db, 'fidelity_mandates', tenant, env, identity)
            old = self.bank._existing(db, 'fidelity_occurrences', tenant, env, key, fingerprint)
            if old: return old
            same = db.execute('SELECT * FROM fidelity_occurrences WHERE mandate_id=? AND occurrence=?', (identity, occurrence)).fetchone()
            if same:
                require(same['amount_paise'] == amount, 'OCCURRENCE_CONFLICT', 'Occurrence already has a different amount.', 409)
                return dict(same)
            require(mandate['state'] == 'active', 'MANDATE_INACTIVE', 'Approve the active agreement first.', 409)
            require(occurrence < mandate['occurrences'] and amount <= mandate['maximum_paise'], 'MANDATE_LIMIT', 'Occurrence is outside the approved agreement.', 409)
            due = mandate['starts_at'] + occurrence * mandate['interval_seconds']
            require(self.time(tenant, env, db) <= due, 'OCCURRENCE_LATE', 'Schedule before the due time; missed runs stay explicit.', 409)
            execution = uid('occurrence')
            db.execute('INSERT INTO fidelity_occurrences VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0)', (execution, tenant, env, identity, occurrence, amount, due, None, 'scheduled', None, key, fingerprint, now()))
            self.notification(db, tenant, env, execution, 'mandate.pre_notification', {'mandate_id': identity, 'amount_paise': amount, 'due_at': due})
            self.event(db, tenant, env, execution, 'occurrence.scheduled', 'merchant', {'due_at': due, 'occurrence': occurrence})
            return self.get(db, 'fidelity_occurrences', tenant, env, execution)

    def execute_occurrence(self, tenant, env, identity):
        with self.store.transaction() as db:
            self.require_enabled(tenant, env, db)
            execution = self.get(db, 'fidelity_occurrences', tenant, env, identity)
            mandate = self.get(db, 'fidelity_mandates', tenant, env, execution['mandate_id'])
            if execution['state'] in {'succeeded', 'declined', 'reversed', 'cancelled', 'expired'}: return execution
            db.execute('UPDATE fidelity_occurrences SET retry_after=? WHERE id=?', (time.time() + 2, identity))
            timestamp = self.time(tenant, env, db)
            if not execution['payment_id']:
                require(timestamp >= execution['due_at'], 'NOT_DUE', 'The recurring execution is not due.', 409)
                if mandate['state'] == 'paused': return execution
                if mandate['state'] != 'active' or timestamp >= mandate['ends_at']:
                    db.execute("UPDATE fidelity_occurrences SET state='cancelled',error='MANDATE_INACTIVE' WHERE id=?", (identity,))
                    self.notification(db, tenant, env, identity, 'mandate.execution_result', {'state': 'cancelled'})
                    return self.get(db, 'fidelity_occurrences', tenant, env, identity)
        # Stable core acceptance key closes a crash between submit and local binding.
        payment = self.upi.read(tenant, env, execution['payment_id']) if execution['payment_id'] else self.upi.submit(tenant, env, {
            'payer_id': mandate['payer_id'], 'payee_id': mandate['payee_id'], 'device_id': mandate['device_id'],
            'amount_paise': execution['amount_paise'], 'request_key': 'lifecycle-occurrence:' + identity})
        with self.store.transaction() as db:
            db.execute('UPDATE fidelity_occurrences SET payment_id=?,state=? WHERE id=? AND (payment_id IS NULL OR payment_id=?)', (payment['id'], payment['state'], identity, payment['id']))
            if payment['state'] == 'awaiting_authorization':
                latest = self.get(db, 'fidelity_mandates', tenant, env, mandate['id'])
                if latest['state'] != 'active': return self.get(db, 'fidelity_occurrences', tenant, env, identity)
                approval = json.loads(latest['authorization_json'])
                self._reserve(db, tenant, env, payment, mandate['device_id'], 'mandate', mandate['id'], approval['credential_epoch'])
        if payment['state'] == 'awaiting_authorization':
            self.upi.authorize(tenant, env, payment['id'], {'device_id': mandate['device_id'], 'device_proof': self.upi._challenge(mandate['device_id']), 'approved': True})
        current = self.upi.read(tenant, env, payment['id'])
        with self.store.transaction() as db:
            state = 'awaiting_evidence' if current['state'] in {'succeeded', 'declined', 'reversed', 'expired', 'cancelled'} and not current['evidence'] else current['state']
            db.execute('UPDATE fidelity_occurrences SET state=?,error=NULL WHERE id=?', (state, identity))
            if current['evidence']:
                self.notification(db, tenant, env, identity, 'mandate.execution_result', {'state': current['state'], 'payment_id': current['id'], 'evidence_hash': current['evidence']['manifest_hash']})
            return self.get(db, 'fidelity_occurrences', tenant, env, identity)

    def tick(self):
        # Small batches provide backpressure; failures remain attached to the work.
        with self.store.reader() as db:
            rows = [dict(x) for x in db.execute("SELECT * FROM fidelity_occurrences WHERE retry_after<=? AND state NOT IN ('succeeded','declined','reversed','cancelled','expired') ORDER BY retry_after,due_at,id LIMIT 50", (time.time(),))]
            complaints = [dict(x) for x in db.execute("SELECT * FROM fidelity_complaints WHERE escalated=0 AND state!='resolved' ORDER BY deadline LIMIT 50")]
        for row in rows:
            if row['due_at'] > self.time(row['tenant_id'], row['environment_id']): continue
            try: self.execute_occurrence(row['tenant_id'], row['environment_id'], row['id'])
            except DomainError as error:
                with self.store.transaction() as db:
                    db.execute('UPDATE fidelity_occurrences SET error=?,retry_after=? WHERE id=?', (error.code, time.time() + 5, row['id']))
        for row in complaints:
            if row['deadline'] <= self.time(row['tenant_id'], row['environment_id']):
                with self.store.transaction() as db:
                    if db.execute('UPDATE fidelity_complaints SET escalated=1 WHERE id=? AND escalated=0', (row['id'],)).rowcount:
                        self.event(db, row['tenant_id'], row['environment_id'], row['id'], 'complaint.escalated', 'scheduler', {'deadline': row['deadline']})
