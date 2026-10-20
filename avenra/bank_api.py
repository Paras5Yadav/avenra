"""Scoped banking endpoints shared by console and integration clients."""
import re

from . import bank_cases
from .errors import DomainError


def dispatch(handler, tenant, env, path):
    app, banking, method = handler.server.app, handler.server.app.banking, handler.command
    if path == 'banking/aa' or path.startswith('banking/aa/'):
        from .aa.api import dispatch as aa_dispatch
        return aa_dispatch(handler, tenant, env, path)
    if path == 'banking/runtime' or path.startswith('banking/runtime/'):
        from .production_api import dispatch as runtime_dispatch
        return runtime_dispatch(handler, tenant, env, path)
    if path == 'banking/fidelity' or path.startswith('banking/fidelity/'):
        from avenra_fidelity.api import dispatch as fidelity_dispatch
        return fidelity_dispatch(handler, tenant, env, path)
    if path == 'banking/upi' or path.startswith('banking/upi/'):
        from .upi_api import dispatch as upi_dispatch
        return upi_dispatch(handler, tenant, env, path)
    if path == 'banking/simulation' and method == 'GET':
        return handler.send(200, app.simulation.overview(tenant, env))
    if path.startswith('banking/simulation/'):
        match = re.fullmatch(r'banking/simulation/(profiles|plans|runs|messages|clock)(?:/([A-Za-z0-9_]+))?(?:/(evidence|resume|sign))?', path)
        if not match:
            raise DomainError('NOT_FOUND', 'Simulation endpoint not found.', 404)
        resource, identity, action = match.groups()
        if method == 'GET' and resource == 'runs' and identity and action in {None, 'evidence'}:
            run = app.simulation.read(tenant, env, identity)
            if action == 'evidence':
                if 'evidence' not in run:
                    raise DomainError('EVIDENCE_PENDING', 'Simulation evidence is pending.', 409)
                return handler.send(200, run['evidence'], extra={'Content-Disposition': 'attachment; filename="avenra-' + identity + '.json"'})
            return handler.send(200, run)
        if method == 'POST' and resource == 'runs' and identity and action == 'resume':
            handler.body()
            run = app.simulation.read(tenant, env, identity)
            if run['state'] in {'passed', 'failed'}:
                raise DomainError('EVIDENCE_SEALED', 'Publish a new run; completed evidence is immutable.', 409)
            from .util import uid
            with app.store.transaction() as db:
                app.store.enqueue(db, tenant, env, 'simulation_run', identity, {}, 'simulation-resume:' + uid('attempt'))
            return handler.send(202, run)
        if method == 'POST' and not identity:
            functions = {'profiles': app.simulation.profile, 'plans': app.simulation.publish, 'runs': app.simulation.create_run, 'messages': app.simulation.receive_message, 'clock': app.simulation.clock}
            return handler.send(202, functions[resource](tenant, env, handler.body()))
        if method == 'POST' and resource == 'messages' and identity == 'sign' and action is None:
            return handler.send(200, app.simulation.sign_message(tenant, env, handler.body()))
        raise DomainError('NOT_FOUND', 'Simulation endpoint not found.', 404)
    if method == 'GET' and path == 'banking':
        return handler.send(200, banking.overview(tenant, env))
    if method == 'GET' and path.startswith('banking/core/'):
        parts = path.split('/')[2:]
        if len(parts) in {1, 2}:
            return handler.send(200, app.adapters.core_read(tenant, env, parts[0], parts[1] if len(parts) == 2 else None))
    if method == 'GET' and path == 'banking/contracts':
        return handler.send(200, app.adapters.contract_services(tenant, env))
    resources = {'banks': banking.bank, 'customers': banking.customer, 'accounts': banking.account, 'operations': banking.submit, 'resolve': banking.resolve, 'rules': banking.rule, 'mandates': banking.mandate, 'disputes': banking.dispute, 'cycles': banking.cycle, 'consents': banking.consent,
                 'cases': lambda t, e, d: bank_cases.publish(app, t, e, d), 'test-runs': lambda t, e, d: bank_cases.create_run(app, t, e, d), 'callbacks': app.callbacks.register,
                 'core-commands': app.adapters.core_command, 'contracts': app.adapters.import_contract, 'analysis': app.monitoring.submit}
    match = re.fullmatch(r'banking/([a-z-]+)(?:/([A-Za-z0-9_]+))?(?:/([a-z-]+))?', path)
    if not match:
        raise DomainError('NOT_FOUND', 'Banking endpoint not found.', 404)
    resource, identity, action = match.groups()
    if method == 'POST' and identity is None and resource in resources:
        return handler.send(202, resources[resource](tenant, env, handler.body()))
    if method == 'GET' and resource == 'operations' and identity and not action:
        return handler.send(200, banking.operation(tenant, env, identity))
    if method == 'GET' and resource == 'core-commands' and identity and not action:
        with app.store.reader() as db:
            value = banking._get(db, 'external_commands', tenant, env, identity)
            if value['service'] != 'banking_core':
                raise DomainError('NOT_FOUND', 'Banking command not found.', 404)
            return handler.send(200, value)
    if method == 'GET' and resource == 'analysis' and identity and not action:
        return handler.send(200, app.monitoring.read(tenant, env, identity))
    if method == 'GET' and resource == 'test-runs' and identity and action in {None, 'evidence'}:
        run = bank_cases.read_run(app, tenant, env, identity)
        if action == 'evidence':
            if 'evidence' not in run:
                raise DomainError('EVIDENCE_PENDING', 'Evidence is not available yet.', 409)
            return handler.send(200, run['evidence'], extra={'Content-Disposition': 'attachment; filename="avenra-' + identity + '.json"'})
        return handler.send(200, run)
    if method == 'GET' and resource == 'consents' and identity and action == 'data':
        return handler.send(200, banking.consent_data(tenant, env, identity, handler.headers.get('X-Avenra-Consent')))
    if method == 'POST' and identity:
        data = handler.body()
        if resource == 'core-commands' and action == 'reconcile':
            return handler.send(202, app.adapters.reconcile_core(tenant, env, identity))
        if resource == 'operations' and action == 'reconcile':
            return handler.send(202, banking.reconcile(tenant, env, identity))
        if resource == 'operations' and action in {'refund', 'reversal'}:
            return handler.send(202, banking.adjust(tenant, env, identity, data, action))
        if resource == 'accounts' and action == 'state':
            return handler.send(200, banking.account_state(tenant, env, identity, data))
        if resource == 'mandates' and action in {'approve', 'revoke', 'execute'}:
            return handler.send(202, banking.mandate_action(tenant, env, identity, action, data))
        if resource == 'consents' and action in {'approve', 'revoke'}:
            return handler.send(200, banking.consent_action(tenant, env, identity, action, data))
        if resource == 'disputes' and action == 'state':
            return handler.send(200, banking.dispute_update(tenant, env, identity, data))
        if resource == 'cycles' and action == 'reconcile':
            return handler.send(200, banking.reconcile_cycle(tenant, env, identity, data))
        if resource == 'contracts' and action == 'invoke':
            return handler.send(200, app.adapters.invoke_contract(tenant, env, identity, data))
    raise DomainError('NOT_FOUND', 'Banking endpoint not found.', 404)
