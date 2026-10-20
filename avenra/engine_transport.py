"""Verified engine TLS adapter preserving the original accounting interface."""
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, ProxyHandler, build_opener

from .deployment_identity import client_context
from .errors import require
from .gateways import HTTP, NoRedirect


class SecureEngineHTTP(HTTP):
    def __init__(self, origin, context):
        parsed = urlsplit(origin)
        require(parsed.scheme == 'https' and parsed.hostname and not (parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment), 'ENGINE_CONFIGURATION', 'Engine transport requires an approved TLS origin.', 503)
        self.origin = origin
        self.opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=context))


def configure(app):
    from .private_config import deployment
    config = deployment(app.store.directory).get('transport', {})
    if config.get('accounting'):
        value = config['accounting']
        app.accounting.http = SecureEngineHTTP(value['origin'], client_context(app.store.directory, value['identity_reference']))
