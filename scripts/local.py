#!/usr/bin/env python3
"""Manage this installation's private local processes."""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / '.local'
STATE = LOCAL / 'processes.json'
DATABASE = Path.home() / '.local/share/avenra/components/database'
PG = Path('/opt/homebrew/opt/postgresql@16/bin')
DSN = 'postgresql://avenra@127.0.0.1:55437/avenra_ledger?sslmode=disable'


def healthy(port, path):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f'http://127.0.0.1:{port}{path}', timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


def process_owned(entry):
    try:
        command = subprocess.check_output(['ps', '-p', str(entry['pid']), '-o', 'command='], text=True).strip()
    except subprocess.CalledProcessError:
        return False
    return entry['marker'] in command


def database():
    if not PG.is_dir():
        raise SystemExit('Install the local database toolchain or configure a qualified alternative before starting.')
    data = DATABASE / 'data'
    DATABASE.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not (data / 'PG_VERSION').exists():
        subprocess.run([str(PG / 'initdb'), '-D', str(data), '-U', 'avenra', '--auth=trust', '--encoding=UTF8', '--no-instructions'], check=True, stdout=subprocess.DEVNULL)
        with (data / 'postgresql.conf').open('a') as handle:
            handle.write("\nlisten_addresses='127.0.0.1'\nport=55437\nmax_connections=40\nshared_buffers='128MB'\n")
    check = subprocess.run([str(PG / 'pg_ctl'), '-D', str(data), 'status'], capture_output=True)
    if check.returncode:
        subprocess.run([str(PG / 'pg_ctl'), '-D', str(data), '-l', str(DATABASE / 'database.log'), 'start'], check=True, stdout=subprocess.DEVNULL)
    if (LOCAL / 'data/database-admin.dsn').exists():
        import psycopg
        with psycopg.connect((LOCAL / 'data/database-admin.dsn').read_text().strip(), autocommit=True) as connection:
            if not connection.execute("SELECT 1 FROM pg_database WHERE datname='avenra_ledger'").fetchone():
                connection.execute('CREATE DATABASE avenra_ledger OWNER avenra_accounting')
        sys.path.insert(0, str(ROOT))
        from avenra.clock_policy import enforce
        enforce(ROOT)
    else:
        exists = subprocess.check_output([str(PG / 'psql'), '-h', '127.0.0.1', '-p', '55437', '-U', 'avenra', '-d', 'postgres', '-Atc', "SELECT 1 FROM pg_database WHERE datname='avenra_ledger'"], text=True).strip()
        if exists != '1':
            subprocess.run([str(PG / 'createdb'), '-h', '127.0.0.1', '-p', '55437', '-U', 'avenra', 'avenra_ledger'], check=True)


def start():
    LOCAL.mkdir(parents=True, exist_ok=True, mode=0o700)
    (LOCAL / 'logs').mkdir(exist_ok=True)
    for role in ('accounting', 'fault'):
        if not (LOCAL / 'bin' / role).exists():
            raise SystemExit('Build the included engines first: python3 scripts/build_engines.py')
    database()
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    services = [
        ('accounting', [str(LOCAL / 'bin/accounting'), 'serve', '--bind', '127.0.0.1:4830',
                        '--auto-upgrade', '--worker', '--postgres-max-open-conns', '12', '--postgres-max-idle-conns', '4'], 4830, '/_/info'),
        ('fault', [str(LOCAL / 'bin/fault'), '-host', '127.0.0.1', '-port', '4831', '-seed', '42'], 4831, '/version'),
        ('console', [str(LOCAL / 'runtime/bin/python') if (LOCAL / 'runtime/bin/python').exists() else sys.executable, '-m', 'avenra.asgi', '--data', str(LOCAL / 'data')], 4827, '/api/health')
    ]
    for name, command, port, endpoint in services:
        if healthy(port, endpoint):
            if name not in state or not process_owned(state[name]):
                raise SystemExit(f'Port {port} is occupied by an untracked service. Preserve it and resolve the conflict before starting.')
            print(name + ': already running')
            continue
        log = (LOCAL / 'logs' / (name + '.log')).open('ab')
        environment = os.environ.copy()
        if name == 'accounting':
            dsn_path = LOCAL / 'data/accounting.dsn'
            if dsn_path.exists() and dsn_path.stat().st_mode & 0o077:
                raise SystemExit('Accounting connection file requires private permissions.')
            environment['POSTGRES_URI'] = dsn_path.read_text().strip() if dsn_path.exists() else DSN
        process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, env=environment)
        log.close()
        state[name] = {'pid': process.pid, 'marker': str(LOCAL / 'data') if name == 'console' else command[0]}
        STATE.write_text(json.dumps(state, indent=2) + '\n')
        STATE.chmod(0o600)
        deadline = time.monotonic() + 40
        while not healthy(port, endpoint):
            if process.poll() is not None or time.monotonic() > deadline:
                raise SystemExit(f'{name} did not become ready. Inspect {LOCAL / "logs" / (name + ".log")}')
            time.sleep(.25)
        print(name + ': ready', flush=True)
    print('Avenra: http://127.0.0.1:4827')


def stop(include_database=False):
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    for name in ('console', 'fault', 'accounting'):
        entry = state.get(name)
        if entry and process_owned(entry):
            os.kill(entry['pid'], signal.SIGTERM)
            deadline = time.monotonic() + 15
            while process_owned(entry) and time.monotonic() < deadline:
                time.sleep(.2)
            if process_owned(entry):
                raise SystemExit(f'{name} is still draining. Inspect its local log before retrying.')
            print(name + ': stopped')
        state.pop(name, None)
    STATE.write_text(json.dumps(state, indent=2) + '\n')
    if include_database and (DATABASE / 'data/PG_VERSION').exists():
        subprocess.run([str(PG / 'pg_ctl'), '-D', str(DATABASE / 'data'), '-m', 'fast', 'stop'], check=True)


def stop_console():
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    entry = state.get('console')
    if entry and process_owned(entry):
        os.kill(entry['pid'], signal.SIGTERM)
        deadline = time.monotonic() + 20
        while process_owned(entry) and time.monotonic() < deadline:
            time.sleep(.2)
        if process_owned(entry):
            raise SystemExit('Console is still draining; inspect its log before retrying.')
    state.pop('console', None)
    STATE.write_text(json.dumps(state, indent=2) + '\n')


def restart_console():
    stop_console()
    start()


if __name__ == '__main__':
    runtime = LOCAL / 'runtime/bin/python'
    if runtime.exists() and Path(sys.executable).absolute() != runtime.absolute():
        os.execv(str(runtime), [str(runtime), *sys.argv])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'stop', 'status', 'restart-console', 'stop-console'])
    parser.add_argument('--database', action='store_true', help='Also stop this installation’s private database.')
    args = parser.parse_args()
    if args.action == 'start':
        start()
    elif args.action == 'stop':
        stop(args.database)
    elif args.action == 'restart-console':
        restart_console()
    elif args.action == 'stop-console':
        stop_console()
    else:
        for name, port, path in [('console',4827,'/api/health'),('accounting',4830,'/_/info'),('fault',4831,'/version')]:
            print(f'{name}: {"ready" if healthy(port,path) else "unavailable"}')
