/* Runtime settings in the current Avenra console and environment. */
let runtimeData=null;
const runtimeStyle=document.createElement('style');runtimeStyle.textContent='#runtime-form textarea{display:block;width:100%;margin-top:8px;padding:12px;border:1px solid #dce3d4;border-radius:7px;font:12px ui-monospace,monospace;color:#43563a;background:white;resize:vertical}#runtime-form textarea:focus-visible{outline:3px solid #86ad65;outline-offset:3px}';document.head.append(runtimeStyle);
const runtimeBase=()=>scope()+'/banking/runtime';
const runtimeDefaults={enforce_capabilities:true,rate_per_minute:600,burst:60,max_inflight:16,max_pending_jobs:2000,request_lease_seconds:180};
const runtimeLabels={rate_per_minute:'Requests per minute',burst:'Burst allowance',max_inflight:'Concurrent environment requests',max_pending_jobs:'Pending-work admission limit',request_lease_seconds:'Request observation lease (seconds)'};
const runtimeMonitorLabels={window_seconds:'Observation window (seconds)',sample_interval_seconds:'Sampling interval (seconds)',queue_age_seconds:'Due-work delay (seconds)',queue_depth:'Pending-work warning count',unknown_age_seconds:'Unconfirmed outcome age (seconds)',failed_records:'Failed records per system',request_errors:'Request failures in the window',rejections:'Admission refusals in the window',request_p95_ms:'Request p95 threshold (milliseconds)',minimum_latency_samples:'Minimum completed latency samples',worker_grace_seconds:'Worker heartbeat grace (seconds)',for_seconds:'Failure dwell before firing (seconds)',recovery_seconds:'Healthy dwell before resolving (seconds)'};
function runtimeOperations(data,admin){
 const sample=data.observation?.metrics;
 let html=`<h3>Operational monitoring</h3><p class="bank-note">${data.policy?(data.observation_fresh?'Current scoped observation':'Observation is stale or awaiting collection'):'No monitoring policy is enabled for this environment.'} · ${data.open_count} open incidents</p>`;
 if(admin)html+='<button class="btn small" data-runtime="monitor-policy">Set monitoring policy</button>';
 if(sample)html+=bankTable(['Pending work','Oldest due work','Request failures','Admission refusals'],[`<tr><td>${sample.pending_jobs}</td><td>${Math.round(sample.oldest_due_seconds)} s</td><td>${sample.request_errors}</td><td>${Object.values(sample.rejections).reduce((a,b)=>a+b,0)}</td></tr>`]);
 const incidentRows=rows=>rows.map(row=>`<tr><td><strong>${esc(row.title)}</strong><br><small>${esc(row.action)}</small></td><td>${badge(row.state)}<br>${row.acknowledged_by?'Acknowledged by '+esc(row.acknowledged_by):'Unacknowledged'}</td><td>${esc(new Date(row.changed_at*1000).toLocaleString())}<br><small class="mono">${esc(row.id)}</small></td><td><button class="btn small" data-runtime="incident-events" data-incident="${esc(row.id)}">Retained events</button>${admin&&row.state!=='resolved'&&!row.acknowledged_by?`<button class="btn small" data-runtime="incident-ack" data-incident="${esc(row.id)}">Acknowledge</button>`:''}</td></tr>`);
 html+='<h3>Current incidents</h3>'+bankTable(['Incident / action','State','Observation','Review'],incidentRows(data.open_incidents||[]));
 html+='<h3>Incident history</h3>'+bankTable(['Incident / action','State','Observation','Review'],incidentRows(data.incidents));
 if(data.next_cursor)html+=`<button class="btn small" data-runtime="incidents-older" data-cursor="${esc(data.next_cursor)}">Older incidents</button>`;
 html+='<p class="bank-note">Acknowledgement records review; observed recovery determines resolution. Monitoring does not initiate financial actions or send external notifications.</p>';
 return html;
}
async function runtimeIncidentEvents(identity,cursor=null,version=state.scopeVersion){
 const data=await api(runtimeBase()+'/operations/incidents/'+identity+'/events'+(cursor?'?cursor='+encodeURIComponent(cursor):''));if(version!==state.scopeVersion)return;
 let html=dialogHeader('Incident events','Retained transitions and original observations for this environment.');
 html+=bankTable(['Event','Time / actor','Observed evidence'],data.events.map(row=>`<tr><td>${esc(row.kind)}</td><td>${esc(new Date(row.recorded_at*1000).toLocaleString())}<br>${esc(row.actor||'Operational monitor')}</td><td><pre class="mono">${esc(JSON.stringify(row.payload,null,2))}</pre></td></tr>`));
 if(data.next_cursor)html+=`<button class="btn small" data-runtime="incident-events" data-incident="${esc(identity)}" data-cursor="${esc(data.next_cursor)}">Older events</button>`;
 html+='<button class="btn small" data-runtime="open">Runtime controls</button><div class="form-error" id="runtime-error" role="alert"></div>';
 openDialog('<section class="fidelity-view" aria-label="Incident events">'+html+'</section>');
}
function runtimeError(message){
 const target=$('#runtime-error');if(target){target.textContent=message;target.scrollIntoView({block:'nearest'});}else toast(message);
}
async function runtimeOpen(version=state.scopeVersion){
 const [data,identity]=await Promise.all([api(runtimeBase()),api('/api/me')]);if(version!==state.scopeVersion)return;
 runtimeData=data;const admin=identity.operator||identity.capabilities.includes('runtime.admin');
 runtimeData.canAdmin=admin;
 const status=data.deployment,metric=data.metrics;
 let html=dialogHeader('Runtime controls','Shared policies and observed worker health for the current environment.');
 html+=`<p class="bank-note">${status.identity_mode==='local'?'Local operator access':'Institutional identity'} · ${status.api_tls?'HTTPS ingress configured':'Local HTTP ingress'} · ${status.database_access==='runtime'?'Restricted runtime database role':'Migration-capable local database role'}</p>`;
 html+=`<div class="bank-actions">${admin?'<button class="btn small" data-runtime="policy">Set request policy</button><button class="btn small" data-runtime="grant">Grant subject permissions</button>':''}<button class="btn small" data-runtime="open">Refresh observations</button></div>`;
 html+='<h3>Dependency readiness</h3>'+bankTable(['Component','Observation'],data.readiness.checks.map(c=>`<tr><td>${esc(c.component)}</td><td>${badge(c.ready?'ready':'unavailable')}</td></tr>`));
 html+=bankTable(['Requests / hour','Active','Response deadlines','Sample p95'],[`<tr><td>${metric.requests_last_hour}</td><td>${metric.active}</td><td>${metric.deadline_exceeded}</td><td>${metric.latency_p95_ms===null?'No completed sample':Math.round(metric.latency_p95_ms)+' ms'}<br><small>${metric.latency_sample_size} completed requests</small></td></tr>`]);
 html+='<div id="runtime-operations">'+(data.operations?runtimeOperations(data.operations,admin):'<p class="bank-note">Operational monitoring becomes available after this server loads the upgraded application. Existing runtime controls remain available.</p>')+'</div>';
 html+='<h3>Environment admission policy</h3>'+(data.policy?bankTable(['Control','Active value'],Object.entries(data.policy).map(([key,value])=>`<tr><td>${esc(runtimeLabels[key]||'Granular permission enforcement')}</td><td>${esc(String(value))}</td></tr>`)):'<p class="bank-note">No additional environment request policy is enabled. Existing local workflows retain their current behaviour.</p>');
 html+='<h3>Subject grants</h3>'+bankTable(['Principal','Permissions','Scope / expiry','Action'],data.grants.map(g=>`<tr><td class="mono">${esc(g.principal)}</td><td>${JSON.parse(g.capabilities_json).map(esc).join('<br>')}</td><td>${JSON.parse(g.environments_json).length?JSON.parse(g.environments_json).map(esc).join('<br>'):'All environments in this workspace'}<br>${esc(new Date(g.expires_at*1000).toLocaleString())}</td><td>${g.revoked_at?'Revoked':admin?`<button class="btn small" data-runtime="revoke" data-grant="${esc(g.id)}">Revoke</button>`:'Active'}</td></tr>`));
 html+='<h3>Deployment processes</h3>'+bankTable(['Node','Role','Last heartbeat'],data.workers.map(w=>`<tr><td class="mono">${esc(w.id)}</td><td>${esc(w.role)}</td><td>${esc(new Date(w.last_seen*1000).toLocaleTimeString())}</td></tr>`));
 html+='<p class="bank-note">These are observed local controls. Institutional deployment, capacity, recovery and security acceptance are tracked separately.</p><div class="form-error" id="runtime-error" role="alert"></div>';
 openDialog('<section class="fidelity-view" aria-label="Runtime controls">'+html+'</section>');
}
function runtimeForm(kind,title,description,content){
 openDialog(dialogHeader(title,description)+`<form id="runtime-form" data-kind="${kind}" data-version="${state.scopeVersion}" data-request-key="${crypto.randomUUID()}">${content}<div class="form-error" id="runtime-error" role="alert"></div><div class="form-actions"><span>Current workspace and environment</span><button class="btn primary">Save controls →</button></div></form>`);
}
document.addEventListener('click',async event=>{
 const button=event.target.closest('[data-runtime]');if(!button)return;
 try{
  const action=button.dataset.runtime;
  if(action==='open')return await runtimeOpen();
  if(action==='incident-events')return await runtimeIncidentEvents(button.dataset.incident,button.dataset.cursor||null);
  if(action==='incidents-older'){
   const version=state.scopeVersion;button.disabled=true;const data=await api(runtimeBase()+'/operations?cursor='+encodeURIComponent(button.dataset.cursor));
   if(version!==state.scopeVersion)return;const target=$('#runtime-operations');if(target)target.innerHTML=runtimeOperations(data,runtimeData?.canAdmin===true);
   return;
  }
  if(action==='incident-ack'){
   button.disabled=true;button.dataset.requestKey||=crypto.randomUUID();const version=state.scopeVersion;
   await api(runtimeBase()+'/operations/incidents/'+button.dataset.incident+'/acknowledge',{request_key:button.dataset.requestKey});
   if(version===state.scopeVersion){await runtimeOpen();toast('Incident acknowledged. Its observed health state is unchanged.');}return;
  }
  if(action==='monitor-policy'){
   if(!runtimeData)throw new Error('Open runtime controls first.');const policy=runtimeData.operations.policy?.document||runtimeData.operations.defaults;
   return runtimeForm('monitor','Operational monitoring policy','Thresholds observe this environment. No financial operations are initiated.',Object.entries(runtimeMonitorLabels).map(([key,label])=>`<label class="field">${esc(label)}<input type="number" name="${key}" value="${policy[key]}" required step="1" min="${['for_seconds','recovery_seconds'].includes(key)?0:1}"></label>`).join(''));
  }
  if(action==='policy'){
   if(!runtimeData)throw new Error('Open runtime controls first.');const policy=runtimeData.policy||runtimeDefaults;
   runtimeForm('policy','Environment request policy','Recovery and status remain available when the pending-work queue is full.',Object.entries(runtimeLabels).map(([key,label])=>`<label class="field">${esc(label)}<input type="number" name="${key}" value="${policy[key]}" required step="1" min="${key==='request_lease_seconds'?120:1}"></label>`).join('')+`<label class="field">Granular permission enforcement<select name="enforce_capabilities"><option value="true" ${policy.enforce_capabilities?'selected':''}>Enforce declared subject permissions</option><option value="false" ${policy.enforce_capabilities?'':'selected'}>Retain deployment-level permission policy</option></select></label>`);
  }else if(action==='grant'){
   if(!runtimeData)throw new Error('Open runtime controls first.');
   runtimeForm('grant','Grant subject permissions','Use the principal identifier returned by the authenticated /api/me endpoint.',`<label class="field">Authenticated principal<input name="principal" placeholder="oidc:… or service:…" required autocomplete="off"></label><label class="field">Role template<select name="role">${Object.keys(runtimeData.roles).map(r=>`<option value="${esc(r)}">${esc(r)}</option>`).join('')}</select></label><label class="field">Capabilities, one per line<textarea name="capabilities" rows="5" required>${esc(runtimeData.roles.observer.join('\n'))}</textarea></label><label class="field">Environment identifiers, one per line<textarea name="environment_ids" rows="2">${esc(state.environment)}</textarea><small>Empty means all environments in this workspace.</small></label><label class="field">Grant lifetime (hours)<input name="hours" type="number" value="24" min="1" max="8784" required></label>`);
  }else if(action==='revoke'){
   button.disabled=true;await api(runtimeBase()+'/grants/'+button.dataset.grant+'/revoke',{});await runtimeOpen();toast('The subject grant is revoked.');
  }
 }catch(error){runtimeError(error.message);}finally{if(button.isConnected)button.disabled=false;}
});
document.addEventListener('change',event=>{
 if(event.target.matches('#runtime-form [name="role"]'))$('#runtime-form [name="capabilities"]').value=runtimeData.roles[event.target.value].join('\n');
});
document.addEventListener('submit',async event=>{
 const form=event.target;if(form.id!=='runtime-form')return;event.preventDefault();const button=form.querySelector('button[type="submit"],button');button.disabled=true;
 try{
  if(Number(form.dataset.version)!==state.scopeVersion)throw new Error('The environment changed. Reopen its runtime controls.');
  const values=Object.fromEntries(new FormData(form));
  if(form.dataset.kind==='policy'){
   const body={request_key:form.dataset.requestKey,enforce_capabilities:values.enforce_capabilities==='true'};
   for(const key of Object.keys(runtimeLabels))body[key]=Number(values[key]);await api(runtimeBase()+'/policies',body);
  }else if(form.dataset.kind==='monitor'){
   const body={request_key:form.dataset.requestKey};for(const key of Object.keys(runtimeMonitorLabels))body[key]=Number(values[key]);await api(runtimeBase()+'/operations/policies',body);
  }else await api(runtimeBase()+'/grants',{principal:values.principal.trim(),capabilities:values.capabilities.split(/\s+/).filter(Boolean),environment_ids:values.environment_ids.split(/\s+/).filter(Boolean),expires_at:Date.now()/1000+Number(values.hours)*3600});
  await runtimeOpen();toast('Runtime controls saved.');
 }catch(error){runtimeError(error.message);}finally{if(button.isConnected)button.disabled=false;}
});
