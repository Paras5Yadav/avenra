"""Strict JWS and X25519/HKDF/AES-GCM for the selected synthetic AA profile."""
import base64
import hashlib
import json
import secrets

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa, x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..errors import DomainError, require
from ..util import canonical
from .contracts import stamp, timestamp


def b64(value): return base64.b64encode(value).decode()
def b64u(value): return base64.urlsafe_b64encode(value).rstrip(b'=').decode()


def decode64(value, url=False, limit=2_000_000):
    require(isinstance(value, str) and len(value) <= limit, 'InvalidKey', 'Invalid bounded base64 value.')
    try:
        result = base64.b64decode(value + ('=' * (-len(value) % 4) if url else ''), altchars=b'-_' if url else None, validate=True)
    except (ValueError, UnicodeError): raise DomainError('InvalidKey', 'Invalid base64 encoding.')
    require((b64u if url else b64)(result) == value, 'InvalidKey', 'Use canonical base64 encoding.')
    return result


def document(raw):
    require(isinstance(raw, bytes) and len(raw) <= 2_000_000, 'InvalidRequest', 'Use a bounded JSON message.')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'InvalidRequest', 'Repeated JSON fields are refused.')
            result[key] = value
        return result
    try: result = json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError): raise DomainError('InvalidRequest', 'Invalid financial-data JSON.')
    require(isinstance(result, dict), 'InvalidRequest', 'Use a JSON object.')
    return result


def sign(raw, key, kid, detached=True, unencoded=False):
    header = {'alg': 'RS256', 'kid': kid}
    if unencoded: header.update(b64=False, crit=['b64'])
    protected = b64u(canonical(header).encode())
    payload = raw if unencoded else b64u(raw).encode()
    signature = key.sign(protected.encode() + b'.' + payload, padding.PKCS1v15(), hashes.SHA256())
    require(detached or not unencoded, 'InvalidRequest', 'Attached consent signatures use encoded JWS.')
    return protected + '.' + ('' if detached else payload.decode()) + '.' + b64u(signature)


def verify(raw, signature, keys, detached=True):
    require(isinstance(signature, str) and len(signature) <= 32768, 'SignatureDoesNotMatch', 'A bounded registered JWS is required.', 401)
    parts = signature.split('.')
    require(len(parts) == 3 and (not parts[1] if detached else bool(parts[1])), 'SignatureDoesNotMatch', 'Use the selected JWS serialization.', 401)
    header = document(decode64(parts[0], True))
    require(set(header) <= {'alg', 'kid', 'b64', 'crit', 'typ'} and header.get('alg') == 'RS256' and header.get('kid') in keys, 'SignatureDoesNotMatch', 'JWS authority or algorithm is not registered.', 401)
    require(type(header.get('b64', True)) is bool, 'SignatureDoesNotMatch', 'Invalid JWS encoding policy.', 401)
    unencoded = header.get('b64') is False
    require(header.get('crit', []) == (['b64'] if unencoded else []) and (detached or not unencoded), 'SignatureDoesNotMatch', 'Unsupported critical JWS extension.', 401)
    key = keys[header['kid']]
    require(isinstance(key, rsa.RSAPublicKey) and key.key_size >= 2048, 'InvalidKey', 'Use a registered RSA signing key.')
    if not detached: raw = decode64(parts[1], True)
    payload = raw if unencoded else b64u(raw).encode()
    try: key.verify(decode64(parts[2], True), parts[0].encode() + b'.' + payload, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature: raise DomainError('SignatureDoesNotMatch', 'Message signature verification failed.', 401)
    return raw, header['kid']


def signing_key(): return rsa.generate_private_key(public_exponent=65537, key_size=2048)
def pem(key): return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
def public_pem(key): return key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def ephemeral(expiry):
    key = x25519.X25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()), {
        'cryptoAlg': 'ECDH', 'curve': 'X25519', 'params': 'cipher=AES/GCM/NoPadding;KeyPairGenerator=ECDH',
        'DHPublicKey': {'expiry': stamp(expiry), 'Parameters': '', 'KeyValue': b64(public)}, 'Nonce': b64(secrets.token_bytes(32))}


def material(value, current):
    require(isinstance(value, dict) and set(value) <= {'cryptoAlg', 'curve', 'params', 'DHPublicKey', 'Nonce'} and value.get('cryptoAlg') == 'ECDH' and value.get('curve') == 'X25519', 'InvalidKey', 'This selected profile requires ECDH/X25519.')
    require(value.get('params', '') in {'', 'cipher=AES/GCM/NoPadding;KeyPairGenerator=ECDH', 'AES/GCM/NoPadding'}, 'InvalidKey', 'Unsupported cipher parameters.')
    public = value.get('DHPublicKey', {})
    require(isinstance(public, dict) and set(public) <= {'expiry', 'Parameters', 'KeyValue'} and public.get('Parameters', '') == '', 'InvalidKey', 'Unsupported DH parameters.')
    require(timestamp(public.get('expiry')) > current, 'InvalidKey', 'Ephemeral public key has expired.')
    encoded = decode64(public.get('KeyValue'));nonce = decode64(value.get('Nonce'))
    require(len(nonce) == 32 and len(encoded) in {32, 44}, 'InvalidKey', 'Use a 256-bit nonce and registered X25519 public key.')
    try: key = x25519.X25519PublicKey.from_public_bytes(encoded) if len(encoded) == 32 else serialization.load_der_public_key(encoded)
    except ValueError: raise DomainError('InvalidKey', 'Invalid X25519 public key encoding.')
    require(isinstance(key, x25519.X25519PublicKey), 'InvalidKey', 'DH public key is not X25519.')
    try: x25519.X25519PrivateKey.generate().exchange(key)
    except ValueError: raise DomainError('InvalidKey','Low-order DH public keys are refused.')
    return key, nonce


def derive(private, own_nonce, remote, current):
    public, remote_nonce = material(remote, current)
    nonce = decode64(own_nonce);require(len(nonce) == 32 and len(private) == 32, 'InvalidKey', 'Invalid local ephemeral material.')
    try: secret = x25519.X25519PrivateKey.from_private_bytes(private).exchange(public)
    except ValueError: raise DomainError('InvalidKey', 'Invalid all-zero or low-order DH exchange.')
    mixed = bytes(a ^ b for a, b in zip(nonce, remote_nonce))
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=mixed[:20], info=b'').derive(secret)
    return key, mixed[20:]


def encrypt(xml, recipient, expiry, current):
    private, own = ephemeral(expiry);key, iv = derive(private, own['Nonce'], recipient, current)
    return b64(AESGCM(key).encrypt(iv, xml, None)), own


def decrypt(ciphertext, private, local, remote, current):
    material(local, current);key, iv = derive(private, local['Nonce'], remote, current)
    try: return AESGCM(key).decrypt(iv, decode64(ciphertext), None)
    except InvalidTag: raise DomainError('InvalidFI', 'Encrypted financial information failed authentication.')


def seal_review(value, public_jwk):
    """FIU-to-browser review envelope; the AA process has no review private key."""
    require(isinstance(public_jwk,dict) and set(public_jwk)<={'kty','n','e','alg','ext','key_ops'} and public_jwk.get('kty')=='RSA', 'InvalidKey', 'Use a public RSA review key.')
    try: key=rsa.RSAPublicNumbers(int.from_bytes(decode64(public_jwk['e'],True),'big'),int.from_bytes(decode64(public_jwk['n'],True),'big')).public_key()
    except (ValueError,KeyError): raise DomainError('InvalidKey','Invalid review public key.')
    require(2048<=key.key_size<=4096 and key.public_numbers().e==65537,'InvalidKey','Use a 2048–4096-bit public RSA review key.')
    protected=b64u(canonical({'alg':'RSA-OAEP-256','enc':'A256GCM','cty':'application/json'}).encode())
    content_key=secrets.token_bytes(32);nonce=secrets.token_bytes(12)
    encrypted_key=key.encrypt(content_key,padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
    ciphertext=AESGCM(content_key).encrypt(nonce,canonical(value).encode(),protected.encode())
    return '.'.join([protected,b64u(encrypted_key),b64u(nonce),b64u(ciphertext[:-16]),b64u(ciphertext[-16:])])
