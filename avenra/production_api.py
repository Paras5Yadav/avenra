"""Runtime controls registered in the current workspace/environment API."""
import re
from urllib.parse import parse_qs, urlsplit
from .errors import DomainError, require


def dispatch(handler, tenant, env, path):
    runtime = handler.server.app.runtime
    actor = handler.identity_context['principal']
    parsed=urlsplit(path);query=parse_qs(urlsplit(handler.path).query)
    suffix = parsed.path.removeprefix('banking/runtime').strip('/')
    if handler.command == 'GET' and not suffix:
        return handler.send(200, runtime.overview(tenant, env))
    if handler.command == 'GET' and suffix=='operations':
        require(set(query)<={'cursor'} and all(len(x)==1 for x in query.values()),'INCIDENT_CURSOR','Use one incident cursor.')
        return handler.send(200,runtime.operations.overview(tenant,env,query.get('cursor',[None])[0]))
    match=re.fullmatch(r'operations/incidents/([A-Za-z0-9_]+)/events',suffix)
    if handler.command=='GET' and match:
        require(set(query)<={'cursor'} and all(len(x)==1 for x in query.values()),'INCIDENT_CURSOR','Use one incident-event cursor.')
        return handler.send(200,runtime.operations.history(tenant,env,match[1],query.get('cursor',[None])[0]))
    require(handler.command == 'POST', 'NOT_FOUND', 'Runtime endpoint not found.', 404)
    data = handler.body()
    if suffix == 'policies': return handler.send(202, runtime.publish_policy(tenant, env, data, actor))
    if suffix == 'operations/policies': return handler.send(202,runtime.operations.publish(tenant,env,data,actor))
    match=re.fullmatch(r'operations/incidents/([A-Za-z0-9_]+)/acknowledge',suffix)
    if match:return handler.send(200,runtime.operations.acknowledge(tenant,env,match[1],data,actor))
    if suffix == 'grants': return handler.send(202, runtime.grant(tenant, env, data, actor))
    match = re.fullmatch(r'grants/([A-Za-z0-9_]+)/revoke', suffix)
    if match:
        require(data == {}, 'INVALID_BODY', 'Use an empty revocation body.')
        return handler.send(200, runtime.revoke(tenant, env, match[1], actor))
    raise DomainError('NOT_FOUND', 'Runtime endpoint not found.', 404)
