#!/usr/bin/env python3
"""Explicit private deployment installation steps; print no credentials."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from avenra.database_deployment import provision
from avenra.deployment_identity import issue
from avenra.lab_identity import private_read
from avenra.local_database import admin_dsn
from avenra.synthetic_identity import provision as provision_identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['new-database', 'issue-identity', 'synthetic-issuer'])
    parser.add_argument('--data', required=True);parser.add_argument('--admin-dsn-file')
    parser.add_argument('--name');parser.add_argument('--port', type=int);parser.add_argument('--external-origin')
    args = parser.parse_args();directory = Path(args.data).resolve()
    if args.action == 'new-database':
        dsn = private_read(args.admin_dsn_file).decode().strip() if args.admin_dsn_file else admin_dsn(ROOT)
        result = provision(directory, dsn)
    elif args.action == 'issue-identity':
        if not args.name: parser.error('issue-identity requires --name')
        result = issue(directory, args.name)
    else:
        if not args.port or not args.external_origin: parser.error('synthetic-issuer requires --port and --external-origin')
        result = provision_identity(directory, args.port, args.external_origin)
        # Passwords and client secret remain in private fixture files.
        result = {k: result[k] for k in ['config', 'directory', 'identity_reference', 'issuer', 'client_id', 'audience', 'ca_file']}
    print(json.dumps(result));return 0


if __name__ == '__main__': raise SystemExit(main())
