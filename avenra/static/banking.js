/* Avenra banking workspace. All financial state is read through scoped APIs. */
const bankingTabs=[['upi','UPI participants'],['institutions','Institutions & accounts'],['operations','Interbank payments'],['mandates','Mandates'],['settlement','Settlement'],['consent','Financial data'],['risk','Risk policies'],['cases','Custom scenarios'],['simulation','Institution lab'],['integrations','Integrations']];
const bankingTemplate={payer_opening_paise:50000,payee_opening_paise:10000,amount_paise:1000,fault:'lost_credit_response',reconcile:true,expected_state:'succeeded',expected_payer_paise:49000,expected_payee_paise:11000};
const bankTokens=new Map();
const bankPath=()=>scope()+'/banking';
function bankTable(headings,rows){return rows.length?`<div class="table-wrap"><table><thead><tr>${headings.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`:'<div class="empty">No records in this environment yet.</div>';}
function bankPanel(title,action,body){return `<section class="panel bank-panel"><div class="panel-heading"><h2>${esc(title)}</h2>${action}</div>${body}</section>`;}
function bankButton(text,action,id='',primary=false){return `<button class="btn ${primary?'primary':''} small" data-bank-action="${esc(action)}" data-bank-id="${esc(id)}">${esc(text)}</button>`;}
function bankOptions(items,key='id',display='name'){return items.map(x=>`<option value="${esc(x[key])}">${esc(x[display]||x[key])}</option>`).join('');}
function bankSelect(name,title,items,key='id',display='name',required=true){return `<label class="field">${esc(title)}<select name="${esc(name)}" ${required?'required':''}>${bankOptions(items,key,display)}</select></label>`;}
function bankInput(name,title,value='',type='text'){return `<label class="field">${esc(title)}<input name="${esc(name)}" type="${esc(type)}" value="${esc(value)}" required autocomplete="off"></label>`;}
function bankRupees(value){const m=String(value).trim().match(/^(\d{1,7})(?:\.(\d{1,2}))?$/);if(!m)throw new Error('Use rupees with up to two decimals and no commas.');const n=Number(m[1])*100+Number((m[2]||'').padEnd(2,'0'));if(!Number.isSafeInteger(n)||n>100000000)throw new Error('Amount exceeds this profile.');return n;}
function bankingPage(){
 const b=state.bankData;
 const intro=header('Banking workspace','Configure institutions, exercise financial lifecycles and inspect authoritative results.','');
 if(!b)return intro+'<div class="loading"><span class="spinner"></span>Loading banking state…</div>';
 const nav=`<div class="bank-tabs" role="navigation" aria-label="Banking capabilities">${bankingTabs.map(([id,name])=>`<button class="btn small ${state.bankTab===id?'primary':''}" data-bank-tab="${id}" ${state.bankTab===id?'aria-current="page"':''}>${name}</button>`).join('')}</div>`;
 let body='';
 if(state.bankTab==='upi'){
  body=upiPage(b.upi);
 } else if(state.bankTab==='institutions'){
  body=bankPanel('Virtual institutions',bankButton('＋ Create bank','bank'),`<div class="bank-cards">${b.banks.map(x=>`<article><div class="service-title"><h3>${esc(x.name)}</h3>${badge(x.status)}</div><p class="mono">${esc(x.code)}</p><p>${JSON.parse(x.roles_json).map(esc).join(' · ')}</p></article>`).join('')||'<div class="empty">Create virtual banks to start participant testing.</div>'}</div>`);
  body+=bankPanel('Customers & merchants',bankButton('＋ Add customer','customer'),bankTable(['Name','Kind','Identity'],b.customers.map(x=>`<tr><td>${esc(x.name)}</td><td>${esc(x.kind)}</td><td class="mono">${esc(x.id)}</td></tr>`)));
  body+=bankPanel('Bank accounts',bankButton('＋ Open account','account'),bankTable(['Account','Institution','Address','Balance','State',''],b.accounts.map(x=>`<tr><td>${esc(x.name)}</td><td>${esc(x.bank_name)}</td><td class="mono">${esc(x.vpa)}</td><td>${x.balance_paise===null?'Unconfirmed':currency(x.balance_paise)}</td><td>${badge(x.status)}</td><td>${['active','blocked'].includes(x.status)?bankButton(x.status==='active'?'Block':'Activate','account-state',x.account_id):''}</td></tr>`)));
 } else if(state.bankTab==='operations'){
  body=bankPanel('Interbank operations',bankButton('＋ New bank payment','operation','',true),bankTable(['Operation','Kind','Amount','Outcome','Fault',''],b.operations.map(x=>`<tr><td class="mono">${esc(x.id)}</td><td>${esc(x.kind)}</td><td>${currency(x.amount_paise)}</td><td>${badge(x.state)}</td><td>${esc(x.fault)}</td><td>${bankButton('Inspect','inspect',x.id)}</td></tr>`)));
  body+=bankPanel('Disputes',bankButton('＋ Open dispute','dispute'),bankTable(['Operation','Reason','State',''],b.disputes.map(x=>`<tr><td class="mono">${esc(x.operation_id)}</td><td>${esc(x.reason)}</td><td>${badge(x.state)}</td><td>${x.state==='open'?bankButton('Start review','dispute-review',x.id):x.state==='reviewing'?bankButton('Resolve','dispute-resolve',x.id):esc(x.resolution||'')}</td></tr>`)));
 } else if(state.bankTab==='mandates'){
  body=bankPanel('Synthetic mandates',bankButton('＋ Create mandate','mandate'),bankTable(['Mandate','Limit per execution','State','Expires','Actions'],b.mandates.map(x=>`<tr><td class="mono">${esc(x.id)}</td><td>${currency(x.maximum_paise)}</td><td>${badge(x.state)}</td><td>${esc(new Date(x.expires_at*1000).toLocaleString())}</td><td><div class="bank-actions">${x.state==='pending'?bankButton('Authorize','mandate-approve',x.id):''}${x.state==='active'?bankButton('Schedule debit','mandate-execute',x.id):''}${x.state!=='revoked'?bankButton('Revoke','mandate-revoke',x.id):''}</div></td></tr>`)));
 } else if(state.bankTab==='settlement'){
  body=bankPanel('Synthetic settlement cycles',bankButton('＋ Prepare cycle','cycle'),bankTable(['Cycle','State','Expected participant positions',''],b.cycles.map(x=>`<tr><td>${esc(x.name)}</td><td>${badge(x.state)}</td><td><pre>${esc(JSON.stringify(JSON.parse(x.positions_json),null,2))}</pre></td><td>${bankButton('Import observations','cycle-reconcile',x.id)}</td></tr>`)));
  body+='<p class="bank-note">Preparation computes expected obligations. Reconciliation compares a separately supplied report and retains discrepancies. No external settlement funds move.</p>';
 } else if(state.bankTab==='consent'){
  body='<div class="bank-actions bank-note"><button class="btn" data-aa-action="open">AA encrypted data exchange ↗</button></div>';
  body+=bankPanel('Consent & financial data',bankButton('＋ Request consent','consent'),bankTable(['Purpose','Customer','State','Expires','Actions'],b.consents.map(x=>`<tr><td>${esc(x.purpose)}</td><td>${esc(b.customers.find(c=>c.id===x.customer_id)?.name||x.customer_id)}</td><td>${badge(x.expires_at*1000<Date.now()?'expired':x.state)}</td><td>${esc(new Date(x.expires_at*1000).toLocaleString())}</td><td><div class="bank-actions">${x.state==='pending'?bankButton('Authorize','consent-approve',x.id):''}${x.state==='active'?bankButton('Fetch data','consent-data',x.id)+bankButton('Revoke','consent-revoke',x.id):''}</div></td></tr>`)));
  body+='<p class="bank-note">This profile tests local consent scope, expiry, revocation and ledger-backed data. Official AA interoperability qualification is separate.</p>';
 } else if(state.bankTab==='risk'){
  body=bankPanel('Versioned synthetic policies',bankButton('＋ Publish policy','rule'),bankTable(['Policy','Version','Threshold','Action','Enabled'],b.rules.map(x=>`<tr><td>${esc(x.name)}</td><td>${x.version}</td><td>${currency(x.threshold_paise)}</td><td>${esc(x.action)}</td><td>${x.enabled?'Yes':'No'}</td></tr>`)));
  body+='<p class="bank-note">The highest published version of each policy is used for new operations. Existing decisions retain their original rule versions. This is Avenra’s local threshold engine.</p>';
  body+=bankPanel('Post-payment monitoring',bankButton('Analyze a payment','analysis'),bankTable(['Analysis','State','Observation',''],(b.analysis||[]).map(x=>`<tr><td class="mono">${esc(x.id)}</td><td>${badge(x.state)}</td><td>${esc(x.error||'Evaluation retained')}</td><td>${bankButton('Inspect report','analysis-inspect',x.id)}</td></tr>`)));
  body+='<p class="bank-note">Configured monitoring receives confirmed synthetic payments. Reports retain rule versions and evidence signatures. These post-payment results do not authorize payments.</p>';
 } else if(state.bankTab==='cases'){
  body=bankPanel('Published scenario plans',bankButton('＋ Publish scenario','case'),bankTable(['Scenario','Version','Plan hash',''],b.cases.map(x=>`<tr><td>${esc(x.name)}</td><td>${x.version}</td><td class="mono">${esc(x.plan_hash.slice(0,20))}…</td><td>${bankButton('Run in fresh fixture','case-run',x.id)}</td></tr>`)));
  body+=bankPanel('Banking test runs','',bankTable(['Run','State','Created',''],b.test_runs.map(x=>`<tr><td class="mono">${esc(x.id)}</td><td>${badge(x.state)}</td><td>${esc(relative(x.created_at))}</td><td>${bankButton('Inspect evidence','case-inspect',x.id)}</td></tr>`)));
  if(state.bankRun?.evidence){const e=state.bankRun.evidence;body+=bankPanel('Retained evidence',bankButton('↓ Download JSON','case-download',state.bankRun.id),`${bankTable(['Assertion','Expected','Observed','Result'],e.manifest.checks.map(c=>`<tr><td>${esc(c.name)}</td><td>${esc(JSON.stringify(c.expected))}</td><td>${esc(JSON.stringify(c.observed))}</td><td>${badge(c.passed?'passed':'failed')}</td></tr>`))}<p class="bank-note mono">SHA-256 ${esc(e.manifest_hash)}</p>`);}
 } else if(state.bankTab==='simulation'){
  body=simulationPage(b.simulation);
 } else{
  body=bankPanel('Service integration status',bankButton('Refresh services','services'),`<div class="service-grid">${state.catalog.services.map(s=>`<article class="service-card"><div class="service-title"><h3>${esc(s.name)}</h3>${badge(s.state)}</div><p>${esc(s.detail)}</p></article>`).join('')}</div>`);
  body+=bankPanel('Banking product commands',bankButton('＋ Submit core command','core-command'),bankTable(['Action','Outcome','Created',''],(b.core_commands||[]).map(x=>`<tr><td>${esc(x.action)}</td><td>${badge(x.state)}</td><td>${esc(relative(x.created_at))}</td><td>${bankButton('Inspect result','core-inspect',x.id)}</td></tr>`)));
  body+=bankPanel('Provider API contracts',bankButton('＋ Import OpenAPI contract','contract'),'<p class="bank-note" style="padding:0 24px">Contracts are namespaced to this environment. External references and executable dispatch extensions are disabled.</p>');
  body+=bankPanel('Signed callbacks',bankButton('＋ Register destination','callback'),bankTable(['Destination','URL','State'],(b.callback_destinations||[]).map(x=>`<tr><td>${esc(x.name)}</td><td class="mono">${esc(x.url)}</td><td>${badge(x.status)}</td></tr>`)));
  body+=bankPanel('Notification deliveries','',bankTable(['Event','Operation','State'],(b.notifications||[]).map(x=>`<tr><td class="mono">${esc(x.id)}</td><td class="mono">${esc(x.operation_id)}</td><td>${badge(x.state)}</td></tr>`)));
  body+='<p class="bank-note">Only verified service connections appear connected. Source availability does not imply runtime readiness or institutional qualification.</p>';
 }
 return intro+nav+body;
}
function bankForm(action,title,description,fields,id=''){
 openDialog(dialogHeader(title,description)+`<form id="bank-form" data-kind="${esc(action)}" data-identity="${esc(id)}" data-request-key="${crypto.randomUUID()}">${fields}<div class="form-error" id="form-error" role="alert"></div><div class="form-actions"><span>Synthetic financial profile</span><button type="submit" class="btn primary">Submit →</button></div></form>`);
}
function bankDialog(action,id){
 const b=state.bankData,accounts=b.accounts.filter(x=>x.status==='active'),pair=()=>bankSelect('payer_id','Payer',accounts,'account_id')+bankSelect('payee_id','Beneficiary',accounts,'account_id');
 if(action==='bank')bankForm(action,'Create virtual bank','Configure an institution for this environment.',bankInput('name','Institution name')+bankInput('code','Institution code, e.g. bank_a'));
 if(action==='customer')bankForm(action,'Add customer or merchant','Synthetic test identity.',bankInput('name','Display name')+bankSelect('kind','Kind',[{id:'customer',name:'Customer'},{id:'merchant',name:'Merchant'}]));
 if(action==='account'){
  if(!b.banks.length||!b.customers.length)return toast('Create a bank and a customer first.');
  bankForm(action,'Open bank account','Opening funds are recorded as an idempotent ledger posting.',bankSelect('bank_id','Institution',b.banks)+bankSelect('customer_id','Customer',b.customers)+bankInput('vpa','Local address, e.g. customer@bank_a')+bankInput('opening','Opening funds in rupees','5000.00'));
 }
 if(action==='operation'){
  if(accounts.length<2)return toast('Open and fund two bank accounts first.');
  bankForm(action,'Interbank payment','Separate payer debit and beneficiary credit, with retained message identities.',pair()+bankInput('amount','Amount in rupees','250.00')+bankSelect('fault','Test condition',[{id:'none',name:'Normal response'},{id:'lost_debit_response',name:'Lose debit acknowledgement'},{id:'lost_credit_response',name:'Lose credit acknowledgement'},{id:'reject_credit',name:'Reject credit and reverse debit'},{id:'reject_authorization',name:'Reject authorization'}])+bankSelect('callback_id','Notification destination',[{id:'',name:'No external callback'},...(b.callback_destinations||[])],'id','name',false)+bankInput('request_key','Request key',crypto.randomUUID()));
  $('#bank-form select[name="payee_id"]').selectedIndex=1;
 }
 if(action==='refund'||action==='reversal')bankForm(action,action==='refund'?'Refund payment':'Reverse payment','Return funds to the original payer under a separate financial identity.',bankInput('amount','Amount in rupees')+bankInput('request_key','Request key',crypto.randomUUID()),id);
 if(action==='mandate'){bankForm(action,'Create mandate','Synthetic authorization is required before scheduling an execution.',pair()+bankInput('maximum','Maximum per execution in rupees','100.00')+bankInput('hours','Validity in hours','24','number'));if(accounts.length>1)$('#bank-form select[name="payee_id"]').selectedIndex=1;}
 if(action==='mandate-execute')bankForm(action,'Schedule mandate debit','Occurrence identity prevents repeated scheduled debits.',bankInput('occurrence','Occurrence identity',crypto.randomUUID())+bankInput('amount','Amount in rupees','10.00')+bankInput('delay','Delay in seconds','0','number'),id);
 if(action==='dispute')bankForm(action,'Open dispute','Case handling preserves the original financial record.',bankSelect('operation_id','Operation',b.operations,'id','id')+bankInput('reason','Reason'),id);
 if(action==='dispute-review'||action==='dispute-resolve')bankForm(action,'Record dispute decision','Financial adjustments use separate refund operations.',bankInput('resolution','Decision and supporting reference'),id);
 if(action==='cycle')bankForm(action,'Prepare settlement cycle','Assign confirmed operations once and compute participant obligations.',bankInput('name','Cycle name','Synthetic cycle '+new Date().toLocaleDateString()));
 if(action==='cycle-reconcile')bankForm(action,'Import observed positions','Paste independently observed institution positions as signed integer paise.',`<label class="field">Observed positions JSON<textarea name="positions" class="bank-json" required placeholder='{"bank_id": -5000, "other_bank_id": 5000}'></textarea></label>`,id);
 if(action==='consent')bankForm(action,'Request financial data consent','Select an account owned by the selected customer.',bankSelect('customer_id','Customer',b.customers)+bankSelect('account_id','Account',accounts,'account_id','vpa')+bankInput('purpose','Purpose','Lending eligibility test')+bankInput('hours','Validity in hours','1','number'));
 if(action==='rule')bankForm(action,'Publish policy version','Publishing the same name creates its next immutable version.',bankInput('name','Policy name','High-value review')+bankInput('threshold','Threshold in rupees','1000.00')+bankSelect('action','Action',[{id:'alert',name:'Record alert'},{id:'block',name:'Block before debit'}]));
 if(action==='case')bankForm(action,'Publish scenario plan','Expectations are declared independently and checked against actual ledger effects.',bankInput('name','Scenario name','Credit acknowledgement recovery')+`<label class="field">Scenario JSON<textarea class="bank-json" name="plan" required>${esc(JSON.stringify(bankingTemplate,null,2))}</textarea></label>`);
 if(action==='callback')bankForm(action,'Register callback destination','Use an operator-approved origin. The default local test receiver uses port 4888.',bankInput('name','Destination name','Local integration client')+bankInput('url','Callback URL','http://127.0.0.1:4888/notifications'));
 if(action==='analysis')bankForm(action,'Analyze a confirmed payment','Requires a configured analysis service. Replays retain the original message identities.',bankSelect('operation_id','Confirmed payment',b.operations.filter(x=>x.state==='succeeded'),'id','id'));
 if(action==='contract')bankForm(action,'Import provider API contract','Supply OpenAPI 3 JSON with examples. Imported mocks remain separate from financial accounting.',`<label class="field">Specification JSON<textarea name="specification" class="bank-json" required></textarea></label>`);
 if(action==='core-command')bankForm(action,'Submit banking product command','Requires an operator-configured banking service. Unknown external outcomes are retained for investigation.',bankSelect('action','Action',[{id:'create_client',name:'Create client'},{id:'create_loan_product',name:'Create loan product'},{id:'apply_loan',name:'Apply for loan'},{id:'approve_loan',name:'Approve loan'},{id:'disburse_loan',name:'Disburse loan'},{id:'repay_loan',name:'Record repayment'},{id:'create_savings_product',name:'Create savings product'},{id:'apply_savings',name:'Apply for savings account'},{id:'approve_savings',name:'Approve savings account'},{id:'activate_savings',name:'Activate savings account'},{id:'deposit_savings',name:'Record deposit'},{id:'withdraw_savings',name:'Record withdrawal'}])+bankInput('resource_id','Existing resource ID, or 0 for creation','0','number')+`<label class="field">Command body JSON<textarea name="body" class="bank-json" required>{}</textarea></label>`+bankInput('request_key','Request key',crypto.randomUUID()));
}
document.addEventListener('click',async event=>{
 const button=event.target.closest('[data-bank-action],[data-bank-tab]');if(!button)return;
 if(button.dataset.bankTab){state.bankTab=button.dataset.bankTab;render();return;}
 const action=button.dataset.bankAction,id=button.dataset.bankId,base=bankPath(),version=state.scopeVersion;
 if(action?.startsWith('simulation-')||action?.startsWith('upi-'))return;
 const dialogs=['bank','customer','account','operation','refund','reversal','mandate','mandate-execute','dispute','dispute-review','dispute-resolve','cycle','cycle-reconcile','consent','rule','case','callback','contract','core-command','analysis'];
 if(dialogs.includes(action))return bankDialog(action,id);
 button.disabled=true;
 try{
  if(action==='inspect'){
   const x=await api(base+'/operations/'+id);
   if(version!==state.scopeVersion)return;
   openDialog(dialogHeader('Bank payment lifecycle',x.id)+`<div class="receipt">${badge(x.state)} <strong>${currency(x.amount_paise)}</strong>${x.reason?`<p>${esc(x.reason)}</p>`:''}</div>${bankTable(['Financial leg','State','Ledger identity'],x.legs.map(l=>`<tr><td>${esc(l.kind)}</td><td>${badge(l.state)}</td><td class="mono">${esc(l.ledger_id||'Unconfirmed')}</td></tr>`))}<details class="bank-message-detail"><summary>Participant message timeline</summary><pre>${esc(JSON.stringify(x.messages.map(m=>({kind:m.kind,sender:m.sender,recipient:m.recipient,payload:JSON.parse(m.payload_json)})),null,2))}</pre></details><div class="bank-actions">${['unknown','pending','processing'].includes(x.state)?bankButton('Reconcile original operation','operation-reconcile',id):''}${x.kind==='transfer'&&x.state==='succeeded'?bankButton('Refund','refund',id)+bankButton('Reverse','reversal',id):''}</div>`);
  }else if(action==='operation-reconcile'){await api(base+'/operations/'+id+'/reconcile',{});$('#dialog').close();toast('Original financial identities queued for reconciliation.');}
  else if(action==='account-state'){const a=state.bankData.accounts.find(x=>x.account_id===id);await api(base+'/accounts/'+id+'/state',{state:a.status==='active'?'blocked':'active'});}
  else if(action==='mandate-approve'||action==='mandate-revoke')await api(base+'/mandates/'+id+'/'+(action==='mandate-approve'?'approve':'revoke'),{synthetic_authorization:true});
  else if(action==='consent-approve'){const c=await api(base+'/consents/'+id+'/approve',{synthetic_authorization:true});bankTokens.set(id,c.access_token);toast('Synthetic consent authorized. Data access is now available in this session.');}
  else if(action==='consent-revoke'){await api(base+'/consents/'+id+'/revoke',{});bankTokens.delete(id);}
  else if(action==='consent-data'){
   if(!bankTokens.has(id)){const c=await api(base+'/consents/'+id+'/approve',{synthetic_authorization:true});bankTokens.set(id,c.access_token);}
   const data=await api(base+'/consents/'+id+'/data',undefined,{'X-Avenra-Consent':bankTokens.get(id)});openDialog(dialogHeader('Consented financial data','Balances and history read from the authoritative ledger.')+`<pre class="bank-json-output">${esc(JSON.stringify(data,null,2))}</pre>`);
  }else if(action==='case-run'){const r=await api(base+'/test-runs',{case_id:id,request_key:crypto.randomUUID()});state.selectedBankRun=r.id;toast('Banking scenario queued in a fresh fixture.');}
  else if(action==='case-inspect'){state.selectedBankRun=id;state.bankRun=await api(base+'/test-runs/'+id);}
  else if(action==='case-download'){const data=await api(base+'/test-runs/'+id+'/evidence');const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='avenra-'+id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  else if(action==='core-inspect'){const x=state.bankData.core_commands.find(x=>x.id===id);openDialog(dialogHeader('Banking product result','Persisted command identity and service observation.')+`<pre class="bank-json-output">${esc(JSON.stringify(x,null,2))}</pre>`);}
  else if(action==='analysis-inspect'){
   const x=await api(base+'/analysis/'+id),e=x.evidence,r=e?.manifest?.report?.report,types=r?.tadpResult?.typologyResult||[];
   const rules=types.flatMap(t=>(t.ruleResults||[]).map(rule=>`<tr><td class="mono">${esc(rule.id)}</td><td>${esc(rule.cfg)}</td><td>${esc(rule.subRuleRef)}</td><td>${esc(rule.indpdntVarbl)}</td><td>${esc(rule.wght)}</td></tr>`));
   openDialog(dialogHeader('Monitoring evidence',e?.valid?'Evidence signature verified.':'Evaluation or recovery is pending.')+(r?`<div class="bank-note">${badge(r.status==='ALRT'?'Review required':'Evaluation completed')}<p>Operation ${esc(e.manifest.operation_id)}</p><p>Post-payment analysis · ${types.map(t=>'Score '+esc(t.result)+' / alert threshold '+esc(t.workflow?.alertThreshold)).join(' · ')}</p></div>${bankTable(['Rule','Version','Band','Observed value','Weight'],rules)}`:'')+`<details class="bank-message-detail"><summary>View evidence JSON</summary><pre class="bank-json-output">${esc(JSON.stringify(e||x,null,2))}</pre></details>`);
  }
  else if(action==='services')await reloadCatalog();
  if(version===state.scopeVersion)await refresh(true);
 }catch(error){toast(error.message);}finally{if(button.isConnected)button.disabled=false;}
});
document.addEventListener('submit',async event=>{
 if(event.target.id!=='bank-form')return;event.preventDefault();
 const form=event.target,v=Object.fromEntries(new FormData(form)),kind=form.dataset.kind,id=form.dataset.identity,base=bankPath(),button=form.querySelector('button[type="submit"]'),version=state.scopeVersion;
 button.disabled=true;
 try{
  let path,body;const key=form.dataset.requestKey;
  if(kind==='bank'){path='banks';body={name:v.name,code:v.code,request_key:key};}
  else if(kind==='customer'){path='customers';body={name:v.name,kind:v.kind,request_key:key};}
  else if(kind==='account'){path='accounts';body={bank_id:v.bank_id,customer_id:v.customer_id,vpa:v.vpa,opening_paise:bankRupees(v.opening),request_key:key};}
  else if(kind==='operation'){path='operations';body={payer_id:v.payer_id,payee_id:v.payee_id,amount_paise:bankRupees(v.amount),fault:v.fault,request_key:v.request_key,...(v.callback_id?{callback_id:v.callback_id}:{})};}
  else if(kind==='refund'||kind==='reversal'){path='operations/'+id+'/'+kind;body={amount_paise:bankRupees(v.amount),request_key:v.request_key};}
  else if(kind==='mandate'){path='mandates';body={payer_id:v.payer_id,payee_id:v.payee_id,maximum_paise:bankRupees(v.maximum),starts_at:Date.now()/1000,expires_at:Date.now()/1000+Number(v.hours)*3600,request_key:key};}
  else if(kind==='mandate-execute'){path='mandates/'+id+'/execute';body={occurrence:v.occurrence,amount_paise:bankRupees(v.amount),due_at:Date.now()/1000+Number(v.delay)};}
  else if(kind==='dispute'){path='disputes';body={operation_id:v.operation_id,reason:v.reason,request_key:key};}
  else if(kind==='dispute-review'||kind==='dispute-resolve'){path='disputes/'+id+'/state';body={state:kind==='dispute-review'?'reviewing':'resolved',resolution:v.resolution};}
  else if(kind==='cycle'){path='cycles';body={name:v.name,request_key:key};}
  else if(kind==='cycle-reconcile'){path='cycles/'+id+'/reconcile';body={positions:JSON.parse(v.positions)};}
  else if(kind==='consent'){path='consents';body={customer_id:v.customer_id,account_ids:[v.account_id],purpose:v.purpose,expires_at:Date.now()/1000+Number(v.hours)*3600,request_key:key};}
  else if(kind==='rule'){path='rules';body={name:v.name,threshold_paise:bankRupees(v.threshold),action:v.action};}
  else if(kind==='case'){path='cases';body={name:v.name,plan:JSON.parse(v.plan)};}
  else if(kind==='callback'){path='callbacks';body={name:v.name,url:v.url,request_key:key};}
  else if(kind==='analysis'){path='analysis';body={operation_id:v.operation_id};}
  else if(kind==='contract'){path='contracts';body={specification:JSON.parse(v.specification)};}
  else if(kind==='core-command'){path='core-commands';body={action:v.action,resource_id:Number(v.resource_id)||undefined,body:JSON.parse(v.body),request_key:v.request_key};}
  const result=await api(base+'/'+path,body);
  if(kind==='callback'&&result.signing_secret){openDialog(dialogHeader('Callback registered','Save this local verification secret in your integration client configuration.')+`<pre class="bank-json-output">${esc(result.signing_secret)}</pre><button class="btn" data-action="close">Close</button>`);if(version===state.scopeVersion)await refresh(true);return;}
  $('#dialog').close();if(version===state.scopeVersion)await refresh(true);toast('Banking request recorded. Financial outcomes update from the ledger.');
 }catch(error){$('#form-error').textContent=error.message;}finally{if(button.isConnected)button.disabled=false;}
});
