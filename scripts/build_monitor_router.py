#!/usr/bin/env python3
"""Apply a recorded cold-cache routing fix to the pinned local monitoring artifact."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from services import docker


def main():
    source = 'avenra/monitor-event-director:4'
    receipt = json.loads((ROOT / 'LICENSES/monitoring/runtime-images.json').read_text())
    expected = next(x for x in receipt['images'] if x['alias'] == source)
    metadata = json.loads(subprocess.check_output(docker('image', 'inspect', source)))[0]
    if metadata['Id'] != expected['image_id']:
        raise SystemExit('Monitoring router artifact differs from its pinned receipt.')
    context = ROOT / '.local/build/monitor-router'
    context.mkdir(parents=True, exist_ok=True)
    original = context / 'original.js'
    container = subprocess.check_output(docker('create', source), text=True).strip()
    try:
        subprocess.run(docker('cp', container + ':/home/app/build/services/logic.service.js', str(original)), check=True, capture_output=True)
    finally:
        subprocess.run(docker('rm', container), check=True, stdout=subprocess.DEVNULL)
    content = original.read_text()
    start = '            for (const networkMap of networkConfigurationList) {'
    end = '        }\n        else {'
    first, last = content.index(start), content.index(end, content.index(start))
    block = content[first:last].replace('networkMap', 'candidate')
    needle = '                        prunedMessage = candidate.messages.filter'
    if block.count(needle) != 1:
        raise SystemExit('Routing source no longer matches the reviewed fix.')
    block = block.replace(needle, '                        networkMap = candidate;\n' + needle)
    modified = '// Modified for Avenra: retain the selected network map on a cold cache. See LICENSES/monitoring/router-patch.json.\n' + content[:first] + block + content[last:]
    (context / 'logic.service.js').write_text(modified)
    base = next(x for x in expected['digests'] if x.startswith('tazamaorg/'))
    (context / 'Dockerfile').write_text('FROM ' + base + '\nCOPY --chown=nonroot:nonroot logic.service.js /home/app/build/services/logic.service.js\n')
    subprocess.run(docker('build', '--platform', 'linux/amd64', '-t', 'avenra/monitor-router:4.0.0-1', str(context)), check=True)
    output = json.loads(subprocess.check_output(docker('image', 'inspect', 'avenra/monitor-router:4.0.0-1')))[0]
    record = {'base': base, 'original_sha256': hashlib.sha256(original.read_bytes()).hexdigest(), 'modified_sha256': hashlib.sha256(modified.encode()).hexdigest(), 'image_id': output['Id'], 'license': 'Apache-2.0', 'source_reference': 'https://github.com/tazama-lf/event-director/blob/74d13fea685688378b72ac0536b213ae85156971/src/services/logic.service.ts', 'change': 'Remove loop-variable shadowing and assign the matching network map on initial lookup. Original SPDX retained.', 'scope': 'Local integration qualification'}
    (ROOT / 'LICENSES/monitoring/router-patch.json').write_text(json.dumps(record, indent=2) + '\n')
    config_path = ROOT / '.local/deploy/monitoring.json'
    config = json.loads(config_path.read_text())
    config['services']['event-director']['image'] = 'avenra/monitor-router:4.0.0-1'
    config_path.write_text(json.dumps(config, indent=2));config_path.chmod(0o600)
    subprocess.run(docker('compose', '-f', str(config_path), 'up', '-d', 'event-director'), check=True)
    print('Recorded monitoring router fix built and deployed to the private local stack.')


if __name__ == '__main__':
    main()
