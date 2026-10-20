#!/usr/bin/env python3
"""Run the owned financial acceptance matrix sequentially on one fixed build."""
import hashlib
import argparse
import json
from pathlib import Path
import subprocess
import os
import stat
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from avenra.private_config import read_document, write_document
from avenra.errors import DomainError
from avenra.util import application_fingerprint
from scripts.qualification_storage import CASE_PACK_HELPERS, harness_case_pack_hash
from scripts.readiness_evidence import COORDINATED_PROFILES, coordinated_evidence

CASES = [
    ('Identity/store', 'verify_units.py', [], 'unit-verification.json'),
    ('API/worker separation', 'verify_asgi.py', [], 'asgi-verification.json'),
    ('Original local financial/HTTP', 'verify.py', ['--isolated', '--restart'], 'verification.json'),
    ('Banking SQLite', 'verify_banking.py', [], 'banking-verification.json'),
    ('Banking PostgreSQL', 'verify_banking.py', ['--postgres'], 'banking-postgres-verification.json'),
    ('Banking HTTP', 'verify_banking_http.py', [], 'banking-http-verification.json'),
    ('Simulation SQLite', 'verify_simulation.py', [], 'simulation-verification.json'),
    ('Simulation PostgreSQL', 'verify_simulation.py', ['--postgres'], 'simulation-postgres-verification.json'),
    ('Simulation HTTP', 'verify_simulation_http.py', [], 'simulation-http-verification.json'),
    ('UPI SQLite', 'verify_upi.py', [], 'upi-verification.json'),
    ('UPI PostgreSQL', 'verify_upi.py', ['--postgres'], 'upi-postgres-verification.json'),
    ('UPI HTTP', 'verify_upi_http.py', [], 'upi-http-verification.json'),
    ('Synthetic TLS banks', 'verify_synthetic_banks.py', [], 'synthetic-bank-verification.json'),
    ('Versioned UPI XML and independent oracle SQLite', 'verify_upi_wire.py', [], 'upi-wire-verification.json'),
    ('Versioned UPI XML and independent oracle PostgreSQL', 'verify_upi_wire.py', ['--postgres'], 'upi-wire-postgres-verification.json'),
    ('Lifecycle SQLite', 'verify_fidelity.py', [], 'fidelity-verification.json'),
    ('Lifecycle PostgreSQL', 'verify_fidelity.py', ['--postgres'], 'fidelity-postgres-verification.json'),
    ('Banking product adapters', 'verify_products.py', [], 'product-verification.json'),
    ('Native service response recovery', 'verify_service_recovery.py', [], 'service-recovery-verification.json'),
    ('Financial-data roles SQLite', 'verify_aa.py', [], 'aa-verification.json'),
    ('Financial-data roles PostgreSQL', 'verify_aa.py', ['--postgres'], 'aa-postgres-verification.json'),
    ('Signed application upgrades and rollback', 'verify_releases.py', [], 'release-verification.json'),
    ('Standalone SQLite recovery checkpoints', 'verify_checkpoints.py', [], 'checkpoint-verification.json'),
    ('Operational incidents and real outage recovery', 'verify_operations.py', [], 'operations-verification.json'),
    ('Additive migration of the preserved predecessor', 'verify_operations_upgrade.py', [], 'operations-upgrade-verification.json'),
    ('Coordinated cold application and native recovery', 'verify_coordinated_recovery.py', [], 'coordinated-recovery-verification.json'),
    ('Coordinated XML participant cold recovery and contract-aware rollback', 'verify_coordinated_recovery.py', ['--xml'], 'coordinated-upi-wire-recovery-verification.json'),
    ('Coordinated independent PSP/network cold recovery', 'verify_coordinated_recovery.py', ['--independent'], 'coordinated-peer-recovery-verification.json'),
    ('Coordinated AA participant recovery', 'verify_aa_recovery.py', [], 'aa-coordinated-recovery-verification.json'),
    ('Coordinated HTTPS synthetic identity and bank recovery', 'verify_identity_recovery.py', [], 'identity-coordinated-recovery-verification.json'),
    ('Mixed-tenant capacity and actual worker loss', 'verify_capacity.py', [], 'capacity-verification.json'),
    ('Native journal UTC policy', 'qualify_clock.py', [], 'accounting-clock-verification.json'),
    ('Full control/accounting restore', 'backup_restore.py', ['--verify-restore'], 'backup-verification.json'),
]


def matrix_definition_hash():
    return hashlib.sha256(json.dumps(CASES,separators=(',',':')).encode()).hexdigest()


def qualified_receipt(script, filename, value, fingerprint, case_hash):
    """Apply the same evidence rules to fresh execution and prefix reuse."""
    if not isinstance(value,dict) or value.get('passed') is not True or value.get('application_fingerprint')!=fingerprint:
        return False
    checks=value.get('checks')
    if isinstance(checks,list) and (not checks or not all(isinstance(check,dict) and check.get('passed') is True for check in checks)):
        return False
    if script in CASE_PACK_HELPERS and value.get('case_pack_sha256')!=case_hash:
        return False
    if filename in COORDINATED_PROFILES:
        return coordinated_evidence(ROOT,filename,value,fingerprint,case_hash)['passed']
    if script=='verify_capacity.py':
        from scripts.verify_capacity import REFERENCE
        from avenra.util import digest
        return value.get('reference_profile_passed') is True and value.get('mode')=='reference' and value.get('profile')==REFERENCE and value.get('reference_profile_sha256')==digest(REFERENCE)
    return True


def resume_prefix(path, fingerprint):
    original=Path(path)
    if original.is_symlink():raise ValueError('Resume input cannot be a link.')
    path=original.resolve()
    if not path.is_relative_to(ROOT/'.local/verification-attempts') or path.name!='manifest.json':
        raise ValueError('Resume a retained acceptance manifest from this installation.')
    previous=read_document(path)
    if not isinstance(previous,dict):raise ValueError('Resume requires a retained manifest object.')
    if previous.get('application_fingerprint')!=fingerprint or not isinstance(previous.get('cases'),list) or len(previous['cases'])>len(CASES):
        raise ValueError('Resume source differs from the current fixed build.')
    if previous.get('matrix_definition_sha256')!=matrix_definition_hash():
        raise ValueError('Resume requires a verified matching matrix definition, including every command argument.')
    prefix=[]
    for index,row in enumerate(previous['cases']):
        if not isinstance(row,dict):raise ValueError('Resume cases must be retained objects.')
        if row.get('passed') is not True:break
        name,script,arguments,filename=CASES[index]
        receipt=ROOT/'.local'/filename
        case_hash=harness_case_pack_hash(ROOT,script)
        if row.get('name')!=name or row.get('case_pack_sha256')!=case_hash or Path(row.get('report','')).resolve()!=receipt:
            raise ValueError('A completed case or its script no longer matches the retained manifest.')
        info=receipt.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_nlink!=1:
            raise ValueError('A retained receipt is not an ordinary owned evidence file.')
        # Older verifiers inherited the umask inside the private installation.
        # Narrow those evidence modes before using the strict private reader;
        # their bytes and qualification identity remain unchanged.
        if info.st_mode&0o077:receipt.chmod(0o600)
        value=read_document(receipt)
        checks=len(value['checks']) if isinstance(value.get('checks'),list) else value.get('tests')
        if checks!=row.get('checks') or not qualified_receipt(script,filename,value,fingerprint,case_hash):
            raise ValueError('A completed case lacks its matching passing receipt.')
        prefix.append((row,value))
    return prefix,{'source':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'reused_cases':len(prefix),
                   'scope':'Only the consecutive passing prefix with matching build, current case scripts and passing retained receipts is reused. Failed/unfinished and subsequent cases execute again.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--resume',type=Path)
    args=parser.parse_args();fingerprint=application_fingerprint();prefix=[];resumed=None
    try:
        if args.resume:prefix,resumed=resume_prefix(args.resume,fingerprint)
    except (OSError,ValueError,KeyError,TypeError,DomainError) as error:
        print(json.dumps({'passed':False,'error':'Resume refused: '+str(error)}),flush=True);return 2
    stamp = str(time.time_ns());folder = ROOT / '.local/verification-attempts' / ('acceptance-' + stamp);folder.mkdir(mode=0o700, parents=True)
    report = {'passed': False, 'created_at': time.time(), 'application_fingerprint': fingerprint, 'cases': [],'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'matrix_definition_sha256':matrix_definition_hash(),
              'scope': 'Owned local regression matrix. This is additional to deployed runtime qualification and does not establish institutional production readiness.'}
    if resumed:
        report['resumed_from']=resumed
        for index,(row,value) in enumerate(prefix):
            retained=folder/('receipt-'+str(index+1).zfill(2)+'.json');write_document(retained,value)
            report['cases'].append({**row,'retained_receipt':str(retained),'retained_receipt_sha256':hashlib.sha256(retained.read_bytes()).hexdigest(),'reused_verified_case':True})
        print(json.dumps({'resumed_verified_cases':len(prefix),'manifest':resumed['source']}),flush=True)
    target = ROOT / '.local/acceptance-matrix.json'
    for index, (name, script, arguments, filename) in enumerate(CASES):
        if index<len(prefix):continue
        print(json.dumps({'running': name}), flush=True)
        case_hash = harness_case_pack_hash(ROOT,script)
        start = time.time();log = folder / (str(index + 1).zfill(2) + '.log')
        with log.open('wb') as output:
            log.chmod(0o600)
            result = subprocess.run([sys.executable, str(ROOT / 'scripts' / script), *arguments], cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
        receipt = ROOT / '.local' / filename
        try:
            value = json.loads(receipt.read_text())
            if not isinstance(value,dict):raise ValueError('Qualification receipt must be an object.')
            checks = len(value['checks']) if isinstance(value.get('checks'), list) else value.get('tests')
            fresh = value.get('application_fingerprint') == report['application_fingerprint'] and receipt.stat().st_mtime >= start - 1
            passed = result.returncode == 0 and fresh and qualified_receipt(script,filename,value,fingerprint,case_hash)
        except (OSError, ValueError, TypeError, KeyError, OverflowError): passed, checks = False, None
        passed = passed and harness_case_pack_hash(ROOT,script) == case_hash
        row = {'name': name, 'passed': passed, 'checks': checks, 'seconds': round(time.time() - start, 3), 'log': str(log),
               'report': str(receipt), 'case_pack_sha256': case_hash}
        if passed:
            retained=folder/('receipt-'+str(index+1).zfill(2)+'.json');write_document(retained,value)
            row.update(retained_receipt=str(retained),retained_receipt_sha256=hashlib.sha256(retained.read_bytes()).hexdigest())
        report['cases'].append(row);write_document(target, report)
        print(json.dumps({k: row[k] for k in ['name', 'passed', 'checks', 'seconds']}), flush=True)
        if not passed or application_fingerprint() != report['application_fingerprint'] or hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=report['runner_sha256']:
            report['error'] = 'A case failed or the tested build changed; inspect retained case logs.'
            break
    cases_current=all(row['case_pack_sha256']==harness_case_pack_hash(ROOT,case[1]) for row,case in zip(report['cases'],CASES))
    report.update(passed=len(report['cases']) == len(CASES) and all(x['passed'] for x in report['cases']) and cases_current and application_fingerprint() == report['application_fingerprint'] and hashlib.sha256(Path(__file__).read_bytes()).hexdigest()==report['runner_sha256'], completed_at=time.time())
    write_document(target, report);write_document(folder / 'manifest.json', report)
    print(json.dumps({'passed': report['passed'], 'cases': len(report['cases']), 'checks': sum(x.get('checks') or 0 for x in report['cases']), 'report': str(target)}), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__': raise SystemExit(main())
