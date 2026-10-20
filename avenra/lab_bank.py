"""Separate, scoped synthetic bank service. No real bank or payment network."""
import argparse
import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import ssl
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .errors import DomainError, require
from .gateways import AccountingGateway, EngineError
from .lab_bank_client import SCOPE_FIELDS, strict_json
from .lab_identity import CONTRACT_HASH, bank_service_lease, private_read
from .util import canonical, digest, money, now


class SyntheticBank:
    def __init__(self, config, recovery_data=None):
        self.path = Path(config).resolve()
        self.directory = self.path.parent
        if not recovery_data:
            from .recovery_state import require_active
            from .private_config import read_document
            installation = self.directory.parent.parent
            require_active(installation)
            require(not read_document(installation / 'deployment.json', {}).get('recovery'),
                    'RECOVERY_BANK_REQUIRED', 'Recovered banks require their authenticated physical routing context.', 409)
        self.configuration = json.loads(private_read(self.path))
        self.signer = Ed25519PrivateKey.from_private_bytes(private_read(self.directory / 'server-signer.raw'))
        self.endpoint = self.configuration['endpoint']
        accounting_origin = self.configuration['accounting_origin']
        if recovery_data:
            from .bank_recovery import register_process
            self.endpoint, accounting_origin = register_process(recovery_data, self.path)
        self.accounting = AccountingGateway(accounting_origin)
        self.capacity = asyncio.Semaphore(8)
        with self.database() as db:
            db.executescript("PRAGMA journal_mode=WAL; CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,request_hash TEXT NOT NULL,response_json TEXT,created_at TEXT NOT NULL); CREATE TABLE IF NOT EXISTS effects(id TEXT PRIMARY KEY,request_hash TEXT NOT NULL,document_json TEXT NOT NULL,state TEXT NOT NULL,created_at TEXT NOT NULL); CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,event TEXT NOT NULL,effect_id TEXT,details_json TEXT NOT NULL,created_at TEXT NOT NULL);")
        (self.directory / 'bank.sqlite').chmod(0o600)

    @contextmanager
    def database(self):
        db = sqlite3.connect(self.directory / 'bank.sqlite', timeout=8)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:yield db
        finally:db.close()

    def _verify(self, document, signature):
        require(isinstance(document, dict) and set(document) == {'format','transport_hash','scope','request_id','key_id','issued_at','expires_at','action','effect','lose_response'}, 'INVALID_COMMAND', 'Use the synthetic bank command contract.')
        require(document['format'] == 'avenra-bank-command/1' and document['transport_hash'] == CONTRACT_HASH and type(document['lose_response']) is bool, 'PROFILE_UNSUPPORTED', 'Synthetic bank command version differs.')
        configuration = json.loads(private_read(self.path))
        require(isinstance(document['key_id'], str) and document['key_id'] in configuration['clients'], 'SIGNING_KEY_UNKNOWN', 'Unknown synthetic signing identity.', 403)
        client = configuration['clients'][document['key_id']]
        require(not client['revoked'], 'SIGNING_KEY_REVOKED', 'Synthetic signing identity is revoked.', 403)
        require(isinstance(signature, str) and re.fullmatch('[0-9a-f]{128}', signature), 'SIGNATURE_INVALID', 'Invalid synthetic signature.', 403)
        try:
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(client['public_key'])).verify(bytes.fromhex(signature), canonical(document).encode())
        except (ValueError, InvalidSignature):
            raise DomainError('SIGNATURE_INVALID', 'Synthetic request signature is invalid.', 403)
        expected = {k: configuration[k] for k in SCOPE_FIELDS if k not in {'role', 'participant_id'}}
        expected.update(role=client['role'], participant_id=client['participant_id'])
        require(document['scope'] == expected, 'BANK_SCOPE', 'Request does not belong to this synthetic bank and role.', 403)
        issued, expires = document['issued_at'], document['expires_at']
        require(type(issued) is int and type(expires) is int and 0 < expires-issued <= 30 and issued <= time.time()+5 and time.time() < expires, 'COMMAND_EXPIRED', 'Request is outside the synthetic validity interval.', 409)
        require(isinstance(document['request_id'], str) and re.fullmatch('command_[0-9a-f]{20}', document['request_id']), 'INVALID_COMMAND', 'Invalid transport identity.')
        require(isinstance(document['action'], str) and document['action'] in {'probe','status','post'}, 'INVALID_COMMAND', 'Unsupported synthetic command.')
        require(not document['lose_response'] or document['action'] == 'post', 'INVALID_COMMAND', 'Response loss applies only to a financial dispatch.')
        return configuration

    def _effect(self, document, configuration):
        effect = document['effect']
        require(isinstance(effect, dict) and set(effect) == {'operation_id','leg_id','leg_kind','body'}, 'INVALID_EFFECT', 'Use the exact synthetic financial leg contract.')
        operation, kind = effect['operation_id'], effect['leg_kind']
        require(isinstance(operation, str) and re.fullmatch('op_[0-9a-f]{20}', operation) and isinstance(kind, str) and kind in {'debit','credit','reversal'}, 'INVALID_EFFECT', 'Invalid synthetic financial identity.')
        expected_id = 'leg_' + hashlib.sha256((operation + ':' + kind).encode()).hexdigest()[:32]
        require(effect['leg_id'] == expected_id, 'INVALID_EFFECT', 'Financial identity does not match the operation and leg.')
        body = effect['body']
        require(isinstance(body, dict) and set(body) == {'postings','reference','metadata'} and body['reference'] == expected_id, 'INVALID_EFFECT', 'Invalid synthetic posting request.')
        postings = body['postings']
        require(isinstance(postings, list) and len(postings) == 1 and isinstance(postings[0], dict) and set(postings[0]) == {'source','destination','amount','asset'}, 'INVALID_EFFECT', 'Exactly one financial posting is required.')
        posting = postings[0]
        money(posting['amount'])
        require(posting['asset'] == 'INR/2', 'INVALID_EFFECT', 'Synthetic bank currency must match its profile.')
        suspense = 'clearing:' + operation
        role = document['scope']['role']
        valid = ((kind == 'debit' and role == 'remitter' and posting['source'] in configuration['account_addresses'] and posting['destination'] == suspense) or
                 (kind == 'reversal' and role == 'remitter' and posting['destination'] in configuration['account_addresses'] and posting['source'] == suspense) or
                 (kind == 'credit' and role == 'beneficiary' and posting['source'] == suspense and posting['destination'] in configuration['account_addresses']))
        require(valid, 'BANK_ACCOUNT_SCOPE', 'Financial leg is outside the synthetic bank account or role scope.', 403)
        metadata = body['metadata']
        require(isinstance(metadata, dict) and set(metadata) == {'operation_id','leg','profile'} and metadata['operation_id'] == operation and metadata['leg'] == kind and isinstance(metadata['profile'], str) and len(metadata['profile']) <= 100, 'INVALID_EFFECT', 'Financial metadata differs from the operation.')
        return effect

    def _financial(self, document, configuration):
        effect = self._effect(document, configuration)
        identity, fingerprint = effect['leg_id'], digest(effect)
        with self.database() as db:
            existing = db.execute('SELECT * FROM effects WHERE id=?', (identity,)).fetchone()
            require(not existing or existing['request_hash'] == fingerprint, 'FINANCIAL_CONFLICT', 'Financial identity already has different immutable content.', 409)
            if document['action'] == 'post':
                db.execute("INSERT OR IGNORE INTO effects VALUES(?,?,?,'unknown',?)", (identity, fingerprint, canonical(effect), now()))
                stored = db.execute('SELECT request_hash FROM effects WHERE id=?', (identity,)).fetchone()
                require(stored['request_hash'] == fingerprint, 'FINANCIAL_CONFLICT', 'A concurrent request has different financial content.', 409)
        # The native journal owns money. This database records commands only.
        observed = self.accounting.transactions(configuration['ledger_name'], identity)
        require(len(observed) <= 1 and all(x['postings'] == effect['body']['postings'] for x in observed), 'FINANCIAL_INVARIANT', 'Native journal differs from the immutable synthetic bank leg.', 409)
        if document['action'] == 'status':
            return {'transactions': observed}
        if observed:
            transaction = observed[0]
        else:
            if existing and existing['state'] == 'declined':
                raise EngineError(409, 'INSUFFICIENT_FUND', 'Previously declined synthetic bank leg.')
            body = {**effect['body'], 'metadata': {**effect['body']['metadata'], 'synthetic_bank_worker': configuration['worker_id']}}
            try:
                transaction = self.accounting.post(configuration['ledger_name'], body, identity)
            except EngineError as error:
                if error.code == 'INSUFFICIENT_FUND':
                    with self.database() as db:db.execute("UPDATE effects SET state='declined' WHERE id=?", (identity,))
                raise
        with self.database() as db:
            db.execute("UPDATE effects SET state='confirmed' WHERE id=?", (identity,))
            db.execute('INSERT INTO events(event,effect_id,details_json,created_at) VALUES(?,?,?,?)', ('financial.confirmed', identity, canonical({'native_id': str(transaction['id'])}), now()))
        return {'transaction': transaction}

    def command(self, wrapper):
        require(isinstance(wrapper, dict) and set(wrapper) == {'document','signature'}, 'INVALID_COMMAND', 'Use a signed synthetic command.')
        document = wrapper['document']
        require(isinstance(document, dict), 'INVALID_COMMAND', 'Use an object command.')
        response, status = None, 200
        try:
            configuration = self._verify(document, wrapper['signature'])
            fingerprint = digest(document)
            with self.database() as db:
                existing = db.execute('SELECT * FROM requests WHERE id=?', (document['request_id'],)).fetchone()
                require(not existing or existing['request_hash'] == fingerprint, 'COMMAND_CONFLICT', 'Transport identity has different signed content.', 409)
                if existing and existing['response_json']:
                    return 200, json.loads(existing['response_json']), False
                db.execute('INSERT OR IGNORE INTO requests VALUES(?,?,NULL,?)', (document['request_id'], fingerprint, now()))
                stored = db.execute('SELECT request_hash FROM requests WHERE id=?', (document['request_id'],)).fetchone()
                require(stored['request_hash'] == fingerprint, 'COMMAND_CONFLICT', 'A concurrent transport request has different signed content.', 409)
            if document['action'] == 'probe':
                require(document['effect'] is None, 'INVALID_COMMAND', 'A probe cannot include a financial operation.')
                result = {'worker_id': configuration['worker_id'], 'transport_hash': CONTRACT_HASH, 'role': document['scope']['role'], 'official_conformance': False, 'financial_side_effects': False}
            else:
                result = self._financial(document, configuration)
            response = {'ok': True, 'result': result}
        except DomainError as error:
            status, response = error.status, {'ok': False, 'error_code': error.code}
        except EngineError as error:
            status, response = (409 if error.code == 'INSUFFICIENT_FUND' else 503), {'ok': False, 'error_code': error.code if error.code == 'INSUFFICIENT_FUND' else 'OUTCOME_UNKNOWN'}
        response.update(request_hash=digest(document), request_id=document.get('request_id'), scope=document.get('scope'), transport_hash=CONTRACT_HASH)
        result = {'document': response, 'signature': self.signer.sign(canonical(response).encode()).hex()}
        if response['ok']:
            with self.database() as db:db.execute('UPDATE requests SET response_json=? WHERE id=? AND response_json IS NULL', (canonical(result), document['request_id']))
        return status, result, bool(response['ok'] and document['lose_response'])

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            while True:
                event = await receive()
                if event['type'] == 'lifespan.startup':await send({'type':'lifespan.startup.complete'})
                elif event['type'] == 'lifespan.shutdown':
                    await send({'type':'lifespan.shutdown.complete'});return
        if scope['type'] != 'http':return
        status, result, drop = 400, {'error':'Invalid synthetic bank request'}, False
        headers = scope['headers']
        lengths = [v for k,v in headers if k == b'content-length']
        valid = scope['method'] == 'POST' and scope['path'] == '/v1/commands' and not scope['query_string'] and len(lengths) == 1 and len(lengths[0]) <= 5 and lengths[0].isdigit() and int(lengths[0]) <= 32768 and not any(k == b'transfer-encoding' for k,v in headers)
        if valid and not self.capacity.locked():
            async with self.capacity:
                try:
                    body, deadline = bytearray(), time.monotonic()+5
                    while True:
                        event = await asyncio.wait_for(receive(), max(.001, deadline-time.monotonic()))
                        if event['type'] == 'http.disconnect':return
                        body.extend(event.get('body', b''))
                        if len(body)>32768:raise ValueError('body limit')
                        if not event.get('more_body'):break
                    if len(body)!=int(lengths[0]):raise ValueError('framing')
                    status, result, drop = await asyncio.to_thread(self.command, strict_json(body))
                except (ValueError, TypeError, KeyError, DomainError):pass
                except TimeoutError:status=408
        elif valid:status=503
        encoded = canonical(result).encode()
        await send({'type':'http.response.start','status':status,'headers':[(b'content-type',b'application/json'),(b'content-length',str(len(encoded)).encode()),(b'cache-control',b'no-store'),(b'x-content-type-options',b'nosniff')]})
        # A deliberate lab fault cuts real response bytes only after commit and
        # retained response. The server closes this incomplete response.
        await send({'type':'http.response.body','body':b'' if drop else encoded})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--recovery-data')
    args = parser.parse_args()
    import uvicorn
    with bank_service_lease(Path(args.config).resolve().parent):
        bank = SyntheticBank(args.config, args.recovery_data)
        port = int(bank.endpoint.rsplit(':',1)[1])
        uvicorn.run(bank, host='127.0.0.1', port=port, ssl_certfile=str(bank.directory/'server.pem'), ssl_keyfile=str(bank.directory/'server-key.pem'),
                    ssl_ca_certs=str(bank.directory/'ca.pem'), ssl_cert_reqs=ssl.CERT_REQUIRED, limit_concurrency=16, timeout_keep_alive=3,
                    timeout_graceful_shutdown=12, server_header=False, access_log=False, log_level='warning')


if __name__ == '__main__':main()
