"""Private TLS deployment identities and standards-based trust validation."""
from datetime import datetime, timedelta, timezone
import ipaddress
from pathlib import Path
import re
import ssl

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .errors import require
from .lab_identity import private_read, private_write
from .private_config import secret_path


def issue(directory, name, hosts=('127.0.0.1',)):
    require(isinstance(name, str) and re.fullmatch('[a-zA-Z0-9_-]{1,80}', name), 'IDENTITY_NAME', 'Use a named deployment identity.')
    root = Path(directory) / 'deployment-secrets';root.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder = root / name
    require(not folder.exists() and not root.is_symlink(), 'IDENTITY_EXISTS', 'Preserve existing identities; issue a new version.', 409)
    folder.mkdir(mode=0o700)
    current = datetime.now(timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'Avenra Synthetic Deployment'), x509.NameAttribute(NameOID.COMMON_NAME, name + ' test authority')])
    def certificate(common, key, client=False, ca=False):
        subject = ca_name if ca else x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common)])
        builder = x509.CertificateBuilder().subject_name(subject).issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(current - timedelta(minutes=1)).not_valid_after(current + timedelta(days=7))
        builder = builder.add_extension(x509.BasicConstraints(ca=ca, path_length=0 if ca else None), True)
        builder = builder.add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
        builder = builder.add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False)
        builder = builder.add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False, data_encipherment=False, key_agreement=False, key_cert_sign=ca, crl_sign=ca, encipher_only=False, decipher_only=False), True)
        if not ca:
            builder = builder.add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH if client else ExtendedKeyUsageOID.SERVER_AUTH]), True)
            if not client:
                names = []
                for host in hosts:
                    try: names.append(x509.IPAddress(ipaddress.ip_address(host)))
                    except ValueError:
                        require(isinstance(host, str) and re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?', host), 'IDENTITY_HOST', 'Use explicit DNS/IP subject names.')
                        names.append(x509.DNSName(host))
                require(names, 'IDENTITY_HOST', 'At least one server identity is required.')
                builder = builder.add_extension(x509.SubjectAlternativeName(names), False)
        return builder.sign(ca_key, hashes.SHA256())
    server_key, client_key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    for filename, value in [('ca-key.pem', ca_key), ('server-key.pem', server_key), ('client-key.pem', client_key)]:
        private_write(folder / filename, value.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    for filename, value in [('ca-cert.pem', certificate(name, ca_key, ca=True)), ('server-cert.pem', certificate(name, server_key)), ('client-cert.pem', certificate(name + ' client', client_key, client=True))]:
        private_write(folder / filename, value.public_bytes(serialization.Encoding.PEM))
    return {'reference': 'secret://' + name, 'directory': str(folder), 'classification': 'synthetic_test_identity', 'valid_for_days': 7}


def paths(directory, reference, client=False):
    result = {key: secret_path(directory, reference, filename) for key, filename in
              [('cert', 'client-cert.pem' if client else 'server-cert.pem'), ('key', 'client-key.pem' if client else 'server-key.pem'), ('ca', 'ca-cert.pem')]}
    current = datetime.now(timezone.utc)
    for item in ['cert', 'ca']:
        cert = x509.load_pem_x509_certificate(private_read(result[item]))
        require(cert.not_valid_before_utc <= current < cert.not_valid_after_utc, 'CERTIFICATE_EXPIRED', 'Deployment certificate is outside its validity interval.', 503)
    cert = x509.load_pem_x509_certificate(private_read(result['cert']))
    expected = ExtendedKeyUsageOID.CLIENT_AUTH if client else ExtendedKeyUsageOID.SERVER_AUTH
    require(expected in cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value, 'CERTIFICATE_USAGE', 'Deployment certificate has the wrong purpose.', 503)
    return result


def server_context(directory, reference, require_client=False):
    identity = paths(directory, reference)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(identity['cert']), str(identity['key']))
    context.load_verify_locations(cafile=str(identity['ca']))
    context.verify_mode = ssl.CERT_REQUIRED if require_client else ssl.CERT_NONE
    return context


def client_context(directory, reference, with_certificate=True):
    identity = paths(directory, reference, client=True)
    context = ssl.create_default_context(cafile=str(identity['ca']));context.minimum_version = ssl.TLSVersion.TLSv1_2
    if with_certificate: context.load_cert_chain(str(identity['cert']), str(identity['key']))
    return context
