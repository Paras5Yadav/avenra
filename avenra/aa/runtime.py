"""Process-lifetime fences for the cooperating synthetic AA role deployment."""
from contextlib import contextmanager, ExitStack
import fcntl
import os
from pathlib import Path
import re
import stat

from ..errors import require
from ..private_config import read_document


def location(directory):
    directory = Path(directory).absolute()
    for parent in [directory, *directory.parents]:
        if parent.name == 'aa-scopes':
            relative = directory.relative_to(parent)
            require(relative.parts and re.fullmatch('[A-Za-z0-9_]{1,100}', relative.parts[0]),
                    'AA_RUNTIME_SCOPE', 'Use a named financial-data scope.', 409)
            return parent.parent, relative.parts[0]
    return None


def nodes(data):
    root = Path(data) / 'aa-scopes'
    if not root.exists(): return {}
    require(root.is_dir() and not root.is_symlink(), 'AA_RUNTIME_SCOPE', 'AA scopes cannot be linked.', 409)
    result = {}
    for scope in sorted(root.iterdir()):
        require(scope.is_dir() and not scope.is_symlink() and re.fullmatch('[A-Za-z0-9_]{1,100}', scope.name),
                'AA_RUNTIME_SCOPE', 'Use ordinary named scope directories.', 409)
        profile = read_document(scope / 'profile.json')
        require(profile and profile.get('format') == 'avenra-aa-scope/1' and profile.get('classification') == 'synthetic' and
                profile.get('environment_id') == scope.name and isinstance(profile.get('peers'), dict) and profile['peers'],
                'AA_RUNTIME_SCOPE', 'Every captured scope needs its original synthetic participant profile.', 409)
        result[scope.name + '/ingress'] = scope / 'ingress/gateway.json'
        for identity in sorted(profile['peers']):
            require(re.fullmatch('[A-Za-z0-9_]{1,100}', identity) and identity != 'ingress',
                    'AA_RUNTIME_SCOPE', 'Participant identities must be bounded scope names.', 409)
            result[scope.name + '/' + identity] = scope / 'counterparties' / identity / 'peer.json'
    require(len(result) <= 32, 'AA_RUNTIME_CAPACITY', 'This recovery profile supports at most 32 AA listeners.', 409)
    return result


@contextmanager
def service_lease(config):
    path = Path(config).absolute().parent / '.service.lock'
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077 and info.st_nlink == 1,
                'AA_RUNTIME_LEASE', 'Role fences must be private ordinary single-link files.', 409)
        try: fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: require(False, 'RECOVERY_AA_ACTIVE', 'Stop every AA ingress and participant before coordinated capture or another start.', 409)
        yield
    finally:
        os.close(descriptor)


@contextmanager
def quiescent(data):
    with ExitStack() as stack:
        for config in nodes(data).values(): stack.enter_context(service_lease(config))
        yield


def launch_binding(config, recovery_data=None):
    located = location(Path(config).parent)
    if not located:
        require(recovery_data is None, 'RECOVERY_AA_BINDING', 'Recovered AA listeners must use their captured scope path.', 409)
        return None
    data, _ = located
    from ..recovery_state import INACTIVE
    from . import routing
    recovered = (data / INACTIVE).exists() or (data / 'aa-recovery-inactive.json').exists() or (data / routing.FILE).exists() or bool(
        read_document(data / 'deployment.json', {}).get('recovery', {}).get('aa_routes_sha256'))
    require(not recovered or (recovery_data is not None and Path(recovery_data).absolute() == data),
            'RECOVERY_AA_REQUIRED', 'Recovered AA listeners require explicit authenticated deployment routes; ordinary startup cannot reuse source addresses.', 409)
    if recovery_data is None: return None
    require(Path(recovery_data).absolute() == data, 'RECOVERY_AA_BINDING', 'The listener belongs to another recovered deployment.', 409)
    return routing.node_binding(data, config)


def work_allowed(directory):
    located = location(directory)
    if not located: return True
    data, _ = located
    from ..recovery_state import INACTIVE
    if (data / INACTIVE).exists() or (data / INACTIVE).is_symlink() or (data / 'aa-recovery-inactive.json').exists(): return False
    from .routing import FILE, load
    policy = read_document(data / 'deployment.json', {}).get('recovery', {})
    if (data / FILE).exists() or policy.get('aa_routes_sha256'): load(data)
    return True
