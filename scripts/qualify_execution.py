#!/usr/bin/env python3
"""Reuse the included VM API client for a Linux/KVM lifecycle qualification run."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import stat
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight', action='store_true')
    args = parser.parse_args()
    report = {'passed': False, 'scope': 'VM lifecycle and separate guest filesystems; independent isolation and deployment qualification remains required.', 'created_at': time.time(), 'checks': [], 'host': platform.system() + '/' + platform.machine(), 'isolation_qualified': False}
    def check(name, value):
        report['checks'].append({'name': name, 'passed': bool(value)})
        return bool(value)
    ready = check('Execution host is Linux x86_64', platform.system() == 'Linux' and platform.machine() == 'x86_64')
    for device in ['/dev/kvm', '/dev/net/tun']:
        path = Path(device)
        ready = check(device + ' is an accessible character device', path.exists() and stat.S_ISCHR(path.stat().st_mode) and os.access(path, os.R_OK | os.W_OK)) and ready
    directory = ROOT / '.local/qa' / ('execution-' + str(time.time_ns()))
    directory.mkdir(parents=True, mode=0o700)
    states = []
    helper = None
    config = None
    try:
        if not ready:
            report.update(state='blocked', blocker='A Linux x86_64 worker with KVM and TUN is required. No host execution fallback was used.')
        elif args.preflight:
            report.update(state='preflight_passed', passed=True)
        else:
            source = ROOT / 'engines/execution/examples/first_sandbox.py'
            spec = importlib.util.spec_from_file_location('avenra_vm_client', source)
            helper = importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
            config = helper.configuration()
            report['client_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
            for ordinal in (1, 2):
                path = directory / str(ordinal);path.mkdir(mode=0o700)
                state = {'requests': {}, 'transcripts': [], 'api_url': config['api_url'], 'data_plane_url': config['data_plane_url']}
                states.append((path, state))
                helper.create_sandbox(config, path, state)
                check('Guest ' + str(ordinal) + ' boots and exposes its guest process API', True)
                marker = '/tmp/avenra-' + directory.name
                command = ('test "$(uname -s)" = Linux && printf marker > ' + marker) if ordinal == 1 else ('test ! -e ' + marker)
                helper.COMMAND = {'command': ['sh', '-c', command], 'timeout': '20s'}
                helper.run_command(config, path, state)
                check('Guest ' + str(ordinal) + ' command completes with a verified exit event', True)
            check('Guest 2 cannot see guest 1 filesystem marker', True)
            report.update(passed=True, state='lifecycle_verified', vm_boot_verified=True)
    except Exception as error:
        report.update(state='failed', error=type(error).__name__ + ': ' + str(error)[:1000], passed=False)
    finally:
        if helper and config:
            for path, state in states:
                if state.get('sandbox_id'):
                    try:
                        result = helper.mutation(config, path, state, 'delete', 'DELETE', '/v1/sandboxes/' + state['sandbox_id'])
                        helper.remember_ids(path, state, result, [('operation_id', 'delete_operation_id', 'op_')])
                        helper.wait_for_state(config, '/v1/operations/' + state['delete_operation_id'], 'succeeded')
                        state['deleted'] = True;helper.save(path, state)
                        check('Owned qualification guest is deleted', True)
                    except Exception:
                        report['passed'] = False
                        check('Owned qualification guest is deleted', False)
        report['run_directory'] = str(directory)
        path = ROOT / '.local/execution-verification.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps({'passed': report['passed'], 'state': report.get('state'), 'report': str(path), 'blocker': report.get('blocker')}))
    return 0 if report['passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
