#!/usr/bin/env python3
"""Exercise the institution lab through the actual scoped HTTP API/worker."""
import copy
import hashlib
import hmac
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from avenra.simulation import example_plan
from avenra.util import application_fingerprint, digest, uid


def main():
    credentials = json.loads((ROOT / '.local/data/credentials.json').read_text())
    tenant, token = credentials['tenant_id'], credentials['api_key']
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    report = {'passed': False, 'checks': [], 'scope': 'Real scoped HTTP, worker, PostgreSQL and accounting; synthetic lab contract only.'}
    def check(name, condition, detail=None):
        report['checks'].append({'name': name, 'passed': bool(condition), 'detail': detail})
        if not condition:
            raise AssertionError(name)
    def api(path, body=None, expected=200, auth=True):
        headers = {'Content-Type': 'application/json'}
        if auth:
            headers['Authorization'] = 'Bearer ' + token
        request = urllib.request.Request('http://127.0.0.1:4827' + path, data=json.dumps(body, allow_nan=False).encode() if body is not None else None, headers=headers)
        try:
            with opener.open(request, timeout=15) as response:
                status, value = response.status, json.load(response)
        except urllib.error.HTTPError as error:
            with error:
                status, value = error.code, json.load(error)
        if status != expected:
            raise AssertionError(f'Unexpected HTTP {status} at {path}: {value}')
        return value
    try:
        env = api(f'/api/tenants/{tenant}/environments', {'name': 'Institution HTTP ' + uid('qa'), 'fixture_profile': 'banking'}, 202)['id']
        source = f'/api/tenants/{tenant}/environments/{env}'
        for _ in range(100):
            try:
                overview = api(source + '/overview')
                if overview['environment']['status'] == 'active':
                    break
            except AssertionError:
                pass
            time.sleep(.1)
        else:
            raise TimeoutError('Fixture environment did not provision.')
        base = source + '/banking/simulation'
        lab = api(base)
        check('Actual HTTP exposes reviewed operations and explicit external gaps', len(lab['operations']) >= 20 and all(x['state'] != 'ready' for x in lab['official_profiles']))
        api(base, expected=401, auth=False)
        check('Unauthenticated institution lab access is rejected', True)
        api(base.replace(tenant, 'tenant_integration'), expected=403)
        check('Workspace API key cannot read another workspace lab', True)
        profile = api(base + '/profiles', {}, 202)
        check('Immutable profile installs through actual HTTP', profile['document_hash'] == digest(lab['default_profile']))
        api(base + '/profiles', {'document': {'authority': 'official'}}, 409)
        check('Unimplemented official profiles cannot be presented as active', True)
        plan_body = {'profile_id': profile['id'], 'name': 'HTTP recovery, refund and expiry', 'plan': example_plan()}
        definition = api(base + '/plans', plan_body, 202)
        check('Versioned multistep plan is persisted', definition['version'] == 1 and definition['plan_hash'] == digest(example_plan()))
        run_body = {'plan_id': definition['id'], 'request_key': uid('http-case')}
        run = api(base + '/runs', run_body, 202)
        check('Run HTTP replay retains its original identity', api(base + '/runs', run_body, 202)['id'] == run['id'])
        for _ in range(250):
            observed = api(base + '/runs/' + run['id'])
            if observed['state'] in {'passed', 'failed'}:
                break
            time.sleep(.1)
        else:
            raise TimeoutError('Actual worker did not finish the experiment.')
        check('Actual durable worker completes all 15 steps', observed['state'] == 'passed' and observed['cursor'] == 15, observed.get('error'))
        evidence = api(base + '/runs/' + run['id'] + '/evidence')
        key = (ROOT / '.local/data/evidence.key').read_bytes()
        fingerprint = digest(evidence['manifest'])
        check('Downloaded HTTP evidence independently verifies', evidence['valid'] and fingerprint == evidence['manifest_hash'] and hmac.compare_digest(hmac.new(key, fingerprint.encode(), hashlib.sha256).hexdigest(), evidence['signature']))
        check('Evidence identifies the actual loaded application', evidence['manifest']['application_fingerprint'] == application_fingerprint())
        check('Experiment evidence explicitly excludes official certification', evidence['manifest']['official_conformance'] is False)
        api(base + '/runs/' + run['id'] + '/resume', {}, 409)
        check('Sealed experiments cannot be resumed or overwritten', True)
        api(source + '/banking/simulation/messages/sign', {'profile_id': profile['id'], 'sender': 'payer_psp', 'recipient': 'network', 'kind': 'synthetic.request', 'message_key': 'signed-http', 'payload': {}}, 200)
        check('Scoped lab message signing endpoint works', True)
        foreign = f'/api/tenants/{tenant}/environments/' + run['fixture_environment_id'] + '/banking/simulation'
        api(foreign + '/runs/' + run['id'], expected=404)
        check('Run identity cannot cross its source environment', True)
        api(foreign + '/plans', plan_body, 404)
        check('Profile identity cannot cross environments', True)
        invalid = copy.deepcopy(plan_body); invalid['plan']['steps'][0]['operation'] = 'host.execute'
        api(base + '/plans', invalid, 400)
        check('Arbitrary host operations are rejected before execution', True)
        report['run_id'], report['environment_id'] = run['id'], env
        report['passed'] = True
    except Exception as failure:
        import traceback
        report['error'], report['traceback'] = str(failure), traceback.format_exc()
    report['application_fingerprint'] = application_fingerprint()
    target = ROOT / '.local/simulation-http-verification.json'
    content = json.dumps(report, indent=2) + '\n';target.write_text(content)
    attempts = ROOT / '.local/verification-attempts';attempts.mkdir(exist_ok=True)
    (attempts / ('simulation-http-' + str(time.time_ns()) + '.json')).write_text(content)
    print(json.dumps({'passed': report['passed'], 'checks': len(report['checks']), 'report': str(target), 'error': report.get('error')}))
    if not report['passed']:
        print(report.get('traceback', ''))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
