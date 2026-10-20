"""Synthetic credential issuer, bounded verification and atomic policy reservations."""
import hashlib
import hmac
import re
import secrets

from avenra.errors import DomainError, require
from avenra.simulation import integer
from avenra.upi import fields
from avenra.util import canonical, digest, now, reference, uid
from .profile import PROFILE


class Credentials:
    @staticmethod
    def public_credential(value):
        return {key: value[key] for key in ['account_id', 'device_id', 'epoch', 'failed_attempts', 'locked_until', 'cooling_until', 'updated_at']}

    def _device(self, db, tenant, env, device_id, account_id, proof):
        device = self.get(db, 'upi_devices', tenant, env, device_id)
        self.upi._proof(device, proof)
        account = self.get(db, 'bank_accounts', tenant, env, account_id, 'account_id')
        require(device['state'] == 'active' and account['status'] == 'active', 'DEVICE_INACTIVE', 'Use an active device and account.', 403)
        require(device['customer_id'] == account['customer_id'], 'ACCOUNT_OWNERSHIP', 'Device belongs to a different customer.', 403)
        require(db.execute('SELECT 1 FROM upi_links WHERE device_id=? AND account_id=?', (device_id, account_id)).fetchone(), 'ACCOUNT_NOT_LINKED', 'Link this proved device to its own account.', 403)
        return device, account

    def discover(self, tenant, env, data):
        fields(data, ['device_id', 'device_proof'])
        with self.store.reader() as db:
            self.require_enabled(tenant, env, db)
            device = self.get(db, 'upi_devices', tenant, env, data['device_id'])
            self.upi._proof(device, data['device_proof'])
            require(device['state'] == 'active', 'DEVICE_INACTIVE', 'Activate this synthetic device first.', 403)
            accounts = db.execute("SELECT a.account_id,a.bank_id,c.vpa FROM bank_accounts a JOIN accounts c ON c.id=a.account_id WHERE a.tenant_id=? AND a.environment_id=? AND a.customer_id=? AND a.status='active' ORDER BY a.account_id", (tenant, env, device['customer_id'])).fetchall()
        return {'accounts': [dict(x) for x in accounts], 'verification': 'Synthetic device proof; no SMS or bank account-discovery protocol is asserted.'}

    def _code(self, identity):
        value = hmac.new(self.store.signing_key, ('synthetic-issuer-challenge:' + identity).encode(), hashlib.sha256).hexdigest()
        return str(int(value[:12], 16) % 1_000_000).zfill(6)

    def challenge(self, tenant, env, data):
        fields(data, ['device_id', 'account_id', 'device_proof', 'purpose', 'request_key', 'synthetic_bank_verified'])
        require(data['purpose'] in {'setup', 'reset'} and data['synthetic_bank_verified'] is True, 'SYNTHETIC_ISSUER_REQUIRED', 'Explicitly model the synthetic bank identity-verification result.')
        key = reference(data['request_key'])
        fingerprint = digest({k: data[k] for k in ['device_id', 'account_id', 'purpose', 'synthetic_bank_verified']})
        with self.store.transaction() as db:
            self.require_enabled(tenant, env, db)
            self._device(db, tenant, env, data['device_id'], data['account_id'], data['device_proof'])
            old = self.bank._existing(db, 'fidelity_challenges', tenant, env, key, fingerprint)
            if old: return {k: old[k] for k in ['id', 'account_id', 'device_id', 'purpose', 'expires_at', 'state']} | {'synthetic_challenge': self._code(old['id'])}
            existing = db.execute('SELECT epoch FROM fidelity_credentials WHERE account_id=?', (data['account_id'],)).fetchone()
            require(bool(existing) == (data['purpose'] == 'reset'), 'CREDENTIAL_STATE', 'Use setup for a new credential and reset for an existing one.', 409)
            identity = uid('challenge')
            db.execute('INSERT INTO fidelity_challenges(id,tenant_id,environment_id,device_id,account_id,purpose,request_key,request_hash,expires_at,issued_epoch,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (identity, tenant, env, data['device_id'], data['account_id'], data['purpose'], key, fingerprint, self.time(tenant, env, db) + PROFILE['rules']['challenge_seconds'], existing['epoch'] if existing else 0, now()))
            self.event(db, tenant, env, identity, 'credential.challenge_issued', 'synthetic-bank', {'purpose': data['purpose'], 'account_id': data['account_id']})
            row = self.get(db, 'fidelity_challenges', tenant, env, identity)
            return {k: row[k] for k in ['id', 'account_id', 'device_id', 'purpose', 'expires_at', 'state']} | {'synthetic_challenge': self._code(identity)}

    @staticmethod
    def _pin_format(pin):
        require(isinstance(pin, str) and re.fullmatch('[0-9]{6}', pin), 'INVALID_SYNTHETIC_PIN', 'Use a six-digit synthetic test PIN. Never enter a real banking PIN.')

    @staticmethod
    def _hash_pin(pin, salt):
        return hashlib.scrypt(pin.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32).hex()

    def complete_credential(self, tenant, env, identity, data):
        fields(data, ['device_proof', 'synthetic_challenge', 'new_synthetic_pin'])
        self._pin_format(data['new_synthetic_pin'])
        error, result = None, None
        completion = hmac.new(self.store.signing_key, canonical({'challenge_id': identity, 'pin': data['new_synthetic_pin'], 'code': data['synthetic_challenge']}).encode(), hashlib.sha256).hexdigest()
        with self.store.transaction() as db:
            self.require_enabled(tenant, env, db)
            challenge = self.get(db, 'fidelity_challenges', tenant, env, identity)
            self._device(db, tenant, env, challenge['device_id'], challenge['account_id'], data['device_proof'])
            if challenge['state'] == 'completed':
                require(hmac.compare_digest(challenge['completion_hash'], completion), 'CREDENTIAL_CONFLICT', 'Completed challenge has different credential content.', 409)
                return self.public_credential(dict(db.execute('SELECT * FROM fidelity_credentials WHERE account_id=?', (challenge['account_id'],)).fetchone()))
            timestamp = self.time(tenant, env, db)
            if timestamp >= challenge['expires_at']:
                error = 'CHALLENGE_EXPIRED'
                db.execute("UPDATE fidelity_challenges SET state='expired' WHERE id=?", (identity,))
            elif challenge['attempts'] >= PROFILE['rules']['challenge_attempts']:
                error = 'CHALLENGE_LOCKED'
            elif not isinstance(data['synthetic_challenge'], str) or not hmac.compare_digest(self._code(identity), data['synthetic_challenge']):
                error = 'CHALLENGE_INVALID'
                db.execute('UPDATE fidelity_challenges SET attempts=attempts+1 WHERE id=?', (identity,))
            else:
                old = db.execute('SELECT * FROM fidelity_credentials WHERE account_id=?', (challenge['account_id'],)).fetchone()
                require(not old or old['epoch'] == challenge['issued_epoch'], 'CHALLENGE_SUPERSEDED', 'A newer credential superseded this issuer challenge.', 409)
                # A second setup challenge cannot silently replace the first credential.
                require(challenge['purpose'] == 'reset' or not old, 'CREDENTIAL_STATE', 'A credential already exists; request an explicit reset.', 409)
                salt = secrets.token_hex(16)
                pin_hash = self._hash_pin(data['new_synthetic_pin'], salt)
                epoch = old['epoch'] + 1 if old else 1
                cooling = timestamp + PROFILE['rules']['reset_cooling_seconds'] if old else 0
                if old:
                    db.execute('UPDATE fidelity_credentials SET device_id=?,salt=?,pin_hash=?,epoch=?,failed_attempts=0,locked_until=0,cooling_until=?,updated_at=? WHERE account_id=?',
                               (challenge['device_id'], salt, pin_hash, epoch, cooling, now(), challenge['account_id']))
                else:
                    db.execute('INSERT INTO fidelity_credentials VALUES(?,?,?,?,?,?,?,0,0,?,?)', (challenge['account_id'], tenant, env, challenge['device_id'], salt, pin_hash, epoch, cooling, now()))
                db.execute("UPDATE fidelity_challenges SET state='completed',completion_hash=? WHERE id=?", (completion, identity))
                self.event(db, tenant, env, challenge['account_id'], 'credential.' + challenge['purpose'], 'synthetic-bank', {'epoch': epoch, 'cooling_until': cooling})
                result = self.public_credential(dict(db.execute('SELECT * FROM fidelity_credentials WHERE account_id=?', (challenge['account_id'],)).fetchone()))
            if error:
                self.event(db, tenant, env, identity, 'credential.challenge_rejected', 'synthetic-bank', {'code': error})
        require(not error, error or 'CREDENTIAL', 'Synthetic credential challenge was refused.', 403)
        return result

    def _check_pin(self, db, tenant, env, account, device, pin):
        self._pin_format(pin)
        credential = db.execute('SELECT * FROM fidelity_credentials WHERE account_id=? AND tenant_id=? AND environment_id=?', (account, tenant, env)).fetchone()
        if not credential or credential['device_id'] != device: return None, 'CREDENTIAL_REQUIRED'
        timestamp = self.time(tenant, env, db)
        if timestamp < credential['locked_until']: return None, 'CREDENTIAL_LOCKED'
        if timestamp < credential['cooling_until']: return None, 'CREDENTIAL_COOLING'
        valid = hmac.compare_digest(self._hash_pin(pin, credential['salt']), credential['pin_hash'])
        if not valid:
            failures = (0 if credential['locked_until'] else credential['failed_attempts']) + 1
            lock = timestamp + PROFILE['rules']['pin_lock_seconds'] if failures >= PROFILE['rules']['pin_attempts'] else 0
            db.execute('UPDATE fidelity_credentials SET failed_attempts=?,locked_until=?,updated_at=? WHERE account_id=?', (failures, lock, now(), account))
            self.event(db, tenant, env, account, 'credential.failed', 'synthetic-payer', {'failed_attempts': failures, 'locked_until': lock})
            return None, 'CREDENTIAL_LOCKED' if lock else 'CREDENTIAL_INVALID'
        db.execute('UPDATE fidelity_credentials SET failed_attempts=0,locked_until=0,updated_at=? WHERE account_id=?', (now(), account))
        return dict(credential), None

    def _reserve(self, db, tenant, env, payment, device, source, source_id, epoch):
        old = db.execute('SELECT * FROM fidelity_guards WHERE payment_id=?', (payment['id'],)).fetchone()
        if old:
            require((old['source'], old['source_id'], old['device_id'], old['credential_epoch']) == (source, source_id, device, epoch), 'AUTHORIZATION_CONFLICT', 'This intent has a different retained lifecycle approval.', 409)
            return dict(old)
        timestamp, rules = self.time(tenant, env, db), PROFILE['rules']
        day = int((timestamp + rules['day_offset_seconds']) // 86400)
        totals = db.execute("SELECT count(*),COALESCE(SUM(g.amount_paise),0) FROM fidelity_guards g JOIN upi_payments p ON p.id=g.payment_id WHERE g.tenant_id=? AND g.environment_id=? AND g.day_index=? AND p.payer_id=? AND p.state NOT IN ('declined','expired','cancelled','reversed')", (tenant, env, day, payment['payer_id'])).fetchone()
        require(payment['amount_paise'] <= rules['per_payment_paise'], 'PAYMENT_LIMIT', 'Payment exceeds this synthetic profile limit.', 409)
        require(totals[0] < rules['daily_count'] and totals[1] + payment['amount_paise'] <= rules['daily_paise'], 'DAILY_LIMIT', 'The synthetic daily budget includes pending and uncertain payments.', 409)
        decision = digest({'intent': payment['request_hash'], 'device': device, 'source': source, 'source_id': source_id, 'epoch': epoch, 'profile': PROFILE})
        db.execute('INSERT INTO fidelity_guards VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', (payment['id'], tenant, env, device, source, source_id, epoch, payment['request_hash'], day, payment['amount_paise'], decision, timestamp, now()))
        # Explicit columns avoid coupling the wire format to storage order.
        self.event(db, tenant, env, payment['id'], 'authorization.reserved', 'synthetic-payer', {'source': source, 'decision_hash': decision, 'day_index': day})
        return dict(db.execute('SELECT * FROM fidelity_guards WHERE payment_id=?', (payment['id'],)).fetchone())

    def authorize_payment(self, tenant, env, identity, data):
        fields(data, ['device_id', 'device_proof', 'synthetic_pin', 'approved'])
        require(type(data['approved']) is bool, 'AUTHORIZATION_REQUIRED', 'Choose an explicit approval decision.')
        error = None
        with self.store.transaction() as db:
            self.require_enabled(tenant, env, db)
            payment = self.get(db, 'upi_payments', tenant, env, identity)
            self._device(db, tenant, env, data['device_id'], payment['payer_id'], data['device_proof'])
            if data['approved']:
                credential, error = self._check_pin(db, tenant, env, payment['payer_id'], data['device_id'], data['synthetic_pin'])
                if not error:
                    require(payment['state'] == 'awaiting_authorization' or payment['authorization_json'], 'INVALID_STATE', 'Intent no longer accepts approval.', 409)
                    self._reserve(db, tenant, env, payment, data['device_id'], 'credential', payment['payer_id'], credential['epoch'])
        require(not error, error or 'CREDENTIAL', 'Synthetic credential verification was refused.', 403)
        return self.upi.authorize(tenant, env, identity, {k: data[k] for k in ['device_id', 'device_proof', 'approved']})

    def guard_error(self, db, tenant, env, payment):
        guard = db.execute('SELECT * FROM fidelity_guards WHERE payment_id=? AND tenant_id=? AND environment_id=?', (payment['id'], tenant, env)).fetchone()
        if not guard or guard['intent_hash'] != payment['request_hash']: return 'LIFECYCLE_APPROVAL_REQUIRED'
        if guard['source'] == 'credential':
            credential = db.execute('SELECT epoch FROM fidelity_credentials WHERE account_id=?', (payment['payer_id'],)).fetchone()
            if not credential or credential['epoch'] != guard['credential_epoch']: return 'CREDENTIAL_CHANGED_BEFORE_DISPATCH'
        else:
            mandate = db.execute('SELECT * FROM fidelity_mandates WHERE id=? AND tenant_id=? AND environment_id=?', (guard['source_id'], tenant, env)).fetchone()
            if not mandate or mandate['state'] != 'active': return 'MANDATE_INACTIVE_BEFORE_DISPATCH'
            if not mandate['starts_at'] <= self.time(tenant, env, db) < mandate['ends_at']: return 'MANDATE_EXPIRED_BEFORE_DISPATCH'
        return None
