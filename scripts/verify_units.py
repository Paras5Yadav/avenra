#!/usr/bin/env python3
"""Retain identity/store unit-test results with the application source identity."""
import json
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from avenra.util import application_fingerprint

suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'))
log = ROOT / '.local/logs/identity-store-checks.log'
log.parent.mkdir(parents=True, exist_ok=True)
with log.open('w') as output:
    result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
report = {'passed': result.wasSuccessful(), 'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors), 'application_fingerprint': application_fingerprint(), 'created_at': time.time(), 'log': str(log)}
(ROOT / '.local/unit-verification.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report))
raise SystemExit(0 if report['passed'] else 1)
