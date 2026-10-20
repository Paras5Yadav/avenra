"""Lifetime fence and inactive-startup guard for the owned synthetic issuer.

The current fixture profile has one issuer per deployment. Its launcher and
provisioner cooperate with cold capture through one installation-wide lease.
This does not fence arbitrary older or nonparticipating identity providers.
"""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re
import stat

from .errors import require
from .private_config import read_document
from .recovery_state import require_active

LEASE = 'issuer-service.lock'
FORMAT = 'avenra-synthetic-issuer-runtime/1'


def private_directory(directory):
    directory = Path(directory).absolute()
    info = directory.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077,
            'ISSUER_RUNTIME_PATH', 'Synthetic issuer state requires ordinary private deployment directories.', 409)
    return directory


def configuration_location(config):
    config = Path(config).absolute()
    require(config.name == 'issuer.json' and config.parent.parent.name == 'deployment-secrets'
            and re.fullmatch('[A-Za-z0-9_-]{1,80}', config.parent.name) and '..' not in config.parts,
            'ISSUER_RUNTIME_PATH', 'Use the issuer configuration inside its original deployment identity directory.', 409)
    return config.parent.parent.parent


def configuration_directory(config):
    config = Path(config).absolute()
    data = private_directory(configuration_location(config))
    private_directory(config.parent.parent)
    private_directory(config.parent)
    return data


def configurations(data):
    """Find declared owned issuers without constructing or starting them."""
    root = Path(data).absolute() / 'deployment-secrets'
    if not root.exists() and not root.is_symlink():
        return []
    private_directory(root)
    result = []
    for folder in sorted(root.iterdir()):
        private_directory(folder)
        config = folder / 'issuer.json'
        if not config.exists() and not config.is_symlink():
            continue
        configuration_directory(config)
        value = read_document(config)
        require(value and value.get('format') == 'avenra-synthetic-issuer/1'
                and value.get('classification') == 'synthetic_counterparty'
                and value.get('identity_reference') == 'secret://' + folder.name,
                'ISSUER_CONFIGURATION', 'Captured issuers must retain their declared synthetic identity binding.', 409)
        result.append(config)
    require(len(result) <= 16, 'ISSUER_RUNTIME_CAPACITY', 'The captured issuer inventory exceeds this local profile.', 409)
    return result


def require_startup(data):
    data = Path(data).absolute()
    require_active(data)
    policy = read_document(data / 'deployment.json', {}).get('recovery', {})
    marker = data.parent / 'recovery-stage.json'
    require(isinstance(policy, dict) and not policy and not marker.exists() and not marker.is_symlink(),
            'RECOVERY_IDENTITY_REQUIRED',
            'Recovered synthetic issuer credentials require coordinated identity bindings before serving. Preserve the captured identities.', 409)


@contextmanager
def service_lease(data):
    """Hold through provisioning, the whole listener lifetime or cold capture."""
    data = private_directory(data)
    descriptor = os.open(data / LEASE, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077
                and info.st_nlink == 1,
                'ISSUER_RUNTIME_LEASE', 'Issuer fences must be private ordinary single-link files.', 409)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            require(False, 'RECOVERY_IDENTITY_ACTIVE',
                    'Stop the synthetic identity issuer and finish provisioning before cold capture or another start.', 409)
        yield
    finally:
        os.close(descriptor)
