#!/usr/bin/env python3
"""Fetch exact public financial-data contracts into the private local cache."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from avenra.aa.contracts import ARTIFACTS
from avenra.errors import require
from avenra.lab_identity import private_write


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--cache',default=str(ROOT/'.local/aa-research'));args=parser.parse_args()
    directory=Path(args.cache).resolve();directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    manifest=json.loads((ROOT/'LICENSES/financial-data-profile.json').read_text());rows=[]
    for binding in manifest['bindings']:
        filename=ARTIFACTS[binding['role']][0];path=directory/filename
        if path.exists():raw=path.read_bytes()
        else:
            with urlopen(Request(binding['source'],headers={'User-Agent':'Avenra-public-contract-installer/1'}),timeout=20) as response:raw=response.read(2_000_001)
        require(len(raw)<=2_000_000 and hashlib.sha256(raw).hexdigest()==binding['sha256'],'CONTRACT_INTEGRITY','Public contract differs from the selected immutable release. Preserve the bytes and review a new version explicitly.',409)
        if not path.exists():private_write(path,raw)
        rows.append({'role':binding['role'],'version':binding['version'],'sha256':binding['sha256']})
    print(json.dumps({'installed':rows,'cache':str(directory),'official_conformance':False}));return 0


if __name__=='__main__':raise SystemExit(main())
