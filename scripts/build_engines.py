#!/usr/bin/env python3
"""Build the local accounting and fault executables from included source."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]


def build(roles):
    go = shutil.which('go') or '/opt/homebrew/bin/go'
    binaries = ROOT / '.local/bin'
    binaries.mkdir(parents=True, exist_ok=True)
    receipt_path = ROOT / '.local/builds.json'
    receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
    definitions = {
        'accounting': ['-ldflags=-X avenra.local/accounting/cmd.Version=avenra-0.1.0', '.'],
        'fault': ['-ldflags=-X avenra.local/fault/v2.Version=avenra-0.1.0', './cmd/server']
    }
    for role in roles:
        if role == 'accounting':
            regenerate_protocol(go, binaries)
        target = binaries / role
        subprocess.run([go, 'build', '-o', str(target)] + definitions[role], cwd=ROOT / 'engines' / role, check=True)
        target.chmod(0o700)
        receipt[role] = {'built_at': datetime.now(timezone.utc).isoformat(),
                         'binary_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                         'source_receipt_sha256': hashlib.sha256((ROOT / 'LICENSES' / role / 'adaptations.json').read_bytes()).hexdigest(),
                         'toolchain': subprocess.check_output([go, 'version'], text=True).strip()}
        receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
        print(role + ': built from included source', flush=True)


def regenerate_protocol(go, binaries):
    protoc = shutil.which('protoc') or '/opt/homebrew/bin/protoc'
    plugins = [('protoc-gen-go', 'google.golang.org/protobuf/cmd/protoc-gen-go@v1.36.10'),
               ('protoc-gen-go-grpc', 'google.golang.org/grpc/cmd/protoc-gen-go-grpc@v1.5.1')]
    import os
    for name, package in plugins:
        if not (binaries / name).is_file():
            subprocess.run([go, 'install', package], env={**os.environ, 'GOBIN': str(binaries)}, check=True)
    engine = ROOT / 'engines/accounting'
    source = 'internal/replication/grpc/replication_service.proto'
    command = [protoc, '-I', '.', '--plugin=protoc-gen-go=' + str(binaries / 'protoc-gen-go'),
               '--plugin=protoc-gen-go-grpc=' + str(binaries / 'protoc-gen-go-grpc'),
               '--go_out=.', '--go_opt=paths=source_relative', '--go-grpc_out=.', '--go-grpc_opt=paths=source_relative']
    include = Path(protoc).resolve().parents[1] / 'include'
    if include.is_dir():
        command += ['-I', str(include)]
    subprocess.run(command + [source], cwd=engine, check=True)
    generated = []
    for name in ('replication_service.pb.go', 'replication_service_grpc.pb.go'):
        path = engine / 'internal/replication/grpc' / name
        path.write_text('// Modified for Avenra; regenerated from the adapted protocol. See LICENSES/.\n' + path.read_text())
        generated.append({'path': path.relative_to(ROOT).as_posix(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    (ROOT / 'LICENSES/accounting/generated.json').write_text(json.dumps({
        'reason': 'Regenerate length-delimited descriptors after changing the local Go module path.',
        'protocol_sha256': hashlib.sha256((engine / source).read_bytes()).hexdigest(),
        'compiler': subprocess.check_output([protoc, '--version'], text=True).strip(), 'plugins': dict(plugins), 'files': generated}, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('roles', nargs='*', choices=['accounting', 'fault'])
    args = parser.parse_args()
    build(args.roles or ['accounting', 'fault'])
