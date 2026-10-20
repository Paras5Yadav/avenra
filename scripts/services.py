#!/usr/bin/env python3
"""Manage only Avenra's optional local service project and private credentials."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / '.local'


def docker(*arguments):
    config = LOCAL / 'docker-client'
    config.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not (config / 'config.json').exists():
        (config / 'config.json').write_text(json.dumps({'auths': {}, 'cliPluginsExtraDirs': ['/opt/homebrew/lib/docker/cli-plugins']}))
    host = os.environ.get('AVENRA_DOCKER_HOST', 'unix://' + str(Path.home() / '.colima/avenra/docker.sock'))
    return ['docker', '--config', str(config), '--host', host, *arguments]


def prepare():
    LOCAL.mkdir(exist_ok=True, mode=0o700)
    env = LOCAL / 'services.env'
    if not env.exists():
        with env.open('x') as target:
            env.chmod(0o600)
            for name in ['AVENRA_CONTRACT_DB_PASSWORD', 'AVENRA_PRODUCT_DB_PASSWORD', 'AVENRA_PRODUCT_MASTER_PASSWORD']:
                target.write(name + '=' + secrets.token_hex(32) + '\n')
    aliases = [('quay.io/microcks/microcks:1.15.0', 'avenra/api-contracts:1.15.0'), ('mongo:8.0', 'avenra/contract-storage:8'), ('postgres:16', 'avenra/product-storage:16')]
    receipts = []
    for original, alias in aliases:
        check = subprocess.run(docker('image', 'inspect', original), capture_output=True)
        if check.returncode:
            subprocess.run(docker('pull', original), check=True)
        subprocess.run(docker('tag', original, alias), check=True)
        value = json.loads(subprocess.check_output(docker('image', 'inspect', original)))[0]
        receipts.append({'original': original, 'local_alias': alias, 'image_id': value['Id'], 'repo_digests': value.get('RepoDigests', []), 'architecture': value['Architecture']})
    (ROOT / 'LICENSES/deployment-images.json').write_text(json.dumps({'scope': 'Local service artifacts; not a production compatibility qualification', 'images': receipts}, indent=2) + '\n')
    print('Private service configuration and pinned image receipts prepared.')


def bind():
    data = LOCAL / 'data'
    config = data / 'services.json'
    existing = json.loads(config.read_text()) if config.exists() else {}
    existing.setdefault('services', {})['api_contracts'] = {'origin': 'http://127.0.0.1:4840', 'tenant_bindings': {'tenant_avenra': 'local'}}
    existing.setdefault('callback_origins', ['http://127.0.0.1:4888'])
    with config.open('w') as target:
        config.chmod(0o600)
        json.dump(existing, target, indent=2)
    print('Local provider-contract binding configured.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'start-contracts', 'start-products', 'status', 'stop'])
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare()
    else:
        command = docker('compose', '--env-file', str(LOCAL / 'services.env'), '-f', str(ROOT / 'deploy/compose.local.yaml'))
        if args.action == 'start-contracts':
            subprocess.run(command + ['up', '-d', 'contract-db', 'contracts'], check=True)
            bind()
        elif args.action == 'start-products':
            subprocess.run(command + ['up', '-d', 'product-db'], check=True)
            for _ in range(30):
                ready = subprocess.run(docker('exec', 'avenra-services-product-db-1', 'pg_isready', '-U', 'avenra'), capture_output=True)
                if ready.returncode == 0:
                    break
                time.sleep(1)
            else:
                raise SystemExit('Product database did not become ready.')
            exists = subprocess.check_output(docker('exec', 'avenra-services-product-db-1', 'psql', '-U', 'avenra', '-d', 'postgres', '-Atc', "SELECT 1 FROM pg_database WHERE datname='fineract_default'"), text=True).strip()
            if exists != '1':
                subprocess.run(docker('exec', 'avenra-services-product-db-1', 'createdb', '-U', 'avenra', 'fineract_default'), check=True)
            subprocess.run(command + ['up', '-d', 'banking-core'], check=True)
        elif args.action == 'status':
            subprocess.run(command + ['ps'], check=True)
        else:
            subprocess.run(command + ['stop'], check=True)
