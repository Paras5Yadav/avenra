"""Non-executing release admission for retained participant protocol versions.

Every retained connection revision matters: an earlier accepted payment can
still reference an inactive revision. Never infer requirements only from the
currently selected defaults or only from payments that presently look pending.
"""
import ast
import hashlib
import os
from pathlib import Path
import re
import stat

from .errors import require

RELATIVE = 'avenra/deployment_capabilities.py'
FIELDS = {'format', 'engine_bindings', 'bank_routes', 'aa_routes', 'participant_profiles'}
VERSION = re.compile(r'[a-z0-9][a-z0-9_./-]{0,127}')

# Explicit compatibility with the original, byte-pinned owned contracts. An
# unchanged adapter ID with a different hash is NOT exempt. The pending profile
# is metadata only; this exemption does not register an executable connector.
LEGACY = {
    'avenra-local-participant/1': '2aae4631f9d3fe286ce078065c6cc39c3d9f14c1303a896203b5dd3835b7e2b8',
    'avenra-tls-synthetic-bank/1': 'edaa461756e2cd03f5e2f3974b21dcfe2e49f473fa079ad7c1f3522a17c336b2',
    'avenra-external-upi-pending/1': '3293c5c84beb9ecc218fd767f9f7b22538beb5dcb3d1705c1a21167413733292',
}


def validate(value):
    require(isinstance(value, dict) and {'format', 'engine_bindings', 'bank_routes'} <= set(value) <= FIELDS and
            value['format'] == 'avenra-deployment-capabilities/1' and
            all(isinstance(value[key], list) and len(value[key]) <= 8 and
                all(isinstance(item, str) and VERSION.fullmatch(item) for item in value[key]) and
                len(set(value[key])) == len(value[key]) for key in set(value) - {'format'}),
            'RELEASE_CAPABILITIES', 'Candidate capability format is unsupported or ambiguous.', 409)
    return value


def declared(artifact):
    """Read only inventory-bound literal data; never import the candidate."""
    entries = [item for item in artifact['manifest']['files'] if item['path'] == RELATIVE]
    require(len(entries) == 1, 'RELEASE_CAPABILITIES', 'The signed candidate lacks its required capability declaration.', 409)
    path = Path(artifact['directory']) / RELATIVE
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_size <= 16384,
                'RELEASE_CAPABILITIES', 'Candidate capabilities exceed their bounded literal format.', 409)
        raw = stream.read(16385)
    require(len(raw) <= 16384 and hashlib.sha256(raw).hexdigest() == entries[0]['sha256'],
            'RELEASE_CAPABILITIES', 'Capability bytes differ from the verified signed inventory.', 409)
    try:
        body = ast.parse(raw).body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            body = body[1:]
        require(len(body) == 1 and isinstance(body[0], ast.Assign) and len(body[0].targets) == 1 and
                isinstance(body[0].targets[0], ast.Name) and body[0].targets[0].id == 'CAPABILITIES',
                'RELEASE_CAPABILITIES', 'Candidate capabilities must be one declarative literal assignment.', 409)
        value = ast.literal_eval(body[0].value)
    except (SyntaxError, ValueError, TypeError, RecursionError):
        require(False, 'RELEASE_CAPABILITIES', 'Candidate capabilities are not supported literal data.', 409)
    return validate(value)


def required(database):
    if not database.execute("SELECT to_regclass('participant_connections') AS present").fetchone()[0]:
        return []
    parameters = tuple(value for pair in LEGACY.items() for value in pair)
    exempt = ' OR '.join('(adapter_id=%s AND profile_hash=%s)' for _ in LEGACY)
    rows = database.execute('SELECT DISTINCT adapter_id,profile_hash FROM participant_connections WHERE NOT (' + exempt + ') ORDER BY adapter_id,profile_hash LIMIT 9', parameters).fetchall()
    require(len(rows) <= 8, 'RELEASE_CAPABILITIES', 'Retained participant versions exceed the supported release declaration bound.', 409)
    result = []
    for row in rows:
        adapter, profile_hash = row[0], row[1]
        require(isinstance(adapter, str) and isinstance(profile_hash, str) and re.fullmatch('[0-9a-f]{64}', profile_hash) and
                VERSION.fullmatch(adapter + '/' + profile_hash),
                'RELEASE_CAPABILITIES', 'Retained participant protocol identity is invalid.', 409)
        result.append(adapter + '/' + profile_hash)
    return result


def ensure(database, artifact):
    needs = required(database)
    if needs:
        supported = declared(artifact).get('participant_profiles', [])
        require(all(item in supported for item in needs), 'RELEASE_CAPABILITIES',
                'The signed candidate cannot execute every retained participant contract. Preserve the selected release and original financial identities.', 409)
    return needs


def ensure_current(database):
    from .deployment_capabilities import CAPABILITIES
    needs = required(database)
    if needs:
        supported = validate(CAPABILITIES).get('participant_profiles', [])
        require(all(item in supported for item in needs), 'RELEASE_CAPABILITIES',
                'This selected runtime cannot execute a retained participant protocol.', 503)
    return needs


def supports_current(adapter, profile_hash):
    """Admission cannot accept a contract omitted by this release's metadata."""
    if LEGACY.get(adapter) == profile_hash:
        return True
    from .deployment_capabilities import CAPABILITIES
    return adapter + '/' + profile_hash in validate(CAPABILITIES).get('participant_profiles', [])
