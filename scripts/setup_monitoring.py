#!/usr/bin/env python3
"""Materialize a private, version-pinned monitoring qualification deployment."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from services import docker

reference = ROOT / '.local/references/monitoring-stack/resolved-private.json'
if not reference.exists():
    raise SystemExit('Pinned local deployment reference is required before qualification.')
config = json.loads(reference.read_text())
config['name'] = 'avenra-monitoring'
for name in ['batch-ppa', 'tazama-demo']:
    config['services'].pop(name, None)
secret_path = ROOT / '.local/monitoring-db.secret'
if not secret_path.exists():
    secret_path.write_text(secrets.token_hex(32))
    secret_path.chmod(0o600)
password = secret_path.read_text().strip()
images = []
for name, service in config['services'].items():
    image = service['image']
    if image.startswith('tazamaorg/'):
        image = image.split(':')[0] + ':4.0.0'
    service.pop('container_name', None)
    service.pop('build', None)
    service.pop('expose', None)
    service['ports'] = []
    if name in {'tms-service', 'admin-service'}:
        service['ports'] = [{'target': 3000 if name == 'tms-service' else 3100, 'published': '4842' if name == 'tms-service' else '4843', 'host_ip': '127.0.0.1', 'protocol': 'tcp'}]
    environment = service.get('environment', {})
    for key in list(environment):
        if 'PASSWORD' in key:
            environment[key] = password
        if key == 'NODE_TLS_REJECT_UNAUTHORIZED':
            environment.pop(key)
    environment.update({'MAX_CPU': '1', 'APM_ACTIVE': 'false'})
    service['environment'] = environment
    service['networks'] = {'risk': None}
    service['mem_limit'] = '384m' if image.startswith('tazamaorg/') else '512m' if name == 'core-postgres' else '128m'
    if image.startswith('tazamaorg/'):
        environment['NODE_OPTIONS'] = '--max-old-space-size=256'
    alias = 'avenra/monitor-' + name + ':4'
    images.append((image, alias))
    service['image'] = alias
    if name == 'core-postgres':
        service.setdefault('volumes', []).append({'type': 'volume', 'source': 'risk-data', 'target': '/var/lib/postgresql'})
    if name == 'nats':
        service['command'] = ['--jetstream', '--store_dir', '/data']
        service['volumes'] = [{'type': 'volume', 'source': 'risk-messages', 'target': '/data'}]
config['networks'] = {'risk': {'internal': True}, 'edge': {}}
config['volumes'] = {'risk-data': {}, 'risk-messages': {}}
for name in ['tms-service', 'admin-service']:
    config['services'][name]['ports'] = []
proxy = ROOT / '.local/deploy/monitoring-ingress.conf'
proxy.parent.mkdir(exist_ok=True)
proxy.write_text('events {}\nhttp { server { listen 8080; client_max_body_size 1m; location / { proxy_pass http://tms-service:3000; } } server { listen 8081; client_max_body_size 1m; location / { proxy_pass http://admin-service:3100; } } }\n')
config['services']['ingress'] = {'image': 'avenra/monitor-ingress:4', 'ports': ['127.0.0.1:4842:8080', '127.0.0.1:4843:8081'], 'volumes': [{'type': 'bind', 'source': str(proxy), 'target': '/etc/nginx/nginx.conf', 'read_only': True}], 'networks': {'risk': None, 'edge': None}, 'depends_on': ['tms-service', 'admin-service'], 'mem_limit': '64m'}
images.append(('nginx:stable-alpine', 'avenra/monitor-ingress:4'))

def image(pair):
    original, alias = pair
    probe = subprocess.run(docker('image', 'inspect', original), capture_output=True)
    if probe.returncode:
        subprocess.run(docker('pull', original), check=True, stdout=subprocess.DEVNULL)
    subprocess.run(docker('tag', original, alias), check=True)
    metadata = json.loads(subprocess.check_output(docker('image', 'inspect', original)))[0]
    print('Prepared monitoring artifact:', alias, flush=True)
    return {'original': original, 'alias': alias, 'image_id': metadata['Id'], 'digests': metadata.get('RepoDigests', []), 'architecture': metadata['Architecture']}

with ThreadPoolExecutor(2) as workers:
    receipts = list(workers.map(image, images))
path = ROOT / '.local/deploy/monitoring.json'
path.parent.mkdir(exist_ok=True)
path.write_text(json.dumps(config, indent=2))
path.chmod(0o600)
receipt = ROOT / 'LICENSES/monitoring/runtime-images.json'
receipt.write_text(json.dumps({'scope': 'Local integration qualification; no production deployment qualification', 'images': receipts}, indent=2) + '\n')
subprocess.run(docker('compose', '-f', str(path), 'up', '-d'), check=True)
subprocess.run([sys.executable, str(ROOT / 'scripts/build_monitor_router.py')], check=True)
print('Private monitoring qualification deployment started. Its interface must pass tests before being enabled.')
