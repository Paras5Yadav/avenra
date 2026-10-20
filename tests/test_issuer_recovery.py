"""Exercise immutable issuer routes and refusal of old callback bindings."""
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import tempfile
import time
import unittest
from urllib.parse import parse_qs, urlsplit

from avenra import issuer_recovery as routing
from avenra.deployment_identity import issue, paths
from avenra.errors import DomainError
from avenra.lab_identity import private_write
from avenra.private_config import read_document, write_document, deployment
from avenra.recovery_state import INACTIVE
from avenra.release_bundle import sha
from avenra.synthetic_identity import Issuer, provision
from avenra.util import digest


def port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


class IssuerRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.data = self.root / 'data'
        self.data.mkdir(mode=0o700)
        self.profile = provision(self.data, port(), 'https://127.0.0.1:' + str(port()))
        self.original = Issuer(self.profile['config'])
        self.portal = issue(self.data, 'portal')
        self.identity = {'mode': 'institutional', 'issuer': self.profile['issuer'], 'authorization_endpoint': self.profile['issuer'] + '/authorize',
                         'token_endpoint': self.profile['issuer'] + '/token', 'jwks_url': self.profile['issuer'] + '/jwks',
                         'external_origin': self.original.config['redirect_uri'].removesuffix('/auth/callback'), 'ca_file': self.profile['ca_file'],
                         'client_id': self.profile['client_id'], 'client_secret': self.profile['client_secret'], 'audience': self.profile['audience']}
        write_document(self.data / 'identity.json', self.identity)
        self.policy = {'format': 'avenra-deployment/1', 'http': {'tls_identity': self.portal['reference']},
                       'identity': {'granular': True, 'require_session_subject': True, 'max_session_seconds': 120}}
        write_document(self.data / 'deployment.json', self.policy)
        private_write(self.data / 'evidence.key', os.urandom(32))
        for name in ['control.dsn', 'migration.dsn', 'accounting.dsn']:
            private_write(self.data / name, 'private-target:' + name)
        write_document(self.data / INACTIVE, {'state': 'staging'})
        folders = {Path(self.profile['directory']), Path(self.portal['directory'])}
        files = {'identity.json': sha(self.data / 'identity.json')}
        files.update({p.relative_to(self.data).as_posix(): sha(p) for folder in folders for p in folder.iterdir() if p.suffix != '.db'})
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        def certificate(reference):
            return x509.load_pem_x509_certificate(paths(self.data, reference)['cert'].read_bytes()).fingerprint(hashes.SHA256()).hex()
        self.routes = {'format': routing.FORMAT, 'data_directory': str(self.data), 'set_id': 'a' * 64, 'artifact_id': 'b' * 64,
                       'request_id': 'e21cd0a5-7905-4a39-802e-184c314d4c3f', 'actor': 'unit-verifier', 'intent_hash': 'c' * 64,
                       'created_at': time.time(), 'accounting_origin': 'http://127.0.0.1:5001', 'fault_origin': 'http://127.0.0.1:5002',
                       'database_hashes': {name: sha(self.data / name) for name in ['control.dsn', 'migration.dsn', 'accounting.dsn']},
                       'config': Path(self.profile['config']).relative_to(self.data).as_posix(), 'issuer': self.profile['issuer'],
                       'issuer_origin': 'https://127.0.0.1:' + str(port()), 'api_origin': 'https://127.0.0.1:' + str(port()),
                       'source_origins': sorted([self.identity['issuer'], self.identity['external_origin']]), 'http_policy': self.policy['http'],
                       'identity_policy': self.policy['identity'], 'files': files, 'jwk_sha256': digest(self.original.jwk),
                       'issuer_certificate_sha256': certificate(self.profile['identity_reference']), 'api_certificate_sha256': certificate(self.portal['reference']),
                       'process_executable': '/private/verified/python', 'process_executable_sha256': 'd' * 64, 'process_command_prefix': '/private/verified/python'}
        self.write_routes()

    def write_routes(self):
        routing.signed_write(self.data, self.data / routing.FILE, self.routes)

    def activate(self):
        write_document(self.data / 'deployment.json', {**self.policy, 'recovery': {'set_id': self.routes['set_id'], 'issuer_routes_sha256': sha(self.data / routing.FILE)}})
        (self.data / INACTIVE).unlink()

    def query(self, redirect=None):
        self.verifier = secrets.token_urlsafe(48)
        return {'response_type': 'code', 'client_id': self.profile['client_id'],
                'redirect_uri': redirect or self.original.config['redirect_uri'], 'scope': 'openid profile', 'state': secrets.token_urlsafe(32),
                'nonce': secrets.token_urlsafe(32), 'code_challenge_method': 'S256',
                'code_challenge': base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).rstrip(b'=').decode()}

    def sign_in(self, issuer, query):
        import re
        _, page, headers = issuer.authorize(query)
        interaction = re.search('name="interaction" value="([A-Za-z0-9_-]+)"', page.decode()).group(1)
        form = {'interaction': interaction, 'username': 'operator', 'password': self.profile['passwords']['operator']}
        result = issuer.login(form, headers['set-cookie'])
        return result, form, headers['set-cookie']

    def token_form(self, result, redirect):
        return {'grant_type': 'authorization_code', 'client_id': self.profile['client_id'], 'client_secret': self.profile['client_secret'],
                'redirect_uri': redirect, 'code_verifier': self.verifier, 'code': parse_qs(urlsplit(result[2]['location']).query)['code'][0]}

    def test_signed_route_target_and_all_immutable_identity_bytes_are_checked(self):
        self.assertEqual(routing.resolve(self.data), self.routes)
        changed = json.loads((self.data / routing.FILE).read_bytes())
        changed['issuer_origin'] = 'https://127.0.0.1:' + str(port())
        write_document(self.data / routing.FILE, changed)
        with self.assertRaises(DomainError) as refused:
            routing.resolve(self.data)
        self.assertEqual(refused.exception.code, 'RECOVERY_IDENTITY_AUTHENTICATION')
        self.write_routes()
        path = Path(self.profile['directory']) / 'signing-key.pem'
        before = path.read_bytes()
        path.write_bytes(before + b'\n')
        with self.assertRaises(DomainError) as refused:
            routing.resolve(self.data)
        self.assertEqual(refused.exception.code, 'RECOVERY_IDENTITY_BINDING')
        path.write_bytes(before)
        self.routes['data_directory'] = str(self.root / 'other')
        self.write_routes()
        with self.assertRaises(DomainError):
            routing.resolve(self.data)

    def test_staging_allows_proofs_but_no_authorization_or_token_issuance(self):
        recovered = Issuer(self.profile['config'], self.data)
        proof = recovered.recovery_probe({b'x-avenra-recovery-challenge': secrets.token_urlsafe(32).encode()})
        import jwt
        claims = jwt.decode(proof['proof'], recovered.key.public_key(), algorithms=['RS256'], issuer=self.profile['issuer'], audience='avenra-recovery-probe')
        self.assertEqual(claims['set_id'], self.routes['set_id'])
        for operation in [lambda: recovered.authorize(self.query()), lambda: recovered.login({}, ''), lambda: recovered.token({})]:
            with self.assertRaises(DomainError) as refused:
                operation()
            self.assertEqual(refused.exception.code, 'RECOVERY_INACTIVE')

    def test_old_code_and_interaction_are_never_rebound_to_new_callback(self):
        query = self.query()
        result, _, _ = self.sign_in(self.original, query)
        old_form = self.token_form(result, query['redirect_uri'])
        old_interaction = self.original.authorize(self.query())
        import re
        interaction = re.search('name="interaction" value="([A-Za-z0-9_-]+)"', old_interaction[1].decode()).group(1)
        recovered = Issuer(self.profile['config'], self.data)
        self.activate()
        with self.assertRaises(DomainError) as refused:
            recovered.token(old_form)
        self.assertEqual(refused.exception.code, 'INVALID_GRANT')
        with self.assertRaises(DomainError) as refused:
            recovered.login({'interaction': interaction, 'username': 'operator', 'password': self.profile['passwords']['operator']}, old_interaction[2]['set-cookie'])
        self.assertEqual(refused.exception.code, 'LOGIN_BINDING')
        with recovered.db() as db:
            self.assertEqual(db.execute('SELECT used FROM codes WHERE code_hash=?', (hashlib.sha256(old_form['code'].encode()).hexdigest(),)).fetchone()[0], 0)
        query = self.query(self.routes['api_origin'] + '/auth/callback')
        result, _, _ = self.sign_in(recovered, query)
        form = self.token_form(result, query['redirect_uri'])
        tokens = recovered.token(form)
        import jwt
        claims = jwt.decode(tokens['access_token'], recovered.key.public_key(), algorithms=['RS256'], issuer=self.profile['issuer'], audience=self.profile['audience'])
        self.assertEqual(claims['sub'], 'synthetic-operator')
        self.assertLessEqual(claims['exp'] - claims['iat'], self.original.config['token_seconds'])
        with self.assertRaises(DomainError):
            recovered.token(form)

    def test_active_route_loss_policy_change_and_wrong_callback_listener_refuse(self):
        self.activate()
        routing.api_binding(self.data, '127.0.0.1', urlsplit(self.routes['api_origin']).port)
        with self.assertRaises(DomainError):
            routing.api_binding(self.data, '127.0.0.1', port())
        original = (self.data / routing.FILE).read_bytes()
        (self.data / routing.FILE).unlink()
        for callback in [lambda: routing.resolve(self.data), lambda: deployment(self.data), lambda: routing.configuration(self.data, self.identity)]:
            with self.assertRaises(DomainError):
                callback()
        private_write(self.data / routing.FILE, original)
        changed = read_document(self.data / 'deployment.json')
        changed['identity']['max_session_seconds'] = 240
        write_document(self.data / 'deployment.json', changed)
        with self.assertRaises(DomainError):
            routing.resolve(self.data)

    def test_recovery_discovery_is_explicitly_unavailable_at_replacement_origin(self):
        issuer = Issuer(self.profile['config'], self.data)
        events = []
        async def receive():
            return {'type': 'http.request', 'body': b''}
        async def send(event):
            events.append(event)
        asyncio.run(issuer({'type': 'http', 'scheme': 'https', 'headers': [], 'path': '/.well-known/openid-configuration', 'method': 'GET'}, receive, send))
        self.assertEqual(events[0]['status'], 503)
        self.assertEqual(json.loads(events[1]['body'])['error'], 'RECOVERY_IDENTITY_STATIC')

    def test_service_log_refuses_links_and_public_files_without_changing_their_targets(self):
        outside = self.root / 'outside'
        private_write(outside, b'original private bytes')
        log = Path(self.profile['directory']) / 'recovery-service.log'
        os.link(outside,log)
        with self.assertRaises(DomainError):
            with routing.service_log(self.data,log) as stream:
                stream.write(b'forbidden append')
        log.unlink()
        log.symlink_to(outside)
        with self.assertRaises(OSError):
            with routing.service_log(self.data,log):
                self.fail('Linked log was accepted.')
        log.unlink()
        log.write_bytes(b'public log')
        log.chmod(0o644)
        with self.assertRaises(DomainError):
            with routing.service_log(self.data,log):
                self.fail('Public log was accepted.')
        self.assertEqual(outside.read_bytes(),b'original private bytes')
        log.unlink()
        with routing.service_log(self.data,log) as stream:
            stream.write(b'private service record')
        self.assertEqual(log.read_bytes(),b'private service record')
        self.assertEqual(log.stat().st_mode & 0o777,0o600)


if __name__ == '__main__':
    unittest.main()
