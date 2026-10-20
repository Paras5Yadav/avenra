#!/usr/bin/env python3
"""Capture, verify and restore an inactive coordinated Avenra recovery set."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from avenra.errors import DomainError
from avenra import recovery_set, recovery_restore, recovery_native, recovery_banks, recovery_aa
from avenra.private_config import read_document


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
    capture=sub.add_parser('capture')
    for name in ['data','destination','artifact','release-trust','signing-key','accounting-dsn','native-directory','pg-bin','request-id','actor']:capture.add_argument('--'+name,required=True)
    for action in ['verify','stage','import-databases','activate']:
        command=sub.add_parser(action)
        command.add_argument('--source' if action in {'verify','stage'} else '--directory',required=True)
        for name in ['recovery-trust','release-trust']:command.add_argument('--'+name,required=True)
        if action=='stage':command.add_argument('--destination',required=True)
        if action=='import-databases':
            for name in ['admin-dsn','pg-bin']:command.add_argument('--'+name,required=True)
        if action in {'import-databases','activate'}:
            for name in ['request-id','actor']:command.add_argument('--'+name,required=True)
        if action=='activate':
            for name in ['accounting-origin','fault-origin']:command.add_argument('--'+name,required=True)
    start=sub.add_parser('native-start');start.add_argument('--directory',required=True)
    for name in ['recovery-trust','release-trust']:start.add_argument('--'+name,required=True)
    for role in ['accounting','fault']:start.add_argument('--'+role+'-port',type=int,required=True)
    stop=sub.add_parser('native-stop');stop.add_argument('--directory',required=True)
    banks=sub.add_parser('bank-start');banks.add_argument('--directory',required=True);banks.add_argument('--ports',required=True)
    for name in ['recovery-trust','release-trust','request-id','actor']:banks.add_argument('--'+name,required=True)
    bank_stop=sub.add_parser('bank-stop');bank_stop.add_argument('--directory',required=True)
    aa=sub.add_parser('aa-start');aa.add_argument('--directory',required=True);aa.add_argument('--ports',required=True);aa.add_argument('--api-origin',required=True)
    for name in ['recovery-trust','release-trust','request-id','actor']:aa.add_argument('--'+name,required=True)
    aa_stop=sub.add_parser('aa-stop');aa_stop.add_argument('--directory',required=True)
    args=parser.parse_args()
    try:
        if args.action=='capture':value=recovery_set.capture(args.data,args.destination,args.artifact,args.release_trust,args.signing_key,args.accounting_dsn,args.native_directory,args.pg_bin,args.request_id,args.actor)
        elif args.action=='verify':
            checked=recovery_set.verify(args.source,args.recovery_trust,args.release_trust)
            value={k:checked[k] for k in ['set_id','directory']};value.update(valid=True,artifact_id=checked['manifest']['artifact_id'],files=len(checked['manifest']['files']))
        elif args.action=='stage':value=recovery_set.stage(args.source,args.destination,args.recovery_trust,args.release_trust)
        elif args.action=='import-databases':value=recovery_restore.restore_databases(args.directory,args.recovery_trust,args.release_trust,args.admin_dsn,args.pg_bin,args.request_id,args.actor)
        elif args.action=='native-start':value=recovery_native.start(args.directory,args.accounting_port,args.fault_port,args.recovery_trust,args.release_trust)
        elif args.action=='native-stop':value=recovery_native.stop(args.directory)
        elif args.action=='bank-start':value=recovery_banks.start(args.directory,read_document(args.ports),args.recovery_trust,args.release_trust,args.request_id,args.actor)
        elif args.action=='bank-stop':value=recovery_banks.stop(args.directory)
        elif args.action=='aa-start':value=recovery_aa.start(args.directory,read_document(args.ports),args.api_origin,args.recovery_trust,args.release_trust,args.request_id,args.actor)
        elif args.action=='aa-stop':value=recovery_aa.stop(args.directory)
        else:value=recovery_restore.activate(args.directory,args.recovery_trust,args.release_trust,args.accounting_origin,args.fault_origin,args.request_id,args.actor)
        print(json.dumps(value));return 0
    except DomainError as error:
        print(json.dumps({'error':error.code,'message':error.message}));return 2


if __name__=='__main__':raise SystemExit(main())
