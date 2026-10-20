"""Observe real issuer lifetime ownership and copied-credential startup refusal."""
import hashlib
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import os

from avenra.deployment_backup import snapshot, restore
from avenra.errors import DomainError
from avenra.gateways import EngineError
from avenra.issuer_runtime import LEASE, service_lease
from avenra.private_config import write_document
from avenra.recovery_state import INACTIVE
from avenra.recovery_set import copy_private
from avenra.services import Transport
from avenra.synthetic_identity import Issuer, provision

ROOT = Path(__file__).resolve().parents[1]


def port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


class IssuerRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.data = self.root / 'source'
        self.data.mkdir(mode=0o700)

    def profile(self):
        return provision(self.data, port(), 'https://127.0.0.1:' + str(port()))

    def test_real_issuer_owns_the_lease_until_actual_process_death(self):
        profile = self.profile()
        log = self.root / 'issuer.log'
        command = [sys.executable, '-B', '-m', 'avenra.synthetic_identity', '--config', profile['config']]
        with log.open('xb') as output:
            log.chmod(0o600)
            child = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                     stdout=output, stderr=subprocess.STDOUT)
        def reap():
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
        self.addCleanup(reap)
        deadline = time.monotonic() + 15
        ready = False
        while time.monotonic() < deadline:
            self.assertIsNone(child.poll(), log.read_text())
            try:
                keys = Transport(profile['issuer'], ca_file=profile['ca_file']).call('GET', '/jwks', timeout=1)
                ready = keys['keys'][0]['kid'] == 'synthetic-issuer-k1'
                if ready:
                    break
            except EngineError:
                pass
            time.sleep(.05)
        self.assertTrue(ready, log.read_text())
        with self.assertRaises(DomainError) as rejected:
            with service_lease(self.data):
                self.fail('A serving issuer allowed a cold checkpoint fence.')
        self.assertEqual(rejected.exception.code, 'RECOVERY_IDENTITY_ACTIVE')
        duplicate = subprocess.run(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                   capture_output=True, text=True, timeout=10)
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertIn('RECOVERY_IDENTITY_ACTIVE', duplicate.stderr)
        self.assertIsNone(child.poll())
        child.kill()
        child.wait(timeout=5)
        with service_lease(self.data):
            pass

    def test_cold_fence_also_refuses_provisioning_without_changing_original_keys(self):
        profile = self.profile()
        key = Path(profile['directory']) / 'signing-key.pem'
        before = key.read_bytes()
        with service_lease(self.data):
            with self.assertRaises(DomainError) as rejected:
                self.profile()
        self.assertEqual(rejected.exception.code, 'RECOVERY_IDENTITY_ACTIVE')
        self.assertEqual(key.read_bytes(), before)

    def test_inactive_and_activated_copies_cannot_start_the_original_issuer(self):
        profile = self.profile()
        Issuer(profile['config'])
        stage = self.root / 'stage'
        stage.mkdir(mode=0o700)
        data = stage / 'data'
        previous = os.umask(0o022)
        try:
            snapshot(self.data, self.root / 'checkpoint')
            restore(self.root / 'checkpoint', data)
        finally:
            os.umask(previous)
        self.assertTrue(all(not path.stat().st_mode & 0o077 for path in [data, *data.rglob('*')] if path.is_dir()))
        config = data / Path(profile['config']).relative_to(self.data)
        def hashes():
            return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in config.parent.iterdir()}
        before = hashes()
        with self.assertRaises(DomainError) as rejected:
            Issuer(config)
        self.assertEqual(rejected.exception.code, 'RECOVERY_INACTIVE')
        write_document(stage / 'recovery-stage.json', {'format': 'avenra-recovery-stage/1'})
        (data / INACTIVE).unlink()
        with self.assertRaises(DomainError) as rejected:
            Issuer(config)
        self.assertEqual(rejected.exception.code, 'RECOVERY_IDENTITY_REQUIRED')
        self.assertEqual(hashes(), before)
        self.assertFalse((data / LEASE).exists())

    def test_private_copy_creates_each_parent_privately_and_refuses_unsafe_existing_anchors(self):
        source = self.root / 'payload'
        source.write_bytes(b'private input')
        source.chmod(0o600)
        target = self.data / 'nested/private/payload'
        previous = os.umask(0o022)
        try:
            copy_private(source, target)
        finally:
            os.umask(previous)
        self.assertEqual(target.read_bytes(), b'private input')
        self.assertTrue(all(not p.stat().st_mode & 0o077 for p in [target, target.parent, target.parent.parent]))
        public = self.data / 'public'
        public.mkdir(mode=0o755)
        public.chmod(0o755)
        with self.assertRaises(DomainError):
            copy_private(source, public / 'payload')
        self.assertEqual(public.stat().st_mode & 0o777, 0o755)
        external = self.root / 'external'
        external.mkdir(mode=0o700)
        linked = self.data / 'linked'
        linked.symlink_to(external, target_is_directory=True)
        with self.assertRaises(DomainError):
            copy_private(source, linked / 'nested/payload')
        self.assertFalse((external / 'nested').exists())

    def test_linked_or_public_lease_state_is_refused_without_touching_its_target(self):
        outside = self.root / 'outside'
        outside.write_bytes(b'unchanged')
        outside.chmod(0o600)
        lock = self.data / LEASE
        lock.symlink_to(outside)
        with self.assertRaises(OSError):
            with service_lease(self.data):
                pass
        lock.unlink()
        os.link(outside, lock)
        with self.assertRaises(DomainError) as rejected:
            with service_lease(self.data):
                pass
        self.assertEqual(rejected.exception.code, 'ISSUER_RUNTIME_LEASE')
        lock.unlink()
        lock.write_bytes(b'')
        lock.chmod(0o644)
        with self.assertRaises(DomainError):
            with service_lease(self.data):
                pass
        self.assertEqual(outside.read_bytes(), b'unchanged')


if __name__ == '__main__':
    unittest.main()
