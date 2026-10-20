#!/usr/bin/env python3
"""Replace this installation's bootstrap product credential without logging it."""
import base64
import json
import os
from pathlib import Path
import secrets
import string
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from avenra.services import Transport
from avenra.gateways import EngineError

active = ROOT / '.local/data/product-credentials.json'
pending = active.with_suffix('.pending.json')

def client(credentials):
    authorization = base64.b64encode((credentials['username'] + ':' + credentials['password']).encode()).decode()
    return Transport('http://127.0.0.1:4841', {'Authorization': 'Basic ' + authorization, 'Fineract-Platform-TenantId': 'default'})

if active.exists():
    credentials = json.loads(active.read_text())
    client(credentials).call('GET', '/fineract-provider/api/v1/users/1')
    print('Existing private product identity verified.')
    raise SystemExit(0)
if pending.exists():
    candidate = json.loads(pending.read_text())
    try:
        client(candidate).call('GET', '/fineract-provider/api/v1/users/1')
    except EngineError:
        pass
    else:
        os.replace(pending, active)
        print('Previously accepted private product identity recovered.')
        raise SystemExit(0)
bootstrap = client({'username': 'mifos', 'password': 'password'})
bootstrap.call('GET', '/fineract-provider/api/v1/users/1')
alphabet = string.ascii_letters + string.digits + '!@#_-'
password = 'Av9!'
for _ in range(28):
    password += secrets.choice(alphabet.replace(password[-1], ''))
credentials = {'username': 'mifos', 'password': password}
pending.write_text(json.dumps(credentials))
pending.chmod(0o600)
bootstrap.call('PUT', '/fineract-provider/api/v1/users/1', {'password': password, 'repeatPassword': password})
client(credentials).call('GET', '/fineract-provider/api/v1/users/1')
os.replace(pending, active)
print('Bootstrap product password replaced and the private identity verified.')
