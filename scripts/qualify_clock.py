#!/usr/bin/env python3
"""Observe native journal time, optionally enforce UTC and restart owned serving."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from avenra.clock_policy import enforce
from avenra.gateways import AccountingGateway
from avenra.lab_identity import private_read
from avenra.private_config import write_document
from avenra.util import application_fingerprint,uid


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    import local
    import psycopg
    report={'passed':False,'checks':[],'created_at':time.time(),'scope':'Actual native UTC write/read/default timestamps and preservation of a prior journal response. Existing historical timestamps are not repaired by this policy.'}
    gateway=AccountingGateway();environment=uid('clock');ledger='avn_'+environment;gateway.create(ledger,environment)
    def probe(reference):
        start=time.time();value=gateway.post(ledger,{'postings':[{'source':'world','destination':'fixture:clock','asset':'INR/2','amount':1}],'reference':reference},reference);end=time.time()
        stamp=value['timestamp'];epoch=datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp()
        return value,{'host_before':start,'host_after':end,'native_epoch':epoch,'offset_seconds':epoch-end,'within_utc_window':start-1<=epoch<=end+1}
    def check(name,condition):
        report['checks'].append({'name':name,'passed':bool(condition)})
        if not condition:raise AssertionError(name)
    restarted=False
    try:
        original,observed=probe('clock-before-'+environment);report['before']=observed
        if args.apply:
            check('A completed control/accounting backup exists before changing the journal deployment',json.loads((ROOT/'.local/backup-verification.json').read_text())['passed'])
            local.stop_console();report['configuration']=enforce(ROOT)
            state=json.loads(local.STATE.read_text());entry=state.get('accounting')
            check('The accounting process is owned by this installation',entry and local.process_owned(entry))
            os.kill(entry['pid'],signal.SIGTERM);deadline=time.monotonic()+20
            while local.process_owned(entry) and time.monotonic()<deadline:time.sleep(.1)
            check('The owned accounting process drains before restart',not local.process_owned(entry))
            state.pop('accounting');write_document(local.STATE,state);local.start();restarted=True
        current,observed=probe('clock-after-'+environment);report['after']=observed
        check('New default native transaction timestamps fall inside the actual UTC request interval',observed['within_utc_window'])
        with psycopg.connect(private_read(ROOT/'.local/data/accounting.dsn').decode().strip(),autocommit=True) as db:zone=db.execute('SHOW timezone').fetchone()[0]
        check('Fresh accounting database sessions use UTC',zone=='UTC')
        replay=gateway.transactions(ledger,original['reference'])[0]
        check('A prior native response retains its original timestamp and financial identity',replay['timestamp']==original['timestamp'] and replay['id']==original['id'])
        check('Clock qualification creates only the declared two synthetic postings',len(gateway.transactions(ledger,all_pages=True))==2)
        report.update(passed=True,ledger=ledger,existing_timestamp_repair_required=not report['before']['within_utc_window'])
    except Exception as error:report.update(error=str(error))
    finally:
        if args.apply and not restarted:
            try:local.start()
            except Exception:pass
    report.update(application_fingerprint=application_fingerprint(),native_binary_sha256=hashlib.sha256((ROOT/'.local/bin/accounting').read_bytes()).hexdigest())
    target=ROOT/'.local/accounting-clock-verification.json';write_document(target,report);write_document(ROOT/'.local/verification-attempts'/('clock-'+str(time.time_ns())+'.json'),report)
    print(json.dumps({'passed':report['passed'],'checks':len(report['checks']),'offset_seconds':report.get('after',{}).get('offset_seconds'),'report':str(target),'error':report.get('error')}))
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
