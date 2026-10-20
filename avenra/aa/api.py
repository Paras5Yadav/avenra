"""Existing-console management and participant-authenticated public wire routes."""
import re
import uuid
from ..errors import DomainError, require
from ..util import canonical
from .contracts import stamp, VERSIONS
from .crypto import sign


def wire_dispatch(handler, tenant, env, path):
    app=handler.server.app;app.environment(tenant,env);config,loaded=app.aa.scope(tenant,env)
    try:
        require(handler.command in {'GET','POST'},'InvalidRequest','Unsupported participant method.',405)
        size=handler.headers.get('Content-Length','0');require(size.isdigit() and int(size)<=65536,'InvalidRequest','Financial-data request body exceeds its budget.',413)
        raw=handler.rfile.read(int(size)) if handler.command=='POST' else b''
        value=app.aa.wire(tenant,env,handler.command,path,raw,{k.lower():v for k,v in handler.headers.items()});status=200
    except DomainError as error:
        status=error.status;value={'ver':VERSIONS['aa'],'timestamp':stamp(),'txnid':str(uuid.uuid4()),'errorCode':error.code,'errorMsg':error.message}
    raw=canonical(value).encode()
    return handler.send(status,raw,extra={'x-jws-signature':sign(raw,loaded['key'],config['kid'])},raw_json=True)


def dispatch(handler, tenant, env, path):
    aa=handler.server.app.aa;suffix=path.removeprefix('banking/aa').strip('/')
    if handler.command=='GET' and not suffix:return handler.send(200,aa.overview(tenant,env))
    delivery=re.fullmatch(r'peers/([A-Za-z0-9_-]+)/notices(?:/([A-Za-z0-9_-]+)(?:/(retry))?)?',suffix)
    if delivery and handler.command=='GET' and not delivery[3]:return handler.send(200,aa.deliveries(tenant,env,delivery[1],delivery[2]))
    match=re.fullmatch(r'(consents|sessions|links)/([A-Za-z0-9_-]+)(?:/(approve|state|request|inspect|delink|resume))?',suffix)
    if match and handler.command=='GET' and match[1]=='consents' and not match[3]:return handler.send(200,aa.consent_read(tenant,env,match[2]))
    require(handler.command=='POST','NOT_FOUND','Financial-data management endpoint not found.',404);data=handler.body()
    if delivery and delivery[3]=='retry':return handler.send(202,aa.retry_notice(tenant,env,delivery[1],delivery[2],data,handler.identity_context['principal']))
    job=re.fullmatch(r'deliveries/([A-Za-z0-9_-]+)/retry',suffix)
    if job:return handler.send(202,aa.retry_delivery(tenant,env,job[1],data,handler.identity_context['principal']))
    if suffix=='discover':return handler.send(200,aa.discover(tenant,env,data))
    if suffix=='links':return handler.send(202,aa.link(tenant,env,data))
    if suffix=='consents':return handler.send(202,aa.create_consent(tenant,env,data))
    if match:
        resource,identity,action=match.groups()
        if resource=='links' and action=='delink':require(data=={},'InvalidRequest','Use an empty delink request.');return handler.send(200,aa.delink(tenant,env,identity))
        if resource=='consents' and action=='approve':return handler.send(200,aa.approve(tenant,env,identity,data))
        if resource=='consents' and action=='state':return handler.send(200,aa.consent_action(tenant,env,identity,data))
        if resource=='consents' and action=='request':return handler.send(202,aa.fetch_request(tenant,env,identity,data))
        if resource=='sessions' and action=='inspect':return handler.send(200,aa.inspect(tenant,env,identity,data))
        if resource=='sessions' and action=='resume':require(data=={},'InvalidRequest','Use an empty resume body.');return handler.send(202,aa.resume(tenant,env,identity))
    raise DomainError('NOT_FOUND','Financial-data action not found.',404)
