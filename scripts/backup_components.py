#!/usr/bin/env python3
"""Snapshot owned service volumes and compare restored financial/monitoring records."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from services import docker


def main():
    report = {'passed': False, 'checks': [], 'scope': 'Offline service-volume export and isolated database restores. Message-broker recovery and full institutional DR/SLO qualification remain separate.'}
    folder = ROOT / '.local/backups' / ('services-' + str(time.time_ns()))
    folder.mkdir(parents=True, mode=0o700)
    projects = {'products': docker('compose', '--env-file', str(ROOT / '.local/services.env'), '-f', str(ROOT / 'deploy/compose.local.yaml')), 'monitoring': docker('compose', '-f', str(ROOT / '.local/deploy/monitoring.json'))}
    restored, volumes = [], []
    def run(arguments, **kwargs):
        return subprocess.run(arguments, check=True, capture_output=True, **kwargs)
    def check(name, value):
        report['checks'].append({'name': name, 'passed': bool(value)})
        if not value:
            raise AssertionError(name)
    def pg_records(container, user, targets):
        result = {}
        for database, tables in targets.items():
            for table in tables:
                query = 'SELECT row_to_json(t)::text FROM ' + table + ' t ORDER BY row_to_json(t)::text'
                raw = run(docker('exec', container, 'psql', '-v', 'ON_ERROR_STOP=1', '-U', user, '-d', database, '-Atc', query)).stdout
                result[database + '/' + table] = hashlib.sha256(raw).hexdigest()
        return result
    mongo_script = "const a=db.getSiblingDB('admin'); if(!a.auth('avenra',process.env.AVENRA_CONTRACT_DB_PASSWORD||process.env.MONGO_INITDB_ROOT_PASSWORD))quit(4); const d=db.getSiblingDB('avenra_contracts'); const x={}; d.getCollectionNames().sort().forEach(n=>x[n]=d.getCollection(n).find().sort({_id:1}).toArray()); print(EJSON.stringify(x));"
    def mongo_records(container):
        return hashlib.sha256(run(docker('exec', '--env-file', str(ROOT / '.local/services.env'), container, 'mongosh', '--quiet', '--eval', mongo_script)).stdout).hexdigest()
    def start_restored(name, image, volume, target, command=None):
        container = 'avenra-restore-' + name + '-' + str(time.time_ns())
        arguments = docker('run', '-d', '--name', container, '--network', 'none', '--env-file', str(ROOT / '.local/services.env'), '-v', volume + ':' + target, image, *(command or []))
        run(arguments);restored.append(container)
        return container
    def wait_ready(container, command):
        for _ in range(120):
            result = subprocess.run(docker('exec', container, *command), capture_output=True)
            if result.returncode == 0:
                return
            time.sleep(.5)
        raise TimeoutError('Restored service did not become ready: ' + container)
    subprocess.run([sys.executable, str(ROOT / 'scripts/local.py'), 'stop-console'], check=True, stdout=subprocess.DEVNULL)
    try:
        native = {}
        for project, service in [('products', 'product-db'), ('products', 'contract-db'), ('monitoring', 'core-postgres'), ('monitoring', 'nats')]:
            identity = run(projects[project] + ['ps', '-q', service], text=True).stdout.strip()
            if not identity:
                raise RuntimeError('Required service is not running: ' + service)
            native[service] = identity
        # Stop ingress and processors first; databases remain available for a
        # baseline after application writers have drained.
        run(projects['products'] + ['stop', 'contracts', 'banking-core'])
        monitor_config = json.loads((ROOT / '.local/deploy/monitoring.json').read_text())
        processors = [x for x in monitor_config['services'] if x not in {'core-postgres', 'valkey', 'nats'}]
        run(projects['monitoring'] + ['stop', *processors])
        product_targets = {'fineract_default': ['m_client', 'm_loan', 'm_loan_transaction', 'm_savings_account', 'm_savings_account_transaction']}
        monitor_targets = {'configuration': ['network_map', 'rule', 'typology'], 'raw_history': ['pacs008', 'pacs002'], 'evaluation': ['evaluation'], 'event_history': ['account', 'entity', 'account_holder', 'transaction']}
        expected_product = pg_records(native['product-db'], 'avenra', product_targets)
        expected_monitor = pg_records(native['core-postgres'], 'postgres', monitor_targets)
        expected_contracts = mongo_records(native['contract-db'])
        run(projects['products'] + ['stop'])
        run(projects['monitoring'] + ['stop'])
        source_volumes = {}
        targets = {'product-db': '/var/lib/postgresql/data', 'contract-db': '/data/db', 'core-postgres': '/var/lib/postgresql', 'nats': '/data'}
        for name, identity in native.items():
            metadata = json.loads(run(docker('inspect', identity), text=True).stdout)[0]
            source_volumes[name] = next(x['Name'] for x in metadata['Mounts'] if x['Type'] == 'volume' and x['Destination'] == targets[name])
        archives = {}
        for name, volume in source_volumes.items():
            archive = folder / (name + '.tar.gz')
            with archive.open('wb') as output:
                subprocess.run(docker('run', '--rm', '--network', 'none', '--read-only', '-v', volume + ':/snapshot:ro', 'avenra/product-storage:16', 'tar', '-C', '/snapshot', '-czf', '-', '.'), check=True, stdout=output, stderr=subprocess.PIPE)
            archive.chmod(0o600)
            with tarfile.open(archive) as bundle:
                members = bundle.getmembers()
                check(name + ' snapshot contains only relative archive paths', bool(members) and all(not Path(x.name).is_absolute() and '..' not in Path(x.name).parts for x in members))
            with archive.open('rb') as source:
                archives[name] = {'sha256': hashlib.file_digest(source, 'sha256').hexdigest(), 'bytes': archive.stat().st_size}
            if name == 'nats':
                continue
            clone = 'avenra_restore_' + name.replace('-', '_') + '_' + str(time.time_ns())
            run(docker('volume', 'create', clone));volumes.append(clone)
            with archive.open('rb') as source:
                subprocess.run(docker('run', '--rm', '--network', 'none', '--read-only', '-i', '-v', clone + ':/restore', 'avenra/product-storage:16', 'tar', '-C', '/restore', '-xzf', '-'), check=True, stdin=source, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            if name == 'product-db':
                container = start_restored(name, 'avenra/product-storage:16', clone, targets[name])
                wait_ready(container, ['pg_isready', '-U', 'avenra'])
                check('Restored product database preserves every selected client, loan and savings record', pg_records(container, 'avenra', product_targets) == expected_product)
            elif name == 'core-postgres':
                container = start_restored(name, 'avenra/monitor-core-postgres:4', clone, targets[name])
                wait_ready(container, ['pg_isready', '-U', 'postgres'])
                check('Restored monitoring database preserves configuration, histories and evaluations', pg_records(container, 'postgres', monitor_targets) == expected_monitor)
            else:
                container = start_restored(name, 'avenra/contract-storage:8', clone, targets[name], ['mongod', '--auth', '--bind_ip', '127.0.0.1'])
                wait_ready(container, ['mongosh', '--quiet', '--eval', 'db.adminCommand({ping:1})'])
                check('Restored provider database preserves all contract collections', mongo_records(container) == expected_contracts)
        shutil.copyfile(ROOT / '.local/services.env', folder / 'services.env.private');(folder / 'services.env.private').chmod(0o600)
        for name, source in [('service-bindings.json.private', ROOT / '.local/data/services.json'), ('monitoring-deployment.json.private', ROOT / '.local/deploy/monitoring.json'), ('product-identity.json.private', ROOT / '.local/data/product-credentials.json'), ('monitoring-db.secret', ROOT / '.local/monitoring-db.secret'), ('monitoring-reader.secret', ROOT / '.local/monitoring-reader.secret')]:
            if source.exists():
                shutil.copyfile(source, folder / name);(folder / name).chmod(0o600)
        report.update(passed=True, artifacts=archives)
    except Exception as e:
        import traceback
        report.update(error=type(e).__name__ + ': ' + str(e), traceback=traceback.format_exc())
    finally:
        for container in restored:
            (folder / (container + '.log')).write_bytes(subprocess.run(docker('logs', '--tail', '60', container), capture_output=True).stdout)
            subprocess.run(docker('rm', '-f', container), capture_output=True)
        if report['passed']:
            for volume in volumes:
                subprocess.run(docker('volume', 'rm', volume), capture_output=True)
        else:
            report['retained_restore_volumes'] = volumes
        for command in projects.values():
            subprocess.run(command + ['up', '-d'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run([sys.executable, str(ROOT / 'scripts/local.py'), 'start'], check=True, stdout=subprocess.DEVNULL)
    report['directory'] = str(folder)
    content = json.dumps(report, indent=2) + '\n'
    (folder / 'verification.json').write_text(content)
    (ROOT / '.local/component-backup-verification.json').write_text(content)
    print(json.dumps({'passed': report['passed'], 'checks': len(report['checks']), 'directory': str(folder), 'error': report.get('error')}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
