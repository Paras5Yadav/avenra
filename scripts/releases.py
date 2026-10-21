#!/usr/bin/env python3
"""Build, verify and select signed Avenra releases; never print credentials."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from avenra import release_bundle as bundle
from avenra import release_runtime as runtime
from avenra.errors import DomainError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    key = sub.add_parser('new-key');key.add_argument('--private', required=True);key.add_argument('--public', required=True)
    build = sub.add_parser('build');build.add_argument('--source', default=str(ROOT));build.add_argument('--wheels', required=True);build.add_argument('--key', required=True);build.add_argument('--output', required=True);build.add_argument('--label', required=True)
    for action in ['inspect', 'install']:
        command = sub.add_parser(action);command.add_argument('--archive', required=True);command.add_argument('--trust', required=True)
        if action == 'install': command.add_argument('--store', required=True)
    prepare = sub.add_parser('prepare-runtime');prepare.add_argument('--artifact', required=True);prepare.add_argument('--trust', required=True);prepare.add_argument('--runtime', required=True)
    for action in ['activate', 'rollback']:
        command = sub.add_parser(action)
        for field in ['data', 'artifact', 'trust', 'runtime', 'expected', 'request-id', 'actor']: command.add_argument('--' + field, required=True)
    for action in ['upgrade-operations', 'upgrade-work-queue']:
        migrate=sub.add_parser(action)
        for field in ['data','artifact','trust','expected','request-id','actor']:migrate.add_argument('--'+field,required=True)
    recover = sub.add_parser('recover');recover.add_argument('--data', required=True);recover.add_argument('--trust', required=True)
    status = sub.add_parser('status');status.add_argument('--data', required=True)
    run = sub.add_parser('run');run.add_argument('--data', required=True);run.add_argument('--trust', required=True);run.add_argument('--mode', choices=['api', 'combined', 'worker'], required=True);run.add_argument('--port', type=int, default=4827);run.add_argument('--host', default='127.0.0.1')
    args = parser.parse_args()
    try:
        if args.action == 'new-key': value = bundle.new_key(args.private, args.public)
        elif args.action == 'build': value = bundle.build(args.source, args.wheels, args.key, args.output, args.label)
        elif args.action in {'inspect', 'install'}:
            value = bundle.inspect(args.archive, args.trust) if args.action == 'inspect' else bundle.install(args.archive, args.trust, args.store)
            value = {k: v for k, v in value.items() if k != 'manifest'}
        elif args.action == 'prepare-runtime': value = bundle.prepare_runtime(args.artifact, args.trust, args.runtime)
        elif args.action in {'activate', 'rollback'}:
            value = runtime.transition(args.data, args.artifact, args.trust, args.runtime, None if args.expected == 'none' else args.expected, args.request_id, args.actor, args.action)
        elif args.action=='upgrade-operations':
            from avenra.database_deployment import upgrade_operations
            value=upgrade_operations(args.data,args.artifact,args.trust,None if args.expected=='none' else args.expected,args.request_id,args.actor)
        elif args.action=='upgrade-work-queue':
            from avenra.database_deployment import upgrade_work_queue
            value=upgrade_work_queue(args.data,args.artifact,args.trust,None if args.expected=='none' else args.expected,args.request_id,args.actor)
        elif args.action == 'recover': value = runtime.recover(args.data, args.trust)
        elif args.action == 'status': value = runtime.status(args.data)
        else:
            command = runtime.command(args.data, args.trust, args.mode, args.port, args.host)
            os.execv(command[0], command)
        print(json.dumps(value));return 0
    except DomainError as error:
        print(json.dumps({'error': error.code, 'message': error.message}));return 2


if __name__ == '__main__': raise SystemExit(main())
