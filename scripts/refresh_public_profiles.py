#!/usr/bin/env python3
"""Pin official public contracts and quarantine malformed or mismatched versions."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
PROFILES = [('aa', 'AA_2_1_0.yaml', '2.1.0'), ('fip', 'FIP_2_2_0.yaml', '2.2.0'), ('fiu', 'FIU_2_0_0.yaml', '2.0.0'), ('aa_interop', 'AA_Interop_1_0_0.yaml', '1.0.0'), ('fip_interop', 'FIP_Interop_1_0_0.yaml', '1.0.0'), ('fiu_interop', 'FIU_Interop_1_0_0.yaml', '1.0.0')]


def main():
    import yaml
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh', action='store_true')
    args = parser.parse_args()
    cache = ROOT / '.local/public-specifications';cache.mkdir(parents=True, exist_ok=True)
    records = []
    for role, name, version in PROFILES:
        record = {'role': role, 'file': name, 'source': 'https://specifications.rebit.org.in/api_specifications/account_aggregator/' + name, 'advertised_version': version, 'runtime_implemented': False, 'interoperability_qualified': False}
        path = cache / name
        try:
            if args.refresh or not path.exists():
                request = Request(record['source'], headers={'User-Agent': 'Avenra-contract-registry/1'})
                with urlopen(request, timeout=15) as response:
                    raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ValueError('Contract exceeds the supported registry limit.')
                path.write_bytes(raw)
            raw = path.read_bytes()
            record['sha256'] = hashlib.sha256(raw).hexdigest()
            try:
                specification = yaml.safe_load(raw)
                declared = str(specification.get('info', {}).get('version'))
                record.update(declared_version=declared, state='metadata_matches' if declared == version else 'quarantined_version_mismatch', operation_count=sum(sum(1 for method in value if method.lower() in {'get', 'post', 'put', 'delete', 'patch'}) for value in specification.get('paths', {}).values() if isinstance(value, dict)))
            except (yaml.YAMLError, AttributeError, TypeError):
                record.update(state='quarantined_invalid_document', error='Official document did not parse as the expected API schema; original bytes retained.')
        except Exception as error:
            record.update(state='retrieval_failed', error_type=type(error).__name__)
        records.append(record)
    bindings=json.loads((ROOT/'LICENSES/financial-data-profile.json').read_text())['bindings']
    for record in records:
        binding=next((x for x in bindings if x['role']==record['role'] and x['sha256']==record.get('sha256')),None)
        if binding:record['local_synthetic_binding']={k:binding[k] for k in ['implementation','qualification']}
    report = {'retrieved_at': time.time(), 'scope': 'Official public contract metadata. Selected AA/FIP/FIU deposit subsets have a separately qualified local synthetic binding. Full official profiles and interoperability remain unqualified. Original documents stay in the private local cache.', 'profiles': records}
    content = json.dumps(report, indent=2) + '\n'
    (ROOT / 'LICENSES/protocol-profiles.json').write_text(content)
    (ROOT / '.local/public-specifications.json').write_text(content)
    print(json.dumps({'contracts': len(records), 'matching_metadata': sum(x['state'] == 'metadata_matches' for x in records), 'quarantined': [x['role'] for x in records if x['state'].startswith('quarantined')], 'complete_official_wire_adapters': 0, 'local_synthetic_subsets': sum('local_synthetic_binding' in x for x in records)}))


if __name__ == '__main__':
    main()
