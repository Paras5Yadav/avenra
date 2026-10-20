"""Durable, signed notification delivery to operator-approved destinations."""
import hashlib
import hmac
import time
from urllib.parse import urlsplit

from .errors import require
from .gateways import EngineError
from .services import Transport
from .util import canonical, digest, label, now, reference, uid


class Callbacks:
    def __init__(self, app):
        self.app, self.store = app, app.store

    def secret(self, identity, version):
        return hmac.new(self.store.signing_key, ('callback:' + identity + ':' + str(version)).encode(), hashlib.sha256).hexdigest()

    def register(self, tenant, env, data):
        self.app.environment(tenant, env, True)
        name, url = label(data.get('name')), label(data.get('url'), 'Callback URL', 1000)
        parsed = urlsplit(url)
        require(parsed.scheme in {'http', 'https'} and parsed.hostname and not (parsed.username or parsed.password or parsed.fragment or parsed.query), 'INVALID_DESTINATION', 'Callback must be an approved HTTP endpoint without embedded credentials.')
        origin = parsed.scheme + '://' + parsed.netloc
        allowed = self.app.adapters.configuration().get('callback_origins', ['http://127.0.0.1:4888'])
        require(origin in allowed and not any(ord(x) < 32 for x in url), 'DESTINATION_DENIED', 'Callback origin is not on the operator allowlist.', 403)
        Transport(origin)
        key, fingerprint = reference(data.get('request_key')), digest({'name': name, 'url': url})
        with self.store.transaction() as db:
            old = self.app.banking._existing(db, 'bank_callback_destinations', tenant, env, key, fingerprint)
            if old:
                return {**old, 'signing_secret': self.secret(old['id'], old['key_version'])}
            identity = uid('destination')
            db.execute('INSERT INTO bank_callback_destinations VALUES(?,?,?,?,?,?,?,?,?,?)', (identity, tenant, env, name, url, 1, 'active', key, fingerprint, now()))
            self.store.audit(db, tenant, env, 'callback.registered', identity, {'url': url})
            record = self.app.banking._get(db, 'bank_callback_destinations', tenant, env, identity)
            return {**record, 'signing_secret': self.secret(identity, 1)}

    def enqueue(self, db, operation):
        destination = operation.get('callback_id')
        if not destination:
            return
        target = self.app.banking._get(db, 'bank_callback_destinations', operation['tenant_id'], operation['environment_id'], destination)
        event = uid('notification')
        payload = {'event_id': event, 'operation_id': operation['id'], 'state': operation['state'], 'amount_paise': operation['amount_paise'], 'profile': operation['profile']}
        inserted = db.execute("INSERT OR IGNORE INTO bank_notifications VALUES(?,?,?,?,?,?,'pending',?,NULL)", (event, operation['id'], destination, canonical(payload), digest(payload), target['key_version'], now())).rowcount
        if inserted:
            self.store.enqueue(db, operation['tenant_id'], operation['environment_id'], 'bank_callback', event, {}, 'notification:' + event)

    def deliver(self, tenant, env, identity):
        with self.store.reader() as db:
            event = db.execute('SELECT n.* FROM bank_notifications n JOIN bank_operations o ON o.id=n.operation_id WHERE n.id=? AND o.tenant_id=? AND o.environment_id=?', (identity, tenant, env)).fetchone()
            require(event, 'NOT_FOUND', 'Notification not found.', 404)
            if event['state'] == 'delivered':
                return dict(event)
            target = self.app.banking._get(db, 'bank_callback_destinations', tenant, env, event['destination_id'])
        require(target['status'] == 'active', 'DESTINATION_DISABLED', 'Callback destination is disabled.', 409)
        parsed = urlsplit(target['url'])
        origin = parsed.scheme + '://' + parsed.netloc
        allowed = self.app.adapters.configuration().get('callback_origins', ['http://127.0.0.1:4888'])
        require(origin in allowed, 'DESTINATION_DENIED', 'Destination is no longer allowed.', 403)
        timestamp = str(int(time.time()))
        signed = identity + '.' + timestamp + '.' + event['payload_json']
        signature = hmac.new(self.secret(target['id'], event['key_version']).encode(), signed.encode(), hashlib.sha256).hexdigest()
        status = 200
        try:
            status, _ = Transport(origin).call('POST', parsed.path or '/', event['payload_json'].encode(), {'X-Avenra-Event': identity, 'X-Avenra-Timestamp': timestamp, 'X-Avenra-Signature': 'v1=' + signature}, timeout=5, raw=True, with_status=True)
        except EngineError as error:
            status = error.status
            with self.store.transaction() as db:
                db.execute('INSERT INTO bank_notification_attempts VALUES(?,?,?,?)', (uid('delivery'), identity, status, now()))
            raise
        with self.store.transaction() as db:
            db.execute('INSERT INTO bank_notification_attempts VALUES(?,?,?,?)', (uid('delivery'), identity, status, now()))
            db.execute("UPDATE bank_notifications SET state='delivered',delivered_at=? WHERE id=?", (now(), identity))
            self.store.audit(db, tenant, env, 'callback.delivered', identity, {'destination_id': target['id']})
        return {'state': 'delivered'}
