#!/usr/bin/env python3
"""Bind the private qualification stack and its restricted recovery reader."""
import json
from pathlib import Path
import secrets
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from services import docker


def main():
    import psycopg
    from psycopg import sql
    config_path = ROOT / '.local/deploy/monitoring.json'
    config = json.loads(config_path.read_text())
    config['services']['core-postgres']['ports'] = ['127.0.0.1:55439:5432']
    config['services']['core-postgres']['networks'] = {'risk': None, 'edge': None}
    config_path.write_text(json.dumps(config, indent=2));config_path.chmod(0o600)
    command = docker('compose', '-f', str(config_path))
    subprocess.run(command + ['up', '-d', 'core-postgres'], check=True)
    for _ in range(40):
        ready = subprocess.run(command + ['exec', '-T', 'core-postgres', 'pg_isready', '-U', 'postgres'], capture_output=True)
        if ready.returncode == 0:
            break
        time.sleep(.5)
    else:
        raise SystemExit('Monitoring database is not ready; binding was not activated.')
    secret = ROOT / '.local/monitoring-reader.secret'
    if not secret.exists():
        secret.write_text(secrets.token_hex(32));secret.chmod(0o600)
    password = secret.read_text().strip()
    statement = sql.SQL("DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='avenra_monitor_read') THEN CREATE ROLE avenra_monitor_read LOGIN; END IF; END $$; ALTER ROLE avenra_monitor_read PASSWORD {}; GRANT CONNECT ON DATABASE raw_history TO avenra_monitor_read; GRANT USAGE ON SCHEMA public TO avenra_monitor_read; GRANT SELECT ON pacs008,pacs002 TO avenra_monitor_read;").format(sql.Literal(password)).as_string()
    subprocess.run(command + ['exec', '-T', 'core-postgres', 'psql', '-v', 'ON_ERROR_STOP=1', '-U', 'postgres', '-d', 'raw_history'], input=statement, text=True, check=True, stdout=subprocess.DEVNULL)
    settings = ROOT / '.local/data/services.json'
    existing = json.loads(settings.read_text())
    tenants = json.loads((ROOT / '.local/data/credentials.json').read_text())['tenant_id']
    from psycopg.conninfo import make_conninfo
    existing.setdefault('services', {})['monitoring'] = {'origin': 'http://127.0.0.1:4842', 'admin_origin': 'http://127.0.0.1:4843', 'classification': 'simulation', 'profile': 'avenra-analysis/1.0', 'tenant_bindings': {tenants: 'DEFAULT'}, 'history_dsn': make_conninfo(host='127.0.0.1', port=55439, dbname='raw_history', user='avenra_monitor_read', password=password)}
    settings.write_text(json.dumps(existing, indent=2));settings.chmod(0o600)
    print('Synthetic monitoring binding and restricted history reader configured.')


if __name__ == '__main__':
    main()
