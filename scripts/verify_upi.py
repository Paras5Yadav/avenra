#!/usr/bin/env python3
"""Exercise the participant engine against native financial services and both stores."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from avenra.app import App
from avenra.banking import BankingBusy
from avenra.errors import DomainError
from avenra.local_database import admin_dsn
from avenra.upi import PROFILE_HASH
from avenra.participant_connections import LOCAL, EXTERNAL
from avenra.util import application_fingerprint, canonical, digest, uid


def crash_child(directory, env, payment):
    directory = Path(directory)
    connection = directory / 'control.dsn'
    app = App(directory, control_dsn=connection.read_text().strip() if connection.exists() else None)
    original = app.banking.process
    def killed(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[3] == 'credit' and result['state'] == 'succeeded':
            os.kill(os.getpid(), signal.SIGKILL)
        return result
    app.banking.process = killed
    for _ in range(12):
        app.upi.process('tenant_avenra', env, payment)
    raise RuntimeError('Crash boundary was not reached.')


def main():
    postgres, dsn, app = '--postgres' in sys.argv, None, None
    directory = ROOT / '.local/qa' / uid('upi')
    directory.mkdir(parents=True, mode=0o700)
    report = {'passed': False, 'checks': [], 'directory': str(directory), 'scope': 'Executable synthetic participant contract; real local accounting/fault services; not official UPI conformance.'}
    def check(name, condition, detail=None):
        report['checks'].append({'name': name, 'passed': bool(condition), 'detail': detail})
        if not condition:
            raise AssertionError(name)
    def denied(name, callback, code=None):
        try:
            callback()
        except DomainError as error:
            check(name, not code or error.code == code, error.code)
        else:
            check(name, False)
    try:
        if postgres:
            import psycopg
            from psycopg import sql
            from psycopg.conninfo import conninfo_to_dict, make_conninfo
            database = 'avenra_upi_' + uid('pg').split('_')[1]
            with psycopg.connect(admin_dsn(ROOT), autocommit=True) as connection:
                connection.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
            dsn = make_conninfo(**{**conninfo_to_dict(admin_dsn(ROOT)), 'dbname': database})
            (directory / 'control.dsn').write_text(dsn);(directory / 'control.dsn').chmod(0o600)
        app = App(directory, control_dsn=dsn)
        app.bootstrap()
        tenant = 'tenant_avenra'
        env = app.create_environment(tenant, 'Participant acceptance ' + uid('case'), 'banking')['id']
        app.provision(tenant, env)
        b, u = app.banking, app.upi
        banks = [b.bank(tenant, env, {'name': 'Lab Bank ' + s, 'code': s, 'request_key': s}) for s in ['bank_a', 'bank_b']]
        customers = [b.customer(tenant, env, {'name': name, 'kind': kind, 'request_key': name}) for name, kind in [('Payer', 'customer'), ('Merchant', 'merchant')]]
        accounts = [b.account(tenant, env, {'bank_id': bank['id'], 'customer_id': customer['id'], 'vpa': vpa, 'opening_paise': opening, 'request_key': vpa})['account_id'] for bank, customer, vpa, opening in zip(banks, customers, ['payer@bank_a', 'merchant@bank_b'], [1_000_000, 100_000])]
        for account in accounts:
            b.provision_account(tenant, env, account)
        topology = u.participants(tenant, env, {})
        check('One network and independent bank/PSP actors are persisted', len(topology['participants']) == 9)
        check('Topology installation is idempotent', len(u.participants(tenant, env, {})['participants']) == 9)
        check('Current official protocol gaps remain explicit', topology['official_conformance'] is False and len(topology['profile']['unqualified']) == 4)
        actor = lambda bank, role: next(x for x in topology['participants'] if x['bank_id'] == bank and x['role'] == role)
        connections = u.connections
        registry = connections.overview(tenant, env)
        check('Connection registry distinguishes an executable lab profile from pending institutional requirements', registry['profiles'][0]['implemented'] and not registry['profiles'][1]['implemented'] and {x['type'] for x in registry['profiles'][1]['requirements']} == {'specification', 'software', 'access', 'acceptance', 'operations'})
        connection_body = {'name': 'Remitter revision one', 'participant_id': actor(banks[0]['id'], 'remitter')['id'], 'adapter_id': LOCAL, 'request_key': 'remitter-connection'}
        revision = connections.create(tenant, env, connection_body)
        check('Local connection revisions need no institutional credentials', revision['executable'] and not revision['active'] and revision['configuration'] == {})
        check('Connection acceptance replays its original revision', connections.create(tenant, env, connection_body)['id'] == revision['id'])
        denied('Changed connection payload under the same key conflicts', lambda: connections.create(tenant, env, {**connection_body, 'name': 'Changed name'}), 'IDEMPOTENCY_CONFLICT')
        denied('Raw secrets cannot enter connection configuration', lambda: connections.create(tenant, env, {**connection_body, 'api_key': 'synthetic-test-secret'}), 'INVALID_UPI_REQUEST')
        denied('Malformed participant identity is rejected before database access', lambda: connections.create(tenant, env, {**connection_body, 'participant_id': {}}), 'INVALID_FIELD')
        denied('Local connections cannot silently acquire external endpoints', lambda: connections.create(tenant, env, {**connection_body, 'endpoint': 'https://bank.example'}), 'INVALID_CONNECTION')
        external = connections.create(tenant, env, {**connection_body, 'name': 'Future institutional revision', 'adapter_id': EXTERNAL, 'request_key': 'external-draft', 'endpoint': 'https://bank.example/uat', 'secret_reference': 'secret://future-test-identity'})
        check('An external endpoint and secret reference remain a nonexecuting draft', not external['executable'] and not external['active'] and len(external['remaining']) == 8)
        external_check = connections.validate(tenant, env, external['id'], {})
        check('External checks retain missing software, access and acceptance without claiming connectivity', not external_check['manifest']['passed'] and external_check['signature_valid'] and not external_check['manifest']['official_conformance'])
        denied('Adding credential references cannot activate an unimplemented external adapter', lambda: connections.activate(tenant, env, external['id'], {'check_id': external_check['id']}), 'CONNECTION_UNQUALIFIED')
        for endpoint in ['http://bank.example', 'https://user:password@bank.example', 'https://@bank.example', 'https://bank.example/path?api_key=secret']:
            denied('Unsafe endpoint metadata is rejected: ' + endpoint.split(':')[0], lambda endpoint=endpoint: connections.create(tenant, env, {**connection_body, 'adapter_id': EXTERNAL, 'endpoint': endpoint}), 'INVALID_CONNECTION')
        denied('Secret references cannot contain private keys or paths', lambda: connections.create(tenant, env, {**connection_body, 'adapter_id': EXTERNAL, 'endpoint': 'https://bank.example', 'secret_reference': '../private.key'}), 'INVALID_SECRET_REFERENCE')
        denied('Connections cannot reference a participant in another workspace', lambda: connections.create('tenant_integration', env, connection_body), 'NOT_FOUND')
        receipt = connections.validate(tenant, env, revision['id'], {})
        denied('Malformed check identity is rejected before database access', lambda: connections.activate(tenant, env, revision['id'], {'check_id': {}}), 'INVALID_FIELD')
        check('Local configuration checks bind source, profile and immutable revision', receipt['manifest']['passed'] and receipt['signature_valid'] and receipt['manifest']['config_hash'] == revision['config_hash'] and receipt['manifest']['application_fingerprint'] == app.source_fingerprint)
        denied('A check from another revision cannot authorize activation', lambda: connections.activate(tenant, env, revision['id'], {'check_id': external_check['id']}), 'CONNECTION_CHECK_REQUIRED')
        check('A validated local revision can be bound to its participant', connections.activate(tenant, env, revision['id'], {'check_id': receipt['id']})['active'])
        devices, proofs, links = [], [], []
        for i, customer in enumerate(customers):
            device = u.device(tenant, env, {'customer_id': customer['id'], 'device_reference': 'test-handset', 'request_key': 'device-' + str(i)})
            proof = device['enrolment_challenge']
            denied('Incorrect enrolment proof is denied ' + str(i), lambda: u.device_action(tenant, env, device['id'], 'activate', {'device_proof': '0' * 64, 'synthetic_verification': True}), 'DEVICE_PROOF')
            u.device_action(tenant, env, device['id'], 'activate', {'device_proof': proof, 'synthetic_verification': True})
            devices.append(device);proofs.append(proof)
            links.append(u.link(tenant, env, {'device_id': device['id'], 'account_id': accounts[i], 'psp_id': actor(banks[i]['id'], 'payer_psp')['id']}))
        check('Linked balance inquiry reads the actual accounting engine', u.balance(tenant, env, {'link_id': links[0]['id'], 'device_proof': proofs[0]})['balance_paise'] == 1_000_000)
        denied('A device cannot link another customer account', lambda: u.link(tenant, env, {'device_id': devices[0]['id'], 'account_id': accounts[1], 'psp_id': actor(banks[0]['id'], 'payer_psp')['id']}), 'ACCOUNT_OWNERSHIP')
        check('Public overview does not disclose device challenges or hashes', all('challenge_hash' not in x and 'enrolment_challenge' not in x for x in u.overview(tenant, env)['devices']))
        def balances():
            return {x['id']: x['balance_paise'] for x in app.accounts(tenant, env)}
        def submit(key, amount=1000, **extra):
            return u.submit(tenant, env, {'payer_id': accounts[0], 'payee_id': accounts[1], 'amount_paise': amount, 'device_id': devices[0]['id'], 'request_key': key, **extra})
        def authorize(payment, i=0, approved=True):
            return u.authorize(tenant, env, payment['id'], {'device_id': devices[i]['id'], 'device_proof': proofs[i], 'approved': approved})
        def drain(payment, timeout=8):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                result = app.upi.process(tenant, env, payment['id'])
                if not result['pending']:
                    return app.upi.read(tenant, env, payment['id'])
                time.sleep(.02)
            raise TimeoutError('Participant delivery did not drain.')
        before = balances()
        payment = submit('explicit-authorization', 10_000)
        pinned = json.loads(payment['adapter_bindings_json'])
        check('Prepared intent pins all five adapter roles and exact remitter revision', len(pinned) == 5 and pinned['remitter']['connection_id'] == revision['id'])
        newer = connections.create(tenant, env, {**connection_body, 'name': 'Remitter revision two', 'request_key': 'remitter-connection-two'})
        newer_check = connections.validate(tenant, env, newer['id'], {})
        connections.activate(tenant, env, newer['id'], {'check_id': newer_check['id']})
        check('Changing the active binding cannot redirect an existing request replay', json.loads(submit('explicit-authorization', 10_000)['adapter_bindings_json']) == pinned)
        check('Creating a payment cannot spend funds', payment['state'] == 'awaiting_authorization' and payment['operation_id'] is None and balances() == before)
        denied('A different customer cannot authorize the payer account', lambda: authorize(payment, 1), 'DEVICE_INACTIVE')
        denied('Changed payload under one payment identity is rejected', lambda: submit('explicit-authorization', 10_001), 'IDEMPOTENCY_CONFLICT')
        authorize(payment)
        with app.store.reader() as db:
            operation_id = db.execute('SELECT operation_id FROM upi_payments WHERE id=?', (payment['id'],)).fetchone()[0]
        denied('Generic financial worker cannot bypass participant routing', lambda: b.process(tenant, env, operation_id), 'EXECUTOR_OWNED')
        denied('Generic reconcile cannot bypass participant routing', lambda: b.reconcile(tenant, env, operation_id), 'EXECUTOR_OWNED')
        denied('Beneficiary financial phase cannot precede debit', lambda: b.process(tenant, env, operation_id, 'credit', executor='upi'), 'MESSAGE_ORDER')
        result = drain(payment)
        check('Payment completes through its pinned adapter after the active binding changes', json.loads(result['adapter_bindings_json'])['remitter']['connection_id'] == revision['id'] and result['state'] == 'succeeded')
        check('Signed authorization binds the immutable adapter choices', json.loads(result['authorization_json'])['adapter_bindings_hash'] == digest(pinned))
        check('Seven participant deliveries complete a payment', result['state'] == 'succeeded' and len(result['messages']) == 7 and all(x['delivery_state'] == 'delivered' for x in result['messages']))
        check('Native debit and credit have independent exact effects', balances()[accounts[0]] == before[accounts[0]] - 10_000 and balances()[accounts[1]] == before[accounts[1]] + 10_000 and len(result['operation']['legs']) == 2)
        check('Every participant retains its observed view', {x['role'] for x in result['actor_views']} == {'payer_psp', 'network', 'remitter', 'beneficiary', 'payee_psp'})
        evidence = result['evidence']; fingerprint = digest(evidence['manifest'])
        check('Evidence independently verifies and excludes official conformance', evidence['valid'] and not evidence['manifest']['official_conformance'] and hmac.compare_digest(hmac.new(app.store.signing_key, fingerprint.encode(), hashlib.sha256).hexdigest(), evidence['signature']))
        check('Evidence contains independent real journal observations', len(evidence['manifest']['journal_observations']) == 2 and all(c['passed'] for c in evidence['manifest']['checks']))
        message = result['messages'][1]
        wrapper = {'envelope': json.loads(message['document_json']), 'signature': message['signature']}
        check('Authenticated duplicate returns original recipient receipt', u.receive(tenant, env, wrapper)['replayed'] is True)
        changed = json.loads(canonical(wrapper));changed['envelope']['payload']['amount_paise'] += 1
        denied('Changed signed payment amount is denied', lambda: u.receive(tenant, env, changed), 'MESSAGE_CONFLICT')
        denied('Changed signature is denied', lambda: u.receive(tenant, env, {**wrapper, 'signature': '0' * 128}), 'MESSAGE_CONFLICT')
        denied('Participant receipt cannot cross workspaces', lambda: u.receive('tenant_integration', env, wrapper), 'NOT_FOUND')
        rejected = submit('payer-denied')
        check('Subsequent payments use the newly activated connection revision', json.loads(rejected['adapter_bindings_json'])['remitter']['connection_id'] == newer['id'])
        authorize(rejected, approved=False)
        rejected = drain(rejected)
        check('Payer denial has no financial operation or posting', rejected['state'] == 'declined' and rejected['operation'] is None and rejected['evidence']['valid'])
        before_collect = balances()
        collect = submit('merchant-collect', mode='collect')
        denied('Collect must be presented before payer approval', lambda: authorize(collect), 'COLLECT_NOT_PRESENTED')
        for _ in range(2):u.process(tenant, env, collect['id'])
        check('Collect routes through payee PSP and network to payer PSP', len(u.read(tenant, env, collect['id'])['messages']) == 2 and balances() == before_collect)
        with app.store.transaction() as db:
            db.execute("UPDATE outbox SET state='leased',lease_token='test-handoff',lease_until=? WHERE kind='upi_payment' AND aggregate_id=?", (time.time()+30,collect['id']))
        authorize(collect)
        with app.store.reader() as db:
            wakeup=db.execute("SELECT 1 FROM outbox WHERE kind='upi_payment' AND aggregate_id=? AND state='ready'",(collect['id'],)).fetchone()
        check('Approval during the simulated leased-job handoff retains a fresh durable wakeup',bool(wakeup))
        collect = drain(collect)
        check('Presented merchant collect completes only after approval', collect['state'] == 'succeeded' and len(collect['messages']) == 9)
        denied('Person-to-person collect is unsupported', lambda: u.submit(tenant, env, {'payer_id': accounts[1], 'payee_id': accounts[0], 'amount_paise': 100, 'request_key': 'p2p-collect', 'mode': 'collect'}), 'MERCHANT_REQUIRED')
        qr = u.qr(tenant, env, {'payee_vpa': 'merchant@bank_b', 'amount_paise': 1250, 'reference': 'merchant-order-1'})
        check('QR parsing converts exact decimal rupees to integer paise', qr['decoded']['amount_paise'] == 1250 and qr['decoded']['requires_payer_authorization'])
        denied('Duplicate QR amount fields are rejected', lambda: u.parse_qr(tenant, env, {'uri': qr['uri'] + '&am=1.00'}), 'INVALID_QR')
        denied('Unsupported QR parameters are rejected', lambda: u.parse_qr(tenant, env, {'uri': qr['uri'] + '&url=http%3A%2F%2Fexample.com'}), 'INVALID_QR')
        denied('Authorization cannot silently change the QR amount', lambda: submit('qr-wrong', 1251, mode='qr', qr_uri=qr['uri']), 'QR_MISMATCH')
        qr_payment = submit('qr-payment', 1250, mode='qr', qr_uri=qr['uri']);authorize(qr_payment)
        check('QR intent settles through the participant engine', drain(qr_payment)['state'] == 'succeeded')
        for fault in ['drop_once', 'duplicate_once', 'lose_ack_once', 'corrupt_once', 'delay_once']:
            before = balances();p = submit('delivery-' + fault, 100, delivery_fault={'step': 2, 'type': fault})
            authorize(p);p = drain(p)
            check('Delivery fault recovers without duplicate debit: ' + fault, p['state'] == 'succeeded' and balances()[accounts[0]] == before[accounts[0]] - 100 and balances()[accounts[1]] == before[accounts[1]] + 100)
        for fault in ['lost_debit_response', 'lost_credit_response']:
            before = balances();p = submit(fault, 100, financial_fault=fault);authorize(p)
            for _ in range(8):
                intermediate = u.process(tenant, env, p['id'])
                if intermediate['state'] == 'unknown':break
            check('Actual missing bank response remains unknown: ' + fault, intermediate['state'] == 'unknown')
            p = drain(p)
            check('Actual missing bank response recovers original financial effects: ' + fault, p['state'] == 'succeeded' and balances()[accounts[0]] == before[accounts[0]] - 100 and balances()[accounts[1]] == before[accounts[1]] + 100)
        before = balances();p = submit('beneficiary-rejection', 100, financial_fault='reject_credit');authorize(p);p = drain(p)
        check('Beneficiary rejection reverses the original debit', p['state'] == 'reversed' and balances() == before and {x['kind'] for x in p['operation']['legs']} == {'debit', 'credit', 'reversal'})
        before = balances();p = submit('insufficient', 100_000_000);authorize(p);p = drain(p)
        check('Insufficient funds do not create a beneficiary credit', p['state'] == 'declined' and balances() == before and all(x['kind'] != 'credit.request' for x in p['messages']))
        psp = actor(banks[0]['id'], 'payer_psp')['id']
        u.participant_action(tenant, env, psp, 'pause', {})
        p = submit('paused-actor', 100);before = balances();authorize(p)
        check('Paused participant prevents financial routing', u.process(tenant, env, p['id'])['waiting'] == 'participant_paused' and balances() == before)
        u.participant_action(tenant, env, psp, 'resume', {})
        check('Participant recovery resumes its accepted intent', drain(p)['state'] == 'succeeded')
        refund = u.refund(tenant, env, payment['id'], {'request_key': 'partial-refund', 'amount_paise': 4000, 'device_id': devices[1]['id']})
        authorize(refund, 1);before = balances();refund = drain(refund)
        check('Refund traverses reversed participant roles and returns original funds', refund['state'] == 'succeeded' and refund['operation']['parent_id'] == result['operation_id'] and balances()[accounts[0]] == before[accounts[0]] + 4000)
        excess = u.refund(tenant, env, payment['id'], {'request_key': 'excess-refund', 'amount_paise': 7000, 'device_id': devices[1]['id']})
        denied('Refund authorization cannot exceed remaining original amount', lambda: authorize(excess, 1), 'ADJUSTMENT_LIMIT')
        body = {'payer_id': accounts[0], 'payee_id': accounts[1], 'amount_paise': 100, 'device_id': devices[0]['id'], 'request_key': 'concurrent'}
        with ThreadPoolExecutor(8) as pool:
            accepted = list(pool.map(lambda _: u.submit(tenant, env, body), range(8)))
        check('Concurrent submissions retain one payment identity', len({x['id'] for x in accepted}) == 1)
        authorize(accepted[0]);before = balances()
        def concurrent(_):
            try:return u.process(tenant, env, accepted[0]['id'])
            except BankingBusy:return {'busy': True}
        with ThreadPoolExecutor(8) as pool:list(pool.map(concurrent, range(8)))
        p = drain(accepted[0])
        check('Concurrent deliveries retain one financial effect per leg', p['state'] == 'succeeded' and balances()[accounts[0]] == before[accounts[0]] - 100)
        p = submit('key-rotation', 100);authorize(p)
        with app.store.reader() as db:old_key = db.execute('SELECT key_id FROM upi_envelopes WHERE payment_id=?', (p['id'],)).fetchone()[0]
        u.participant_action(tenant, env, psp, 'rotate-key', {})
        check('Overlapping old verification key remains usable after rotation', drain(p)['state'] == 'succeeded')
        p = submit('revoked-key', 100);authorize(p)
        with app.store.reader() as db:revoked_key = db.execute('SELECT key_id FROM upi_envelopes WHERE payment_id=?', (p['id'],)).fetchone()[0]
        u.participant_action(tenant, env, psp, 'rotate-key', {})
        u.participant_action(tenant, env, psp, 'revoke-key', {'key_id': revoked_key})
        before = balances();u.process(tenant, env, p['id'])
        check('Revoked sender key blocks unaccepted financial dispatch', u.read(tenant, env, p['id'])['state'] == 'unknown' and balances() == before)
        u.reconcile(tenant, env, p['id'], {})
        p = drain(p)
        check('Status recovery uses a fresh valid transport key and original financial identity', p['state'] == 'succeeded' and len([x for x in p['messages'] if x['step'] == 1]) == 2 and balances()[accounts[0]] == before[accounts[0]] - 100)
        epoch = time.time()
        with app.store.transaction() as db:
            db.execute('INSERT INTO simulation_clocks(environment_id,tenant_id,epoch) VALUES(?,?,?)', (env, tenant, epoch))
        def advance(seconds):
            with app.store.transaction() as db:db.execute('UPDATE simulation_clocks SET epoch=epoch+? WHERE environment_id=?', (seconds, env))
        expiring = submit('unapproved-expiry', 100, expires_in_seconds=1)
        before = balances();advance(2);u.expire(tenant, env, expiring['id'])
        expired = u.read(tenant, env, expiring['id'])
        check('Unapproved intent expires with retained proof and no financial dispatch', expired['state']=='expired' and expired['operation'] is None and expired['evidence']['valid'] and balances()==before)
        p = submit('approval-expiry-before-debit', 100, expires_in_seconds=1);authorize(p);advance(2)
        check('Expired approval prevents the first financial debit', drain(p)['state']=='declined' and balances()==before)
        p = submit('recover-beyond-approval-expiry', 100, expires_in_seconds=1, financial_fault='lost_debit_response');authorize(p)
        for _ in range(3):
            uncertain=u.process(tenant,env,p['id'])
            if uncertain['state']=='unknown':break
        advance(2);p=drain(p)
        check('Approval expiry does not discard an already attempted financial effect',p['state']=='succeeded' and balances()[accounts[0]]==before[accounts[0]]-100)
        p = submit('message-expiry-recovery',100);authorize(p);advance(301)
        u.process(tenant,env,p['id'])
        check('Expired participant envelope does not dispatch a debit',u.read(tenant,env,p['id'])['state']=='unknown' and balances()[accounts[0]]==before[accounts[0]]-100)
        u.reconcile(tenant,env,p['id'],{});p=drain(p)
        check('Transport renewal preserves an expired unattempted approval as a decline',p['state']=='declined' and all(l['state']!='confirmed' for l in p['operation']['legs']))
        p = submit('process-kill', 250);authorize(p);before = balances()
        app.close();app = None
        killed = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), '--crash-child', str(directory), env, p['id']], cwd=ROOT, capture_output=True, timeout=15)
        check('Worker was actually killed after committed beneficiary effect', killed.returncode == -signal.SIGKILL, killed.returncode)
        app = App(directory, control_dsn=dsn);u, b = app.upi, app.banking
        check('Financial effect survives the killed worker before recipient receipt', balances()[accounts[0]] == before[accounts[0]] - 250 and balances()[accounts[1]] == before[accounts[1]] + 250)
        with app.store.transaction() as db:
            db.execute('UPDATE upi_payments SET lease_until=0 WHERE id=?', (p['id'],))
        p = drain(p)
        check('New application recovers killed delivery without another debit or credit', p['state'] == 'succeeded' and p['evidence']['valid'] and balances()[accounts[0]] == before[accounts[0]] - 250 and balances()[accounts[1]] == before[accounts[1]] + 250)
        immutable_message = p['messages'][0]['id']
        for table, column, identity in [('participant_connections', 'name', revision['id']), ('participant_connection_checks', 'manifest_hash', receipt['id']), ('upi_payments', 'adapter_bindings_json', p['id'])]:
            try:
                with app.store.transaction() as db:db.execute('UPDATE '+table+' SET '+column+'=? WHERE id=?', ('changed', identity))
            except Exception:check('Stored connection boundary rejects mutation: '+table, True)
            else:check('Stored connection boundary rejects mutation: '+table, False)
        try:
            with app.store.transaction() as db:db.execute("UPDATE upi_envelopes SET signature='changed' WHERE id=?", (immutable_message,))
        except Exception:
            check('Stored participant envelopes reject mutation', True)
        else:check('Stored participant envelopes reject mutation', False)
        try:
            with app.store.transaction() as db:db.execute("UPDATE upi_payments SET state='unknown' WHERE id=?", (p['id'],))
        except Exception:
            check('Sealed evidence rejects state mutation', True)
        else:check('Sealed evidence rejects state mutation', False)
        u.device_action(tenant, env, devices[0]['id'], 'revoke', {})
        denied('Revoked device cannot authorize new payments', lambda: submit('revoked-device'), 'ACCOUNT_NOT_LINKED')
        from avenra.simulation import example_upi_plan
        lab=app.simulation;profile=lab.profile(tenant,env,{})
        definition=lab.publish(tenant,env,{'name':'Complete routed UPI journey','profile_id':profile['id'],'plan':example_upi_plan()})
        run=lab.create_run(tenant,env,{'plan_id':definition['id'],'request_key':'participant-journey'})
        journey=lab.execute(tenant,env,run['id'])
        check('Full 21-step routed collect/recovery/refund/settlement/expiry scenario passes',journey['state']=='passed' and journey['cursor']==21,journey.get('error'))
        check('Journey binds its participant contract and protects device proofs',journey['evidence']['manifest']['participant_profile_hash']==PROFILE_HASH and 'enrolment_challenge' not in canonical(journey) and 'device_proof' not in canonical(journey))
        import copy
        wrong=copy.deepcopy(example_upi_plan());wrong['steps'][7]['expect']={'state':'declined'}
        definition=lab.publish(tenant,env,{'name':'Deliberately wrong participant oracle','profile_id':profile['id'],'plan':wrong})
        run=lab.create_run(tenant,env,{'plan_id':definition['id'],'request_key':'wrong-participant-oracle'})
        failed=lab.execute(tenant,env,run['id'])
        check('Incorrect participant expectation produces retained failed evidence',failed['state']=='failed' and failed['evidence']['valid'])
        check('Complete audit chain verifies after recovery and denials', app.store.verify_audit()['valid'])
        report.update(passed=True, environment_id=env, payment_id=p['id'], crash_lease_scope='Real SIGKILL; test lease advanced to expiry inside the isolated test store.')
    except Exception as failure:
        report.update(error=str(failure), traceback=traceback.format_exc())
    finally:
        if app:app.close()
    report['application_fingerprint'] = application_fingerprint()
    content = json.dumps(report, indent=2) + '\n'
    destination = ROOT / '.local' / ('upi-postgres-verification.json' if postgres else 'upi-verification.json')
    destination.write_text(content);destination.chmod(0o600)
    attempts = ROOT / '.local/verification-attempts';attempts.mkdir(exist_ok=True)
    (attempts / ('upi-' + ('postgres-' if postgres else '') + str(time.time_ns()) + '.json')).write_text(content)
    print(json.dumps({'passed': report['passed'], 'checks': len(report['checks']), 'report': str(destination), 'error': report.get('error')}))
    if not report['passed']:print(report.get('traceback', ''))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--crash-child':
        crash_child(*sys.argv[2:])
    else:
        raise SystemExit(main())
