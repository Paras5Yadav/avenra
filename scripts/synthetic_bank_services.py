#!/usr/bin/env python3
"""Inspect or stop only this installation's managed synthetic bank processes."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['status','stop'])
    args=parser.parse_args()
    path=ROOT/'.local/data/synthetic-bank-processes.json'
    if not path.exists():print(json.dumps({'workers':[],'scope':'No managed synthetic bank demonstration.'}));return
    state=json.loads(path.read_text())
    results=[];unresolved=False
    for worker in state['workers']:
        config=Path(worker['config']).resolve()
        if not config.is_relative_to(ROOT/'.local/data/synthetic-banks'):raise RuntimeError('Worker configuration is outside this installation.')
        def command():
            result=subprocess.run(['ps','-p',str(worker['pid']),'-o','command='],capture_output=True,text=True)
            return result.stdout.strip()
        current=command()
        owned='avenra.lab_bank' in current and str(config) in current
        if args.action=='stop' and owned:
            os.kill(worker['pid'],signal.SIGTERM)
            deadline=time.monotonic()+15
            while command() and time.monotonic()<deadline:time.sleep(.1)
            if command():unresolved=True
            status='stopped' if not command() else 'draining'
        else:
            status='running' if owned else 'not_owned' if current else 'stopped'
            if current and not owned:unresolved=True
        results.append({'worker_id':worker['worker_id'],'endpoint':worker['endpoint'],'state':status})
    if args.action=='stop' and not unresolved:path.unlink()
    print(json.dumps({'workers':results,'scope':'Owned loopback synthetic bank services only.'}))
    if unresolved:raise SystemExit(2)


if __name__=='__main__':main()
