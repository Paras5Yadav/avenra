"""Durable, scoped post-payment monitoring against the pinned analysis profile."""
import hashlib
import hmac
import json
import time
from datetime import datetime
from urllib.parse import quote

from .errors import DomainError, require
from .gateways import EngineError
from .services import Transport
from .util import canonical, digest, now, uid

PROFILE = 'avenra-analysis/1.0'


def identifier(*parts):
    return hashlib.sha256(':'.join(parts).encode()).hexdigest()[:32]


def messages(operation, accounts):
    """Build synthetic analysis messages; these are not UPI network messages."""
    scope = (operation['tenant_id'], operation['environment_id'])
    credit_id = identifier(*scope, operation['id'], 'credit')
    status_id = identifier(*scope, operation['id'], 'status')
    timestamp = operation['updated_at']
    amount = operation['amount_paise'] / 100
    def agent(bank):
        return {'FinInstnId': {'ClrSysMmbId': {'MmbId': identifier(*scope, bank)}}}
    def person(account):
        return {'Nm': 'Avenra synthetic participant', 'Id': {'PrvtId': {'DtAndPlcOfBirth': {'BirthDt': '2000-01-01', 'CityOfBirth': 'Synthetic', 'CtryOfBirth': 'IN'}, 'Othr': [{'Id': identifier(*scope, account['customer_id']), 'SchmeNm': {'Prtry': 'AVENRA'}}]}}, 'CtctDtls': {'MobNb': '+91-9000000000'}}
    def account(value):
        return {'Id': {'Othr': [{'Id': identifier(*scope, value['account_id']), 'SchmeNm': {'Prtry': 'AVENRA'}}]}, 'Nm': 'Avenra synthetic account'}
    def amt(value):
        return {'Amt': {'Amt': value, 'Ccy': 'INR'}}
    payer, payee = accounts
    charge = {**amt(0), 'Agt': agent(payer['bank_id'])}
    credit = {'FIToFICstmrCdtTrf': {
        'GrpHdr': {'MsgId': credit_id, 'CreDtTm': timestamp, 'NbOfTxs': 1, 'SttlmInf': {'SttlmMtd': 'CLRG'}},
        'CdtTrfTxInf': {'PmtId': {'InstrId': credit_id, 'EndToEndId': credit_id}, 'IntrBkSttlmAmt': amt(amount), 'InstdAmt': amt(amount), 'ChrgBr': 'DEBT', 'ChrgsInf': charge, 'InitgPty': person(payer), 'Dbtr': person(payer), 'DbtrAcct': account(payer), 'DbtrAgt': agent(payer['bank_id']), 'Cdtr': person(payee), 'CdtrAcct': account(payee), 'CdtrAgt': agent(payee['bank_id']), 'Purp': {'Cd': 'TEST'}},
        'RgltryRptg': {'Dtls': {'Tp': 'SYNTHETIC', 'Cd': 'TEST'}}, 'RmtInf': {'Ustrd': 'Avenra test operation'},
        'SplmtryData': {'Envlp': {'Doc': {'InitgPty': {'Glctn': {'Lat': '0', 'Long': '0'}}, 'Xprtn': 'Synthetic'}}}}}
    status = {'FIToFIPmtSts': {'GrpHdr': {'MsgId': status_id, 'CreDtTm': timestamp}, 'TxInfAndSts': {'OrgnlInstrId': credit_id, 'OrgnlEndToEndId': credit_id, 'TxSts': 'ACCC', 'ChrgsInf': [charge], 'AccptncDtTm': timestamp, 'InstgAgt': agent(payer['bank_id']), 'InstdAgt': agent(payee['bank_id'])}}}
    return {'profile': PROFILE, 'operation_id': operation['id'], 'credit': credit, 'status': status}


class Monitoring:
    def __init__(self, app):
        self.app = app

    def configuration(self, tenant):
        client, config = self.app.adapters.client('monitoring', tenant)
        require(config.get('classification') == 'simulation' and config.get('profile') == PROFILE, 'SERVICE_CONFIGURATION', 'Monitoring needs a pinned simulation analysis profile.', 503)
        require(config.get('history_dsn') and config.get('admin_origin'), 'SERVICE_CONFIGURATION', 'Monitoring recovery and report connections are required.', 503)
        return client, Transport(config['admin_origin'], client.headers, config.get('ca_file')), config

    def submit(self, tenant, env, data):
        self.app.environment(tenant, env, True)
        self.configuration(tenant)
        with self.app.store.transaction() as db:
            operation = self.app.banking._get(db, 'bank_operations', tenant, env, data.get('operation_id'))
            require(operation['state'] == 'succeeded', 'MONITORING_PENDING', 'This analysis profile requires a confirmed payment.', 409)
            return self.enqueue(db, operation)

    def enqueue(self, db, operation):
        tenant, env, op_id = operation['tenant_id'], operation['environment_id'], operation['id']
        config = self.app.adapters.configuration().get('services', {}).get('monitoring', {})
        if tenant not in config.get('tenant_bindings', {}):
            return None
        key = 'operation:' + op_id
        old = db.execute("SELECT * FROM external_commands WHERE tenant_id=? AND environment_id=? AND service='monitoring' AND request_key=?", (tenant, env, key)).fetchone()
        if old:
            return dict(old)
        accounts = [self.app.banking._get(db, 'bank_accounts', tenant, env, operation[field], 'account_id') for field in ('payer_id', 'payee_id')]
        payload = messages(operation, accounts)
        identity = uid('analysis')
        db.execute('INSERT INTO external_commands(id,tenant_id,environment_id,service,action,path,body_json,request_key,request_hash,state,result_json,error,created_at,updated_at,binding_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (identity, tenant, env, 'monitoring', 'evaluate_payment', '/analysis', canonical(payload), key, digest(payload), 'pending', None, None, now(), now(), self.app.adapters.binding_hash('monitoring', tenant)))
        self.app.store.enqueue(db, tenant, env, 'bank_analysis', identity, {}, 'analysis:' + identity)
        self.app.store.audit(db, tenant, env, 'analysis.accepted', identity, {'operation_id': op_id, 'profile': PROFILE, 'payload_hash': digest(payload)})
        return self.app.banking._get(db, 'external_commands', tenant, env, identity)

    def observed(self, config, kind, payload):
        """Read-only native inbox proves acceptance after a lost HTTP response."""
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
        dsn = config['history_dsn']
        info = conninfo_to_dict(dsn)
        require(info.get('host') in {'127.0.0.1', '::1'} or info.get('sslmode') == 'verify-full', 'SERVICE_CONFIGURATION', 'Remote monitoring history requires verified TLS.', 503)
        root = 'FIToFICstmrCdtTrf' if kind == '008' else 'FIToFIPmtSts'
        msg_id = payload[root]['GrpHdr']['MsgId']
        with psycopg.connect(dsn, connect_timeout=3, options='-c default_transaction_read_only=on -c statement_timeout=3000') as connection:
            # Table names are selected from this fixed profile, never input.
            value = connection.execute('SELECT document FROM pacs' + kind + ' WHERE messageid=%s AND tenantid=%s', (msg_id, config['tenant_bindings'][config['_tenant']])).fetchone()
        if not value:
            return False
        actual = {k: v for k, v in value[0].items() if k not in {'TxTp', 'TenantId', 'DataCache'}}
        require(actual == payload, 'ANALYSIS_CONFLICT', 'Native message identity contains different analysis content.', 409)
        return True

    def read(self, tenant, env, identity):
        with self.app.store.reader() as db:
            value = self.app.banking._get(db, 'external_commands', tenant, env, identity)
        require(value['service'] == 'monitoring', 'NOT_FOUND', 'Analysis record not found.', 404)
        if value['state'] == 'succeeded':
            evidence = json.loads(value['result_json'])
            signature = hmac.new(self.app.store.signing_key, digest(evidence['manifest']).encode(), hashlib.sha256).hexdigest()
            value['evidence'] = {**evidence, 'valid': hmac.compare_digest(signature, evidence['signature'])}
        return value

    def process(self, tenant, env, identity):
        command = self.read(tenant, env, identity)
        if command['state'] in {'succeeded', 'rejected'}:
            return command
        client, admin, config = self.configuration(tenant)
        require(command['binding_hash'] == self.app.adapters.binding_hash('monitoring', tenant), 'BINDING_CHANGED', 'Original analysis binding changed; dispatch is blocked.', 409)
        config = {**config, '_tenant': tenant}
        body = json.loads(command['body_json'])
        result, state, error = None, 'unknown', None
        receipts = json.loads(command['result_json'] or '{}').get('ingestion_receipts', {})
        try:
            for kind, name, version in [('008', 'credit', '.001.10'), ('002', 'status', '.001.12')]:
                payload = body[name]
                if not self.observed(config, kind, payload):
                    try:
                        client.call('POST', '/v1/evaluate/iso20022/pacs.' + kind + version, payload, timeout=15)
                        receipts[kind] = 'http_confirmed'
                    except EngineError:
                        if not self.observed(config, kind, payload):
                            raise
                        receipts[kind] = 'recovered_from_native_inbox'
                else:
                    receipts.setdefault(kind, 'existing_native_inbox')
                require(self.observed(config, kind, payload), 'ANALYSIS_UNKNOWN', 'Analysis ingestion has not been confirmed.', 503)
            msg_id = body['status']['FIToFIPmtSts']['GrpHdr']['MsgId']
            observed = admin.call('GET', '/v1/admin/reports/getreportbymsgid?msgid=' + quote(msg_id), timeout=5)
            if observed.get('data'):
                data = observed['data']
                require(data.get('transaction', {}).get('FIToFIPmtSts', {}).get('GrpHdr', {}).get('MsgId') == msg_id and data['transaction'].get('TenantId') == config['tenant_bindings'][tenant] and data.get('report', {}).get('tadpResult', {}).get('cfg') == '4.0.0', 'ANALYSIS_PROFILE_MISMATCH', 'Evaluation does not match the pinned operation and profile.', 409)
                rules = data['report']['tadpResult'].get('typologyResult', [])
                require(rules and all(x.get('ruleResults') and all(r.get('subRuleRef') != '.err' for r in x['ruleResults']) for x in rules), 'ANALYSIS_RULE_ERROR', 'Evaluation contains an unqualified or failed rule.', 503)
                manifest = {'format': 'avenra-analysis-evidence/1', 'profile': PROFILE, 'tenant_id': tenant, 'environment_id': env, 'operation_id': body['operation_id'], 'payload_hash': command['request_hash'], 'ingestion_receipts': receipts, 'report': data, 'observed_at': now(), 'scope': 'Post-payment synthetic analysis; no UPI authorization or regulatory certification.'}
                result = {'manifest': manifest, 'signature': hmac.new(self.app.store.signing_key, digest(manifest).encode(), hashlib.sha256).hexdigest()}
                state = 'succeeded'
            else:
                state, error = 'pending', 'Ingestion confirmed; evaluation report is pending.'
        except (EngineError, DomainError) as failure:
            error = failure.message
        except Exception:
            error = 'Analysis transport or history lookup failed; original message identities are retained.'
        if result is None:
            result = {'ingestion_receipts': receipts}
        with self.app.store.transaction() as db:
            current = self.app.banking._get(db, 'external_commands', tenant, env, identity)
            if current['state'] != 'succeeded':
                db.execute('UPDATE external_commands SET state=?,result_json=?,error=?,updated_at=? WHERE id=?', (state, canonical(result) if result else None, error, now(), identity))
                self.app.store.audit(db, tenant, env, 'analysis.' + state, identity, {'profile': PROFILE, 'result_hash': digest(result) if result else None})
        return self.read(tenant, env, identity)

    def retryable(self, value):
        return value['state'] in {'pending', 'unknown'} and time.time() - datetime.fromisoformat(value['created_at'].replace('Z', '+00:00')).timestamp() < 600
