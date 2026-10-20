"""Additional routes inside the existing authenticated Avenra API layer."""
import hashlib
from http import cookies
import re

from avenra.errors import DomainError, require


def principal(handler):
    if getattr(handler, 'identity_context', None):
        return handler.identity_context['approval_principal']
    # The shared router has already authenticated this exact credential.
    token = handler.headers.get('Authorization', '')
    if token:
        if not token.startswith('Bearer avn_') and handler.server.app.identity.configuration()['mode'] == 'institutional':
            return 'subject:' + handler.server.app.identity.verify(token[7:])['subject_hash']
        return 'service:' + hashlib.sha256(token.encode()).hexdigest()
    jar = cookies.SimpleCookie();jar.load(handler.headers.get('Cookie', ''))
    token = jar.get('avenra_session')
    require(token, 'UNAUTHENTICATED', 'An authenticated principal is required.', 401)
    return 'operator-session:' + hashlib.sha256(token.value.encode()).hexdigest()


def dispatch(handler, tenant, env, path):
    app, method = handler.server.app, handler.command
    lifecycle = app.fidelity
    suffix = path.removeprefix('banking/fidelity').strip('/')
    if method == 'GET' and not suffix:
        return handler.send(200, lifecycle.overview(tenant, env))
    if method == 'GET' and suffix == 'coverage':
        return handler.send(200, lifecycle.coverage(tenant, env))
    match = re.fullmatch(r'(complaints|cycles)/([A-Za-z0-9_]+)(?:/(report|csv))?', suffix)
    if method == 'GET' and match:
        kind, identity, action = match.groups()
        if action == 'csv' and kind == 'cycles':
            return handler.send(200, lifecycle.csv_report(tenant, env, identity), 'text/csv; charset=utf-8', {'Content-Disposition': 'attachment; filename="avenra-synthetic-' + identity + '.csv"'})
        require(action in {None, 'report'} and (kind == 'cycles' or action is None), 'NOT_FOUND', 'Lifecycle report not found.', 404)
        return handler.send(200, (lifecycle.complaint_read if kind == 'complaints' else lifecycle.cycle_read)(tenant, env, identity))
    require(method == 'POST', 'NOT_FOUND', 'Lifecycle endpoint not found.', 404)
    data, actor = handler.body(), principal(handler)
    if suffix == 'profile': return handler.send(200, lifecycle.enable(tenant, env, data))
    if suffix == 'discover': return handler.send(200, lifecycle.discover(tenant, env, data))
    if suffix == 'challenges': return handler.send(202, lifecycle.challenge(tenant, env, data))
    if suffix == 'mandates': return handler.send(202, lifecycle.mandate(tenant, env, data))
    if suffix == 'complaints': return handler.send(202, lifecycle.complaint(tenant, env, data, actor))
    if suffix == 'cycles': return handler.send(202, lifecycle.cycle(tenant, env, data, actor))
    match = re.fullmatch(r'(challenges|payments|mandates|occurrences|complaints|cycles|proposals)/([A-Za-z0-9_]+)/([a-z-]+)', suffix)
    require(match, 'NOT_FOUND', 'Lifecycle endpoint not found.', 404)
    kind, identity, action = match.groups()
    if kind == 'challenges' and action == 'complete': return handler.send(200, lifecycle.complete_credential(tenant, env, identity, data))
    if kind == 'payments' and action == 'authorize': return handler.send(202, lifecycle.authorize_payment(tenant, env, identity, data))
    if kind == 'mandates':
        if action == 'schedule': return handler.send(202, lifecycle.schedule(tenant, env, identity, data))
        if action in {'approve', 'pause', 'resume', 'revoke'}: return handler.send(200, lifecycle.mandate_action(tenant, env, identity, action, data))
    if kind == 'occurrences' and action == 'dispatch':
        require(data == {}, 'INVALID_BODY', 'Use an empty dispatch body.')
        return handler.send(202, lifecycle.execute_occurrence(tenant, env, identity))
    if kind == 'complaints':
        if action == 'investigate': return handler.send(200, lifecycle.investigate(tenant, env, identity, data, actor))
        if action == 'propose': return handler.send(202, lifecycle.propose_resolution(tenant, env, identity, data, actor))
    if kind == 'cycles':
        if action == 'reconcile': return handler.send(200, lifecycle.reconcile(tenant, env, identity, data, actor))
        if action == 'propose': return handler.send(202, lifecycle.propose_release(tenant, env, identity, data, actor))
    if kind == 'proposals' and action == 'approve': return handler.send(200, lifecycle.approve(tenant, env, identity, data, actor))
    raise DomainError('NOT_FOUND', 'Lifecycle action not found.', 404)
