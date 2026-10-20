"""Owned India lab assertions over the adapted engines; no certification claim."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
from pathlib import Path

from .app import PROFILE
from .errors import DomainError
from .util import canonical, digest, now, uid


def step(app, run_id, key, name, expected, observed, passed):
    with app.store.transaction() as db:
        db.execute('INSERT OR IGNORE INTO run_steps VALUES(?,?,?,?,?,?,?,?)',
                   (uid('step'), run_id, key, name, canonical(expected), canonical(observed), int(passed), now()))
    if not passed:
        raise AssertionError(name)


def execute(app, tenant, run_id):
    run = app.run(tenant, run_id)
    from .poc_scenarios import CATALOG as poc_cases,execute as execute_poc
    if run['scenario_key'] in {case['key'] for case in poc_cases}:
        return execute_poc(app,tenant,run_id)
    if run['state'] in {'passed', 'failed'}:
        return
    environment, scenario = run['environment_id'], run['scenario_key']
    with app.store.transaction() as db:
        db.execute("UPDATE runs SET state='running',started_at=COALESCE(started_at,?) WHERE id=?", (now(), run_id))
    failure = None
    try:
        app.provision(tenant, environment)
        accounts = {a['role']: a for a in app.accounts(tenant, environment)}
        env = app.environment(tenant, environment, True)
        amount = 10_000_000 if scenario == 'insufficient_funds' else 25_000
        body = {'payer_id': accounts['payer']['id'], 'payee_id': accounts['payee']['id'], 'amount_paise': amount,
                'request_key': run_id + ':payment', 'fault': 'lost_response' if scenario == 'lost_response' else 'none'}
        step(app, run_id, '01-isolation', 'Dedicated run ledger is ready', {'kind': 'simulation', 'purpose': 'run'},
             {'kind': env['kind'], 'purpose': env['purpose'], 'environment_id': environment}, env['kind'] == 'simulation' and env['purpose'] == 'run')
        if scenario == 'concurrent_replay':
            with ThreadPoolExecutor(max_workers=8) as pool:
                submitted = list(pool.map(lambda _: app.submit_payment(tenant, environment, body), range(8)))
            identities = sorted({p['id'] for p in submitted})
            step(app, run_id, '02-acceptance', 'Eight concurrent submissions resolve to one intent', {'identities': 1},
                 {'identities': len(identities), 'submissions': 8}, len(identities) == 1)
            payment_id = identities[0]
            with ThreadPoolExecutor(max_workers=4) as pool:
                settled = list(pool.map(lambda _: app.process_payment(tenant, environment, payment_id), range(4)))
            ids = {p['ledger_transaction_id'] for p in settled}
            step(app, run_id, '03-concurrent-effect', 'Concurrent effects retain one ledger identity', {'ledger_identities': 1},
                 {'ledger_identities': len(ids), 'attempts': 4}, len(ids) == 1 and None not in ids)
            payment = app.payment(tenant, environment, payment_id)
        else:
            submitted = app.submit_payment(tenant, environment, body)
            payment_id = submitted['id']
            payment = app.process_payment(tenant, environment, payment_id)
        if scenario == 'idempotency':
            replayed = app.submit_payment(tenant, environment, body)
            app.process_payment(tenant, environment, replayed['id'])
            step(app, run_id, '02-replay', 'Identical payload returns the original intent', {'same_payment_id': True},
                 {'same_payment_id': replayed['id'] == payment_id, 'replayed': replayed['replayed']}, replayed['id'] == payment_id and replayed['replayed'])
            try:
                app.submit_payment(tenant, environment, {**body, 'amount_paise': amount + 1})
                conflict = None
            except DomainError as error:
                conflict = error.code
            step(app, run_id, '03-conflict', 'Changed payload is rejected', {'code': 'IDEMPOTENCY_CONFLICT'},
                 {'code': conflict}, conflict == 'IDEMPOTENCY_CONFLICT')
        if scenario == 'lost_response':
            with app.store.reader() as db:
                historical = db.execute("SELECT observed_json FROM run_steps WHERE run_id=? AND step_key='02-unknown'", (run_id,)).fetchone()
            observation = json.loads(historical[0]) if historical else {'state': payment['state'], 'fault_applied': bool(payment['fault_applied'])}
            step(app, run_id, '02-unknown', 'Dropped response leaves the outcome unconfirmed', {'state': 'unknown', 'fault_applied': True},
                 observation, observation == {'state': 'unknown', 'fault_applied': True})
            committed = app.accounting.transactions(env['ledger_name'], 'payment:' + payment_id)
            step(app, run_id, '03-commit', 'Ledger committed despite response loss', {'matching_transactions': 1},
                 {'matching_transactions': len(committed)}, len(committed) == 1)
            payment = app.process_payment(tenant, environment, payment_id)
            step(app, run_id, '04-recovery', 'Reconciliation recovers the original transaction', {'state': 'succeeded', 'original_transaction': True},
                 {'state': payment['state'], 'original_transaction': payment['ledger_transaction_id'] == str(committed[0]['id'])},
                 payment['state'] == 'succeeded' and payment['ledger_transaction_id'] == str(committed[0]['id']))
        expected_state = 'declined' if scenario == 'insufficient_funds' else 'succeeded'
        payment = app.payment(tenant, environment, payment_id)
        step(app, run_id, '05-state', 'Final payment state matches the expected outcome', {'state': expected_state},
             {'state': payment['state'], 'ledger_transaction_id': payment['ledger_transaction_id']}, payment['state'] == expected_state)
        matching = app.accounting.transactions(env['ledger_name'], 'payment:' + payment_id)
        expected_count = 0 if scenario == 'insufficient_funds' else 1
        step(app, run_id, '06-effects', 'Authoritative transaction count is correct', {'matching_transactions': expected_count},
             {'matching_transactions': len(matching), 'ids': [str(t['id']) for t in matching]}, len(matching) == expected_count)
        observed_balances = {a['role']: a['balance_paise'] for a in app.accounts(tenant, environment)}
        delta = 0 if scenario == 'insufficient_funds' else 25_000
        expected_balances = {'payer': 5_000_000 - delta, 'payee': 250_000 + delta, 'treasury': 100_000_000}
        step(app, run_id, '07-balances', 'Real account balances reconcile in integer paise', expected_balances,
             observed_balances, observed_balances == expected_balances)
        if scenario == 'duplicate_callback':
            with app.store.reader() as db:
                event = db.execute('SELECT id FROM callback_events WHERE payment_id=?', (payment_id,)).fetchone()[0]
            for _ in range(3):
                app.deliver_callback(tenant, environment, event)
            with app.store.reader() as db:
                counts = {r['result']: r['n'] for r in db.execute('SELECT result,COUNT(*) n FROM callback_attempts WHERE event_id=? GROUP BY result', (event,))}
            step(app, run_id, '08-notification', 'Local sink accepts one notification and deduplicates retries', {'accepted': 1, 'minimum_duplicates': 2},
                 counts, counts.get('accepted') == 1 and counts.get('duplicate', 0) >= 2)
    except Exception as error:
        failure = str(error)[:300] if isinstance(error, (AssertionError, DomainError)) else 'Scenario could not complete: ' + type(error).__name__
        with app.store.transaction() as db:
            db.execute('INSERT OR IGNORE INTO run_steps VALUES(?,?,?,?,?,?,?,?)',
                       (uid('step'), run_id, '99-completion', 'Scenario completed without an execution error', canonical({'completed': True}), canonical({'error': failure}), 0, now()))
    finalize(app, tenant, run_id, failure)


def finalize(app, tenant, run_id, failure):
    root = Path(__file__).resolve().parents[1]
    artifacts = {}
    for role in ('accounting', 'fault'):
        binary = root / '.local/bin' / role
        if binary.is_file():
            artifacts[role] = {'binary_file_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
                               'scope': 'Local build file digest; not worker or runtime attestation.'}
    with app.store.transaction() as db:
        if db.execute('SELECT 1 FROM evidence WHERE run_id=?', (run_id,)).fetchone():
            return
        run = dict(db.execute('SELECT * FROM runs WHERE id=? AND tenant_id=?', (run_id, tenant)).fetchone())
        steps = [dict(s) for s in db.execute('SELECT * FROM run_steps WHERE run_id=? ORDER BY step_key', (run_id,))]
        passed = bool(steps) and all(s['passed'] for s in steps) and failure is None
        state, completed = ('passed' if passed else 'failed'), now()
        db.execute('UPDATE runs SET state=?,error=?,completed_at=? WHERE id=?', (state, failure, completed, run_id))
        app.store.audit(db, tenant, run['environment_id'], 'run.' + state, run_id, {'steps': len(steps)})
        head = db.execute('SELECT event_hash FROM audit_events ORDER BY sequence DESC LIMIT 1').fetchone()[0]
        manifest = {'format': 'avenra-evidence/1', 'run_id': run_id, 'tenant_id': tenant, 'environment_id': run['environment_id'],
                    'profile': PROFILE, 'scenario': run['scenario_key'], 'fixture': run['seed'], 'state': state,
                    'created_at': run['created_at'], 'completed_at': completed, 'artifacts': artifacts, 'audit_head': head,
                    'steps': [{'key': s['step_key'], 'name': s['name'], 'expected': json.loads(s['expected_json']),
                               'observed': json.loads(s['observed_json']), 'passed': bool(s['passed'])} for s in steps],
                    'scope': 'Synthetic local financial simulation. No institutional certification or live payment execution.'}
        manifest_hash = digest(manifest)
        signature = hmac.new(app.store.signing_key, manifest_hash.encode(), hashlib.sha256).hexdigest()
        db.execute('INSERT INTO evidence VALUES(?,?,?,?,?)', (run_id, canonical(manifest), manifest_hash, signature, completed))
