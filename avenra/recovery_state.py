"""Local restore guards and explicit loopback engine bindings.

Restored credentials must never start a process against their source deployment.
These files are controlled by the installation operator, not tenant input.
"""
from pathlib import Path
from urllib.parse import urlsplit

from .errors import require

INACTIVE = 'recovery-inactive.json'


def require_active(directory):
    marker = Path(directory) / INACTIVE
    require(not marker.exists() and not marker.is_symlink(), 'RECOVERY_INACTIVE',
            'This restored deployment is inactive. Complete its explicit recovery binding before startup.', 503)


def engine_bindings(directory, accounting, fault):
    from .private_config import read_document
    value = read_document(Path(directory) / 'engine-endpoints.json')
    if value is None: return accounting, fault
    require(set(value) == {'format', 'accounting', 'fault'} and value['format'] == 'avenra-loopback-engines/1',
            'ENGINE_CONFIGURATION', 'Unsupported private loopback engine bindings.', 503)
    for role in ['accounting', 'fault']:
        origin = value[role]
        require(isinstance(origin, str), 'ENGINE_CONFIGURATION', 'Use an explicit loopback engine origin.', 503)
        try: parsed = urlsplit(origin);port = parsed.port
        except ValueError: parsed, port = None, None
        require(parsed and parsed.scheme == 'http' and parsed.hostname == '127.0.0.1' and port and
                not (parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment),
                'ENGINE_CONFIGURATION', 'This engine profile accepts explicit IPv4 loopback HTTP ports only.', 503)
    return value['accounting'], value['fault']
