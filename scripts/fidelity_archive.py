#!/usr/bin/env python3
"""Encrypt completed Avenra snapshots; restore only into a new directory."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from avenra_fidelity.archive import encrypt_snapshot, recovery_key, restore_snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['encrypt', 'restore'])
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--key-file', type=Path, required=True)
    args = parser.parse_args()
    try:
        key = recovery_key(args.key_file, create=args.action == 'encrypt')
        result = (encrypt_snapshot if args.action == 'encrypt' else restore_snapshot)(args.source, args.destination, key)
    except Exception as error:
        print(json.dumps({'passed': False, 'error_type': type(error).__name__, 'message': str(error)}));return 1
    print(json.dumps({'passed': True, **result}));return 0


if __name__ == '__main__': raise SystemExit(main())
