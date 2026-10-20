"""Participant lab routes, behind the common workspace and environment guard."""
import re

from .errors import DomainError, require


def dispatch(handler, tenant, env, path):
    upi, method = handler.server.app.upi, handler.command
    if path == 'banking/upi' and method == 'GET':
        return handler.send(200, upi.overview(tenant, env))
    if path == 'banking/upi/connections' and method == 'GET':
        return handler.send(200, upi.connections.overview(tenant, env))
    if path == 'banking/upi/wire/contract' and method == 'GET':
        from .upi_wire import contract, XSD
        from .util import digest
        upi.app.environment(tenant, env)
        value = contract()
        return handler.send(200, {**value, 'contract_hash': digest(value), 'schema': XSD})
    wire = re.fullmatch(r'banking/upi/wire/messages(?:/([A-Za-z0-9_]+))?', path)
    if wire:
        from .upi_wire import MEDIA_TYPE, export, read_body, receive
        identity = wire.group(1)
        if identity and method == 'GET':
            return handler.send(200, export(upi, tenant, env, identity), content_type=MEDIA_TYPE + '; charset=utf-8')
        if not identity and method == 'POST':
            return handler.send(202, receive(upi, tenant, env, read_body(handler)))
        raise DomainError('NOT_FOUND', 'Wire endpoint not found.', 404)
    match = re.fullmatch(r'banking/upi/connections(?:/([A-Za-z0-9_]+)/(validate|activate))?', path)
    if match and method == 'POST':
        identity, action = match.groups()
        function = upi.connections.create if identity is None else getattr(upi.connections, action)
        value = function(tenant, env, handler.body()) if identity is None else function(tenant, env, identity, handler.body())
        return handler.send(202 if identity is None else 200, value)
    match = re.fullmatch(r'banking/upi/(participants|devices|links|payments|balance|qr|messages)(?:/([A-Za-z0-9_]+))?(?:/([a-z-]+))?', path)
    require(match, 'NOT_FOUND', 'Participant endpoint not found.', 404)
    resource, identity, action = match.groups()
    if method == 'GET' and resource == 'payments' and identity and action in {None, 'status', 'evidence'}:
        value = upi.read(tenant, env, identity)
        if action == 'evidence':
            require(value['evidence'], 'EVIDENCE_PENDING', 'All participant deliveries and financial observations must complete.', 409)
            return handler.send(200, value['evidence'], extra={'Content-Disposition': 'attachment; filename="avenra-' + identity + '.json"'})
        return handler.send(200, value)
    if method != 'POST':
        raise DomainError('NOT_FOUND', 'Participant endpoint not found.', 404)
    data = handler.body()
    if not identity:
        functions = {'participants': upi.participants, 'devices': upi.device, 'links': upi.link,
                     'payments': upi.submit, 'balance': upi.balance, 'qr': upi.qr, 'messages': upi.receive}
        return handler.send(202, functions[resource](tenant, env, data))
    if resource == 'qr' and identity == 'decode' and not action:
        return handler.send(200, upi.parse_qr(tenant, env, data))
    if resource == 'participants' and action in {'pause', 'resume', 'rotate-key', 'revoke-key'}:
        return handler.send(200, upi.participant_action(tenant, env, identity, action, data))
    if resource == 'devices' and action in {'activate', 'revoke'}:
        return handler.send(200, upi.device_action(tenant, env, identity, action, data))
    if resource == 'payments' and action in {'authorize', 'reconcile', 'refund'}:
        function = {'authorize': upi.authorize, 'reconcile': upi.reconcile, 'refund': upi.refund}[action]
        return handler.send(202, function(tenant, env, identity, data))
    raise DomainError('NOT_FOUND', 'Participant endpoint not found.', 404)
