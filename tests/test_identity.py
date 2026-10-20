"""Exercise OIDC signature, claims, state and PKCE against a local test issuer."""
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.parse import parse_qs, urlsplit

from avenra.app import App
from avenra.errors import DomainError


class IdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import jwt
        from cryptography.hazmat.primitives.asymmetric import rsa
        cls.jwt = jwt
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.jwk = {**jwt.algorithms.RSAAlgorithm.to_jwk(cls.key.public_key(), as_dict=True), 'kid': 'test-key', 'alg': 'RS256', 'use': 'sig'}
        class Issuer(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'keys': [cls.jwk]}).encode())
            def do_POST(self):
                body = parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode())
                context = self.server.context
                challenge = base64.urlsafe_b64encode(hashlib.sha256(body['code_verifier'][0].encode()).digest()).rstrip(b'=').decode()
                if body.get('code') != ['test-code'] or challenge != context['challenge']:
                    self.send_error(401)
                    return
                token = cls.sign({'aud': 'avenra-portal', 'nonce': context['nonce']})
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'id_token': token}).encode())
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Issuer)
        cls.origin = 'http://127.0.0.1:' + str(cls.server.server_port)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    @classmethod
    def sign(cls, updates=None, key=None, algorithm='RS256'):
        payload = {'iss': cls.origin, 'aud': 'avenra-api', 'sub': 'test-user', 'iat': int(time.time()), 'exp': int(time.time()) + 120, 'avenra_tenant': 'tenant_avenra', 'avenra_permissions': ['read', 'write']}
        payload.update(updates or {})
        return cls.jwt.encode(payload, key or cls.key, algorithm=algorithm, headers={'kid': 'test-key'})

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = App(self.directory.name)
        self.app.bootstrap()
        self.config = {'mode': 'institutional', 'issuer': self.origin, 'jwks_url': self.origin + '/jwks', 'authorization_endpoint': self.origin + '/authorize', 'token_endpoint': self.origin + '/token', 'external_origin': 'http://127.0.0.1:4827', 'audience': 'avenra-api', 'client_id': 'avenra-portal', 'local_identity_test': True}
        self.write_config()

    def write_config(self):
        path = Path(self.directory.name) / 'identity.json'
        path.write_text(json.dumps(self.config))
        path.chmod(0o600)

    def tearDown(self):
        self.app.close()
        self.directory.cleanup()

    def test_valid_signed_token_has_workspace_and_permissions(self):
        identity = self.app.identity.verify(self.sign())
        self.assertEqual(identity['tenant_id'], 'tenant_avenra')
        self.assertEqual(identity['permissions'], ['read', 'write'])

    def test_wrong_audience_issuer_expiry_and_workspace_are_rejected(self):
        for values in [{'aud': 'other-api'}, {'iss': 'https://other.example'}, {'exp': int(time.time()) - 60}, {'avenra_tenant': 'unknown'}, {'avenra_permissions': ['all']}]:
            with self.subTest(values=values), self.assertRaises(DomainError):
                self.app.identity.verify(self.sign(values))

    def test_algorithm_confusion_and_unsigned_tokens_are_rejected(self):
        forged = self.sign(key='A-long-test-HMAC-key-that-is-not-the-issuer-key', algorithm='HS256')
        with self.assertRaises(DomainError):
            self.app.identity.verify(forged)
        unsigned = self.jwt.encode({'sub': 'attacker'}, key='', algorithm='none')
        with self.assertRaises(DomainError):
            self.app.identity.verify(unsigned)

    def test_code_flow_verifies_pkce_nonce_and_creates_scoped_session(self):
        state, redirect = self.app.identity.login()
        query = parse_qs(urlsplit(redirect).query)
        self.server.context = {'challenge': query['code_challenge'][0], 'nonce': query['nonce'][0]}
        token, age = self.app.identity.callback(state, 'test-code', state)
        self.assertGreater(age, 0)
        with self.app.store.reader() as db:
            session = db.execute('SELECT * FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        self.assertEqual(session['tenant_id'], 'tenant_avenra')
        self.assertEqual(json.loads(session['permissions_json']), ['read', 'write'])
        with self.assertRaises(DomainError):
            self.app.identity.callback(state, 'test-code', state)

    def test_login_state_cannot_be_substituted_between_browsers(self):
        state, _ = self.app.identity.login()
        with self.assertRaises(DomainError):
            self.app.identity.callback(state, 'test-code', 'other-browser-state')

    def test_remote_plaintext_identity_configuration_is_rejected(self):
        self.config.pop('local_identity_test')
        self.write_config()
        with self.assertRaises(DomainError):
            self.app.identity.configuration()


if __name__ == '__main__':
    unittest.main()
