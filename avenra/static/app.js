'use strict';

const $ = (selector) => document.querySelector(selector);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const currency = (paise) => new Intl.NumberFormat('en-IN', {style:'currency',currency:'INR',maximumFractionDigits:2}).format((paise ?? 0) / 100);
const relative = (value) => {
  if (!value) return '—';
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000));
  if (seconds < 60) return 'Just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  return new Date(value).toLocaleString('en-IN', {day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'});
};
const paths = {
  overview:'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  payments:'<path d="M4 8h15m-4-4 4 4-4 4M20 16H5m4-4-4 4 4 4"/>',
  ledger:'<path d="M4 4h16v17H4zM8 4v17M12 9h4M12 13h4"/>',
  scenarios:'<path d="m9 3 6 0m-5 0v7L4 20q-.5 1 1 1h14q1.5 0 1-1l-6-10V3M8 15h8"/>',
  runs:'<path d="M6 3h9l4 4v14H6zM14 3v5h5M9 12h6M9 16h6"/>',
  engines:'<rect x="5" y="5" width="14" height="14" rx="2"/><path d="M9 1v4M15 1v4M9 19v4M15 19v4M1 9h4M1 15h4M19 9h4M19 15h4M9 9h6v6H9z"/>'
};
const icon = (key) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">${paths[key] || paths.overview}</svg>`;
const pages = [['overview','Overview'],['banking','Banking workspace'],['payments','Payments'],['ledger','Accounts & ledger'],['scenarios','Scenario library'],['runs','Runs & evidence'],['engines','Engine status']];
const labels = {succeeded:'Succeeded',declined:'Declined',unknown:'Unconfirmed',pending:'Pending',passed:'Passed',failed:'Failed',queued:'Queued',running:'Running',connected:'Connected',unavailable:'Unavailable',source_imported:'Source imported',not_configured:'Not configured',active:'Active',provisioning:'Provisioning'};
const badge = (state) => `<span class="badge ${['succeeded','passed','connected','active'].includes(state)?'good':['unknown','pending','running','queued','provisioning'].includes(state)?'warning':['failed','declined'].includes(state)?'bad':'neutral'}"><span>●</span>${esc(labels[state] || state)}</span>`;
let saved = {};
try { saved = JSON.parse(localStorage.getItem('avenra-scope') || '{}'); } catch {}
const state = {csrf:null,catalog:null,data:null,tenant:saved.tenant,environment:saved.environment,page:'overview',selectedRun:null,run:null,lastPayment:null,loading:false,signature:'',scopeVersion:0,bankData:null,bankTab:'institutions',bankRun:null,selectedBankRun:null};
let toastTimer;
function toast(message) {
  $('#toast').textContent = message; $('#toast').classList.add('show');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').classList.remove('show'), 5000);
}
async function api(path, body, specialHeaders={}) {
  const response = await fetch(path, {method:body === undefined?'GET':'POST',credentials:'same-origin',
    headers:{'Content-Type':'application/json',...(state.csrf?{'X-Avenra-CSRF':state.csrf}:{}),...specialHeaders},
    body:body === undefined?undefined:JSON.stringify(body)});
  const value = await response.json();
  if (!response.ok) { const error = new Error(value.error?.message || 'The operation could not complete.'); error.code = value.error?.code; throw error; }
  return value;
}
const scope = () => `/api/tenants/${state.tenant}/environments/${state.environment}`;
function persistScope() { localStorage.setItem('avenra-scope', JSON.stringify({tenant:state.tenant,environment:state.environment})); }
function chooseScope() {
  if (!state.catalog.tenants.some(t => t.id === state.tenant)) state.tenant = state.catalog.tenants[0]?.id;
  const environments = state.catalog.environments.filter(e => e.tenant_id === state.tenant);
  if (!environments.some(e => e.id === state.environment)) state.environment = environments[0]?.id;
  $('#tenant-select').innerHTML = state.catalog.tenants.map(t => `<option value="${esc(t.id)}" ${t.id===state.tenant?'selected':''}>${esc(t.name)}</option>`).join('');
  persistScope();
}
async function reloadCatalog() { state.catalog = await api('/api/catalog'); chooseScope(); }
async function refresh(force=false) {
  if (state.loading || !state.environment) return;
  state.loading = true; const version = state.scopeVersion; const base = scope();
  try {
    const data = await api(base + '/overview');
    if (version !== state.scopeVersion) return;
    state.data = data;
    if(state.page==='banking'){
      state.bankData=await api(base+'/banking');
      if(state.selectedBankRun)state.bankRun=await api(base+'/banking/test-runs/'+state.selectedBankRun);
    }
    if (state.selectedRun) state.run = await api(base + '/runs/' + state.selectedRun);
    if (version !== state.scopeVersion) return;
    const signature = JSON.stringify([data,state.run,state.page,state.bankData,state.bankRun,state.bankTab]);
    if (force || signature !== state.signature) { state.signature=signature; render(); }
  } catch (error) {
    toast(error.message);
    if (!state.data) $('#main').innerHTML = `<div class="banner">${esc(error.message)}</div><button class="btn" data-action="retry">Try again</button>`;
  } finally { state.loading=false; }
}
function header(title, description, action='payment') {
  const envs = state.catalog.environments.filter(e => e.tenant_id===state.tenant);
  return `<div class="eyebrow">YOUR LOCAL FINANCIAL LAB</div><div class="page-heading"><div><h1>${esc(title)}</h1><p>${esc(description)}</p></div><div class="heading-actions">${action==='payment'?`<button class="btn primary" data-action="payment" ${state.data?.environment.status!=='active'?'disabled':''}><span>＋</span>New payment</button>`:action==='scenario'?'<button class="btn primary" data-nav="scenarios">＋ Run a scenario</button>':''}</div></div>
  <div class="environment-strip"><div class="heading-actions"><select class="environment-select" id="environment-select" aria-label="Environment">${envs.map(e=>`<option value="${esc(e.id)}" ${e.id===state.environment?'selected':''}>${esc(e.name)}</option>`).join('')}</select><button class="btn ghost small" data-action="environment">＋ New environment</button></div><div class="environment-meta"><span class="dot"></span><strong>INR lab profile</strong><span>·</span><span class="mono">v1.0</span><span>·</span>${badge(state.data?.environment.status || 'provisioning')}</div></div>`;
}
function accountRows() {
  return state.data.accounts.map(a => `<div class="account-row"><div class="account-person"><div class="account-icon">${esc(a.name[0])}</div><div><div class="account-name">${esc(a.name)}</div><div class="account-sub mono">${esc(a.vpa)}</div></div></div><div class="account-money">${currency(a.balance_paise)}</div></div>`).join('');
}
function paymentTable(limit=100) {
  const payments = state.data.payments.slice(0,limit);
  if (!payments.length) return `<div class="empty"><strong>Your first payment starts here.</strong>Transfer synthetic funds, replay a request, or simulate a lost response.<br><button class="btn" data-action="payment">Create a payment →</button></div>`;
  return `<div class="table-wrap"><table><thead><tr><th>Payment</th><th>Beneficiary</th><th>Amount</th><th>Status</th><th>Created</th><th></th></tr></thead><tbody>${payments.map(p=>`<tr><td><div class="table-primary">${esc(p.payer_name)}</div><div class="table-secondary mono">${esc(p.id.slice(0,16))}…</div></td><td><div class="table-primary">${esc(p.payee_name)}</div><div class="table-secondary">${p.fault==='lost_response'?'Response-loss test':'Synthetic transfer'}</div></td><td>${currency(p.amount_paise)}</td><td>${badge(p.state)}</td><td class="table-secondary">${esc(relative(p.created_at))}</td><td>${p.state==='unknown'?`<button class="btn small" data-reconcile="${esc(p.id)}">Reconcile</button>`:`<button class="link-button" data-payment="${esc(p.id)}">Details ↗</button>`}</td></tr>`).join('')}</tbody></table></div>`;
}
function overview() {
  const m=state.data.metrics;
  return header('Environment overview','Explore payment behaviour with synthetic funds and an authoritative ledger.') +
  `<div class="metrics">${[
    ['Recent settled volume',currency(m.settled_paise),'Latest 100 payments','payments'],
    ['Funded accounts',state.data.accounts.length,'Balances read from the ledger','ledger'],
    ['Verified runs',`${m.passed_runs} / ${m.total_runs}`,'Passed runs / latest 100 runs','scenarios'],
    ['Unconfirmed outcomes',m.unconfirmed,m.unconfirmed?'Awaiting reconciliation':'No recovery action pending','runs']
  ].map(([title,number,note,key])=>`<div class="metric"><div class="metric-top"><span>${title}</span>${icon(key)}</div><div class="metric-number">${number}</div><div class="metric-note ${key==='runs'&&!m.unconfirmed?'good':''}">${esc(note)}</div></div>`).join('')}</div>
  <div class="feature-grid"><section class="exercise"><div class="eyebrow">RESILIENCE EXERCISE / 004</div><span class="exercise-badge">REAL NETWORK FAULT</span><h2>The payment went through.<br>The response didn’t.</h2><p>Drop the ledger’s response, observe an unconfirmed payment, then recover the original transaction without a second debit.</p><button class="btn" data-run="lost_response">Test response-loss recovery <span>↗</span></button><div class="route"><span>Intent saved</span><b>→</b><span>Ledger commit</span><b>→</b><span class="route-fault">Response lost</span><b>→</b><span>Reconcile</span></div></section>
  <section class="panel"><div class="panel-heading"><h2>Environment accounts</h2><button class="link-button" data-nav="ledger">View ledger ↗</button></div>${accountRows() || '<div class="empty">Account provisioning in progress.</div>'}<div class="panel-foot">All amounts are synthetic INR · Integer paise accounting</div></section></div>
  <section class="panel activity"><div class="panel-heading"><h2>Recent payments</h2><button class="link-button" data-nav="payments">View all payments ↗</button></div>${paymentTable(5)}</section>${healthStrip()}`;
}
function healthStrip() {
  return `<div class="health-strip">${state.catalog.services.slice(0,3).map(s=>`<span class="health-item"><span class="dot" style="background:${s.state==='connected'?'#73965a':'#c3c9b9'}"></span>${esc(s.name)}<span style="color:#a2ad98">${s.state==='connected'?'connected':'unavailable'}</span></span>`).join('')}<button class="link-button" data-nav="engines">Service details ↗</button></div>`;
}
function payments() {
  const replay=state.lastPayment && state.lastPayment.scope===scope();
  return header('Payments','Submit a synthetic transfer. Observe its financial outcome and reconcile uncertainty.')+
    `${state.data.metrics.unconfirmed?'<div class="banner">An unconfirmed response does not establish whether a payment committed. Reconcile the original intent before creating a replacement payment.</div>':''}`+
    `<section class="panel"><div class="panel-heading"><h2>Payment intents <span class="notification-count">${state.data.payments.length}</span></h2>${replay?'<button class="btn small" data-action="replay">↻ Replay last request</button>':'<span>Latest 100</span>'}</div>${paymentTable()}</section>`;
}
function ledger() {
  return header('Accounts & ledger','Authoritative balances and immutable financial transactions from the local accounting engine.')+
  `<div class="ledger-grid">${state.data.accounts.map(a=>`<div class="balance-card"><div class="account-person"><span class="account-icon">${esc(a.name[0])}</span><div><div class="account-name">${esc(a.name)}</div><div class="account-sub">${esc(a.role)}</div></div></div><div class="account-money">${currency(a.balance_paise)}</div><span class="mono">${esc(a.vpa)}</span></div>`).join('')}</div>
  <section class="panel"><div class="panel-heading"><h2>Financial journal</h2><span>Latest 100 · INR/2</span></div>${state.data.transactions.map(t=>`<details class="ledger-entry"><summary><span><strong>#${esc(t.id)}</strong> <span class="mono">${esc(t.reference)}</span></span><span class="table-secondary">${esc(relative(t.timestamp))}</span></summary><div class="postings">${t.postings.map(p=>`<div class="posting"><span class="mono">${esc(p.source)} → ${esc(p.destination)}</span><strong>${currency(p.amount)}</strong></div>`).join('')}</div></details>`).join('') || '<div class="empty">No confirmed journal entries are available.</div>'}</section>`;
}
function scenarios() {
  const coverage=state.data.poc;
  const poc=coverage?`<section class="panel activity"><div class="panel-heading"><h2>Production POC coverage</h2><span>${coverage.passed_areas} / ${coverage.total_areas} areas demonstrated in this environment</span></div><div class="table-wrap"><table><thead><tr><th>Capability</th><th>Demonstration</th><th>Status</th></tr></thead><tbody>${coverage.areas.map(a=>`<tr><td class="table-primary">${esc(a.name)}</td><td>${esc(a.description)}${a.scenario_key?`<br><button class="link-button" data-run="${esc(a.scenario_key)}">Run demonstration ↗</button>`:''}</td><td>${badge(a.state)}</td></tr>`).join('')}</tbody></table></div><div class="panel-foot">Full synthetic scope · Institutional acceptance and independent assessment are external milestones.</div></section>`:'';
  return header('Scenario library','Repeatable financial assertions. Each run gets its own fresh ledger and retained evidence.','')+
  `<div class="bank-actions"><button class="btn primary" data-action="run-poc">Run available POC demonstrations ↗</button><span class="table-secondary">${coverage?`${coverage.passed_areas} / ${coverage.total_areas} scope areas demonstrated · Full POC ${coverage.poc_complete?'complete':'unfinished'}`:'Current synthetic profile'}</span></div>`+
  `<div class="case-grid">${state.catalog.scenarios.map((s,i)=>`<article class="case-card"><div class="case-index">${String(i+1).padStart(2,'0')}</div><h2>${esc(s.name)}</h2><p>${esc(s.description)}</p><div class="case-bottom"><span>${esc(s.group)}</span><button class="btn" data-run="${esc(s.key)}">Run scenario ↗</button></div></article>`).join('')}</div>`+poc;
}
function runs() {
  return header('Runs & evidence','Inspect expected and observed outcomes. Export a locally signed integrity report.','scenario')+
  `<div class="run-layout"><section class="panel"><div class="panel-heading"><h2>Test runs</h2><span>Latest 100</span></div>${state.data.runs.length?`<div class="table-wrap"><table><thead><tr><th>Scenario</th><th>Result</th><th>Created</th></tr></thead><tbody>${state.data.runs.map(r=>`<tr class="run-row ${r.id===state.selectedRun?'selected':''}" data-open-run="${esc(r.id)}" tabindex="0" role="button" aria-label="Open ${esc(r.name)}"><td><div class="table-primary">${esc(r.name)}</div><div class="table-secondary mono">${esc(r.id.slice(0,15))}…</div></td><td>${badge(r.state)}</td><td class="table-secondary">${esc(relative(r.created_at))}</td></tr>`).join('')}</tbody></table></div>`:'<div class="empty"><strong>No runs yet.</strong>Choose a scenario to create an isolated financial fixture.</div>'}</section><section class="panel run-detail">${runDetail()}</section></div>`;
}
function runDetail() {
  const r=state.run;
  if (!r) return '<div class="empty"><strong>A clear record of what happened.</strong>Select a run to inspect its assertions and evidence.</div>';
  return `<div class="service-title"><h2>${esc(r.name)}</h2>${badge(r.state)}</div><span class="run-id mono">${esc(r.id)}</span>${r.error?`<div class="banner">${esc(r.error)}</div>`:''}${r.steps.map(s=>`<details class="run-step"><summary><span class="check ${s.passed?'':'failed'}">${s.passed?'✓':'×'}</span>${esc(s.name)}</summary><div class="step-compare"><div><span>EXPECTED</span><pre>${esc(JSON.stringify(JSON.parse(s.expected_json),null,2))}</pre></div><div><span>OBSERVED</span><pre>${esc(JSON.stringify(JSON.parse(s.observed_json),null,2))}</pre></div></div></details>`).join('') || '<div class="empty"><span class="spinner"></span><br>Run is queued or executing.</div>'}${r.evidence?`<div class="evidence-hash">${badge(r.evidence.valid?'passed':'failed')} <span>Local evidence integrity</span><p class="mono">SHA-256 ${esc(r.evidence.manifest_hash)}</p><p>Verified with this installation’s key. This report does not establish institutional certification.</p><button class="btn small" data-download="${esc(r.id)}">↓ Download evidence</button></div>`:''}`;
}
function engines() {
  return header('Engine status','Capabilities are enabled only after their local service or worker is available.','')+
  '<div class="bank-actions bank-note"><button class="btn" data-runtime="open">Runtime controls ↗</button></div>'+
  `<div class="banner">This installation uses synthetic data. Isolated code execution needs a qualified Linux worker with KVM. Consent, lending, risk and live UPI adapters are not configured in this build.</div><div class="service-grid">${state.catalog.services.map(s=>`<article class="service-card"><div class="service-title"><h2>${esc(s.name)}</h2>${badge(s.state)}</div><p>${esc(s.detail)}</p><div class="service-meta"><span>Source: ${s.source_present?'imported':'not installed'}</span>${s.version?`<span>Build: ${esc(s.version)}</span>`:''}${s.host?`<span>${esc(s.host)}</span>`:''}</div></article>`).join('')}</div>`;
}
function render() {
  $('#navigation').innerHTML = pages.map(([key,title])=>`<button class="nav-item ${state.page===key?'active':''}" data-nav="${key}" ${state.page===key?'aria-current="page"':''}>${icon(key)}${title}</button>`).join('');
  $('#page-label').textContent = pages.find(p=>p[0]===state.page)?.[1] || 'Overview';
  if (!state.data) return;
  const renderers={overview,banking:bankingPage,payments,ledger,scenarios,runs,engines};
  $('#main').innerHTML = (state.data.unavailable?`<div class="banner">${esc(state.data.unavailable)}</div>`:'') + renderers[state.page]();
}
function navigate(page) { if (!pages.some(p=>p[0]===page)) return; state.page=page; render(); if(page==='banking')refresh(true); $('#main').focus({preventScroll:true}); }
function openDialog(content) { $('#dialog-content').innerHTML=content; $('#dialog').showModal(); }
const dialogHeader=(title,description)=>`<div class="dialog-heading"><div><h2>${esc(title)}</h2><p>${esc(description)}</p></div><button class="close-button" data-action="close" aria-label="Close dialog">×</button></div>`;
function paymentDialog() {
  if (!state.data.accounts.length) return toast('Wait for account provisioning or restore the accounting engine.');
  const options=role=>state.data.accounts.map(a=>`<option value="${esc(a.id)}" ${a.role===role?'selected':''}>${esc(a.name)} · ${currency(a.balance_paise)}</option>`).join('');
  openDialog(dialogHeader('Create a payment','Synthetic INR transfer within this environment.')+`<form id="payment-form"><label class="field">Payer<select name="payer_id">${options('payer')}</select></label><label class="field">Beneficiary<select name="payee_id">${options('payee')}</select></label><label class="field">Amount in rupees<input name="rupees" inputmode="decimal" placeholder="1,250.00" value="1250.00" required autocomplete="off"><span class="hint">The API stores integer paise. No real funds move.</span></label><label class="field">Network behaviour<select name="fault"><option value="none">Normal response</option><option value="lost_response">Drop response after ledger commit</option></select></label><label class="field">Request key<input class="mono" name="request_key" value="${crypto.randomUUID()}" maxlength="160" required><span class="hint">Reusing this key requires the identical financial payload.</span></label><div class="form-error" id="form-error" role="alert"></div><div class="form-actions"><span>Local simulation only</span><button class="btn primary" type="submit">Submit payment →</button></div></form>`);
}
async function startRun(key, button) {
  if (button) button.disabled=true;
  try { const r=await api(scope()+'/runs',{scenario_key:key,request_key:crypto.randomUUID()}); state.selectedRun=r.id;state.run=null; state.page='runs'; await refresh(true); toast('Scenario queued with a dedicated ledger.'); }
  catch(error){toast(error.message);} finally{if(button?.isConnected)button.disabled=false;}
}
async function startPoc(button) {
  const path=scope(), version=state.scopeVersion;
  const cases=state.catalog.scenarios.filter(s=>s.key.startsWith('poc_'));
  button.disabled=true;
  let accepted=0, last=null;
  try {
    for(const item of cases){last=await api(path+'/runs',{scenario_key:item.key,request_key:crypto.randomUUID()});accepted++;}
    if(version===state.scopeVersion){state.selectedRun=last?.id||null;state.run=null;state.page='runs';await refresh(true);}
    toast(`${accepted} POC demonstrations queued. Results and evidence update as each completes.`);
  }catch(error){toast(`${accepted} demonstrations queued. ${error.message}`);}
  finally{if(button.isConnected)button.disabled=false;}
}
document.addEventListener('click',async event=>{
  const button=event.target.closest('button, [data-open-run]'); if(!button)return;
  if(button.dataset.nav){navigate(button.dataset.nav);if(state.page==='engines'){await reloadCatalog();render();}return;}
  if(button.dataset.run)return startRun(button.dataset.run,button);
  if(button.dataset.action==='run-poc')return startPoc(button);
  if(button.dataset.openRun){state.selectedRun=button.dataset.openRun;state.run=null;return refresh(true);}
  if(button.dataset.reconcile){button.disabled=true;try{await api(scope()+'/payments/'+button.dataset.reconcile+'/reconcile',{});await refresh(true);toast('Original payment intent queued for reconciliation.');}catch(error){toast(error.message);}finally{if(button.isConnected)button.disabled=false;}return;}
  if(button.dataset.download){try{const data=await api(scope()+'/runs/'+button.dataset.download+'/evidence');const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=`avenra-${button.dataset.download}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){toast(error.message);}return;}
  if(button.dataset.payment){const p=state.data.payments.find(p=>p.id===button.dataset.payment);if(!p)return;openDialog(dialogHeader('Payment details','Persisted intent and confirmed engine outcome.')+`<div class="receipt">${badge(p.state)}<br><strong>${currency(p.amount_paise)}</strong><br>${esc(p.payer_name)} → ${esc(p.payee_name)}<br><span class="mono">${esc(p.id)}</span></div><label class="field">Request key<pre>${esc(p.request_key)}</pre></label><label class="field">Ledger transaction<pre>${esc(p.ledger_transaction_id || 'Not confirmed')}</pre></label>${p.reason?`<div class="banner">${esc(p.reason)}</div>`:''}<button class="btn" data-action="close">Close</button>`);return;}
  const action=button.dataset.action;
  if(action==='close')$('#dialog').close();
  if(action==='payment')paymentDialog();
  if(action==='retry')refresh(true);
  if(action==='environment')openDialog(dialogHeader('New environment','A separate ledger with a configurable banking profile or the original regression fixture.')+'<form id="environment-form"><label class="field">Environment name<input name="name" placeholder="UPI resilience testing" maxlength="120" required></label><label class="field">Starting profile<select name="fixture_profile"><option value="banking">Banking workspace — configure your institutions and accounts</option><option value="legacy">Original payment regression fixture</option></select></label><div id="form-error" class="form-error" role="alert"></div><div class="form-actions"><span>Separate financial state</span><button class="btn primary">Create environment →</button></div></form>');
  if(action==='replay' && state.lastPayment?.scope===scope()){button.disabled=true;try{const p=await api(scope()+'/payments',state.lastPayment.body);toast(`Original intent ${p.id.slice(0,15)}… · ${labels[p.state] || p.state}`);await refresh(true);}catch(error){toast(error.message);}finally{if(button.isConnected)button.disabled=false;}}
});
document.addEventListener('keydown',event=>{if(event.key==='Enter' && event.target.matches('[data-open-run]'))event.target.click();});
document.addEventListener('change',async event=>{
  if(event.target.id!=='tenant-select' && event.target.id!=='environment-select')return;
  state.scopeVersion++;state.data=null;state.selectedRun=null;state.run=null;state.signature='';state.bankData=null;state.bankRun=null;state.selectedBankRun=null;
  if(event.target.id==='tenant-select'){state.tenant=event.target.value;state.environment=null;chooseScope();}else{state.environment=event.target.value;persistScope();}
  $('#main').innerHTML='<div class="loading"><span class="spinner"></span>Loading environment…</div>';
  while(state.loading)await new Promise(r=>setTimeout(r,50));
  await refresh(true);
});
document.addEventListener('submit',async event=>{
  if(!['payment-form','environment-form'].includes(event.target.id))return;event.preventDefault();
  const form=event.target,values=Object.fromEntries(new FormData(form)),button=form.querySelector('button[type="submit"],button.btn.primary');button.disabled=true;
  const base=scope(),version=state.scopeVersion;
  try {
    if(form.id==='payment-form'){
      const match=values.rupees.trim().match(/^(\d{1,7})(?:\.(\d{1,2}))?$/);if(!match)throw new Error('Enter rupees with up to two decimal places and no commas.');
      const paise=Number(match[1])*100+Number((match[2]||'').padEnd(2,'0'));if(!Number.isSafeInteger(paise)||paise<1||paise>100000000)throw new Error('Enter an amount between ₹0.01 and ₹10,00,000.');
      const body={payer_id:values.payer_id,payee_id:values.payee_id,amount_paise:paise,request_key:values.request_key,fault:values.fault};
      await api(base+'/payments',body);state.lastPayment={scope:base,body};if(version===state.scopeVersion)state.page='payments';toast('Payment intent accepted. Its financial outcome will update shortly.');
    }else{
      const env=await api(`/api/tenants/${state.tenant}/environments`,{name:values.name,fixture_profile:values.fixture_profile});await reloadCatalog();state.environment=env.id;persistScope();state.data=null;state.selectedRun=null;state.run=null;state.bankData=null;state.bankRun=null;state.selectedBankRun=null;if(values.fixture_profile==='banking')state.page='banking';toast('Environment created. The ledger is provisioning.');
    }
    $('#dialog').close();await refresh(true);
  }catch(error){$('#form-error').textContent=error.message;}finally{if(button.isConnected)button.disabled=false;}
});
$('#refresh').addEventListener('click',async()=>{await reloadCatalog();await refresh(true);});
$('#dialog').addEventListener('click',event=>{if(event.target===$('#dialog')){const r=$('#dialog').getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)$('#dialog').close();}});
(async()=>{
  try{const session=await api('/api/session',{}, {'X-Avenra-Client':'console'});state.csrf=session.csrf;await reloadCatalog();await refresh(true);setInterval(()=>{if(!document.hidden)refresh();},2500);}
  catch(error){$('#main').innerHTML=`<div class="banner">${esc(error.message)}</div>${error.code==='SSO_REQUIRED'?'<a class="btn primary" href="/auth/login">Sign in with your institution →</a>':''}`;}
})();
