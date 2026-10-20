/* Additional lifecycle controls within the existing Avenra console. */
let fidelityData=null;
const fidelityStyle=document.createElement('style');fidelityStyle.textContent='dialog:has(.fidelity-view){width:min(1100px,calc(100vw - 30px))}.fidelity-view .table-wrap{overflow-x:auto}.fidelity-view td{overflow-wrap:anywhere}.fidelity-view h3{margin-top:30px}.fidelity-feedback{padding:14px 0;margin-top:12px}';document.head.append(fidelityStyle);
const fidelityBase=()=>bankPath()+'/fidelity';
const fidelityPinField=()=>'<label class="field">Synthetic test PIN<input name="synthetic_pin" type="password" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" required autocomplete="off"><small>Six test digits. Never use a real banking PIN.</small></label>';
const fidelityProofField=()=>'<label class="field">Synthetic device proof<input name="device_proof" type="password" required autocomplete="off"></label>';
const fidelityAccount=id=>state.bankData.accounts.find(a=>a.account_id===id)?.vpa||id;
function fidelityError(message){
 if(!$('#dialog').open)return toast(message);
 let target=$('#fidelity-feedback');if(!target){target=document.createElement('div');target.id='fidelity-feedback';target.className='form-error fidelity-feedback';target.setAttribute('role','alert');$('#dialog-content').append(target);}
 target.textContent=message;target.scrollIntoView({block:'nearest'});
}
function fidelityFill(device){const input=$('#fidelity-form [name="device_proof"]');if(input)input.value=upiProofs.get(upiProofKey(device))||'';}
function fidelityForm(kind,title,description,content,id=''){
 openDialog(dialogHeader(title,description)+`<form id="fidelity-form" data-kind="${esc(kind)}" data-id="${esc(id)}" data-scope="${state.scopeVersion}" data-request-key="${crypto.randomUUID()}">${content}<div class="form-error" id="fidelity-error" role="alert"></div><div class="form-actions"><span>Avenra synthetic lifecycle</span><button class="btn primary" type="submit">Continue →</button></div></form>`);
}
async function fidelityOpen(version=state.scopeVersion){
 const data=await api(fidelityBase());if(version!==state.scopeVersion)return;fidelityData=data;
 let html=dialogHeader('UPI lifecycle workflows','Additional workflows in this environment. Existing payment and accounting tools remain available.');
 html+=`<p class="bank-note">${esc(data.profile.id)} · ${data.enabled?'Enabled for newly prepared intents':'Optional profile; existing flows retain their behaviour'} · ${data.unknown_payments} uncertain payments</p>`;
 if(!data.enabled){html+=`<p class="bank-note">Enabling this profile adds synthetic credential checks and transaction budgets to new participant intents. Previously accepted intents keep their original recovery path.</p><div class="bank-actions">${bankButton('Enable lifecycle profile','fidelity-enable')}</div>`;openDialog(html);return;}
 html+=`<div class="bank-actions bank-note">${bankButton('Account discovery','fidelity-discover')}${bankButton('Set up / reset credential','fidelity-credential')}${bankButton('New recurring agreement','fidelity-mandate')}${bankButton('Open complaint','fidelity-complaint')}${bankButton('Prepare settlement cycle','fidelity-cycle')}${bankButton('Coverage & assumptions','fidelity-coverage')}${bankButton('Advance lab clock','fidelity-clock')}</div>`;
 html+=bankTable(['Account credential','Epoch','Failures','Cooling / lock'],data.credentials.map(c=>`<tr><td>${esc(fidelityAccount(c.account_id))}</td><td>${c.epoch}</td><td>${c.failed_attempts}</td><td>${c.cooling_until>data.business_time?'Cooling':c.locked_until>data.business_time?'Locked':'Ready'}</td></tr>`));
 html+='<h3>Recurring agreements</h3>'+bankTable(['Agreement','Maximum','State','Actions'],data.mandates.map(m=>`<tr><td>${esc(fidelityAccount(m.payer_id))} → ${esc(fidelityAccount(m.payee_id))}</td><td>${currency(m.maximum_paise)}</td><td>${badge(m.state)}</td><td><div class="bank-actions">${m.state==='pending'?bankButton('Approve','fidelity-mandate-approve',m.id):''}${m.state==='active'?bankButton('Schedule','fidelity-schedule',m.id)+bankButton('Pause','fidelity-mandate-pause',m.id):''}${m.state==='paused'?bankButton('Resume','fidelity-mandate-resume',m.id):''}${m.state!=='revoked'?bankButton('Revoke','fidelity-mandate-revoke',m.id):''}</div></td></tr>`));
 html+='<h3>Scheduled participant payments</h3>'+bankTable(['Occurrence','Amount','State','Last observation'],data.occurrences.map(o=>`<tr><td>${o.occurrence+1}</td><td>${currency(o.amount_paise)}</td><td>${badge(o.state)}</td><td>${esc(o.error||o.payment_id||'Waiting for due time')}</td></tr>`));
 html+='<h3>Complaints</h3>'+bankTable(['Original payment','State','Escalated','Actions'],data.complaints.map(c=>`<tr><td class="mono">${esc(c.payment_id)}</td><td>${badge(c.state)}</td><td>${c.escalated?'Yes':'No'}</td><td><div class="bank-actions">${bankButton('Inspect','fidelity-complaint-inspect',c.id)}${c.state==='open'?bankButton('Investigate','fidelity-investigate',c.id):''}${c.state==='investigating'?bankButton('Propose resolution','fidelity-resolve',c.id):''}</div></td></tr>`));
 html+='<h3>Synthetic settlement</h3>'+bankTable(['Cycle','Payments','Unresolved','Actions'],data.cycles.map(c=>`<tr><td class="mono">${esc(c.id)}</td><td>${c.manifest.items.length}</td><td>${c.manifest.unresolved.length}</td><td><div class="bank-actions">${bankButton('Report','fidelity-cycle-inspect',c.id)}${bankButton('Reconcile','fidelity-reconcile',c.id)}${bankButton('Propose release','fidelity-release',c.id)}</div></td></tr>`));
 html+='<h3>Approval queue</h3><p class="bank-note">A different authenticated principal must check each proposal. Local service identities model this workflow; institutional human-role acceptance remains separate.</p>'+bankTable(['Proposal','State','Action'],data.proposals.map(p=>`<tr><td>${esc(p.kind)}</td><td>${badge(p.state)}</td><td>${p.state==='pending'?bankButton('Check & approve','fidelity-approve',p.id):'Retained signed decision'}</td></tr>`));
 openDialog('<section class="fidelity-view" aria-label="UPI lifecycle workspace">'+html+'</section>');
}
async function fidelityAuthorize(id,payment,links){
 fidelityForm('authorize','Authorize exact payment',`${currency(payment.amount_paise)} → ${fidelityAccount(payment.payee_id)}. This profile verifies a synthetic credential and reserves its budget.`,bankSelect('device_id','Payer device',links.map(l=>({id:l.device_id,name:l.name})))+fidelityProofField()+fidelityPinField()+bankSelect('decision','Decision',[{id:'approve',name:'Approve this exact payment'},{id:'reject',name:'Reject payment'}]),id);fidelityFill(links[0].device_id);
}
document.addEventListener('click',async event=>{
 const button=event.target.closest('[data-bank-action^="fidelity-"]');if(!button)return;
 const action=button.dataset.bankAction.slice(9),id=button.dataset.bankId,version=state.scopeVersion,base=fidelityBase(),links=upiLinkOptions();
 try{
  if(action==='open')return await fidelityOpen(version);
  if(action==='coverage'){
   const coverage=await api(base+'/coverage');if(version!==state.scopeVersion)return;
   return openDialog(dialogHeader('Lifecycle coverage & assumptions','Cases are matched to the current source and synthetic profile. Not-run and unqualified items remain visible.')+bankTable(['Requirement','Observed cases','Status'],coverage.requirements.map(r=>`<tr><td>${esc(r.rule)}</td><td>${r.cases}</td><td>${badge(r.state)}</td></tr>`))+'<h3>External qualification</h3>'+bankTable(['Profile','Remaining'],coverage.external.map(r=>`<tr><td>${esc(r.id)} · ${badge(r.state)}</td><td>${esc(r.requires)}</td></tr>`)));
  }
  if(['discover','credential'].includes(action)){
   if(!links.length)throw new Error('Enrol, verify and link a device first.');
   fidelityForm(action,action==='discover'?'Discover own bank accounts':'Synthetic credential challenge','Device and account ownership are checked. This issuer is synthetic.',bankSelect('link_id','Account / device',links)+fidelityProofField()+(action==='credential'?bankSelect('purpose','Credential action',[{id:'setup',name:'Set up a new test credential'},{id:'reset',name:'Reset an existing test credential'}])+'<label class="field"><span><input name="verified" type="checkbox" required> Simulate the bank’s successful identity-verification result.</span></label>':''));fidelityFill(links[0].device_id);return;
  }
  if(action==='mandate')return fidelityForm(action,'Create recurring agreement','The payer must approve the exact agreement before any occurrence can execute.',bankSelect('link_id','Payer account / device',links)+bankSelect('payee_id','Merchant',state.bankData.accounts.filter(a=>state.bankData.customers.some(c=>c.id===a.customer_id&&c.kind==='merchant')),'account_id','vpa')+bankInput('maximum','Maximum per execution in rupees','100.00')+bankInput('interval','Synthetic interval in seconds','3600')+bankInput('starts_after','Start after seconds','60')+bankInput('occurrences','Number of occurrences','3'));
  if(action.startsWith('mandate-')){
   const m=fidelityData.mandates.find(m=>m.id===id);if(!m)throw new Error('Refresh lifecycle workflows.');
   fidelityForm(action,action.slice(8)+' recurring agreement',`${fidelityAccount(m.payer_id)} → ${fidelityAccount(m.payee_id)} · ${currency(m.maximum_paise)} maximum`,fidelityProofField()+fidelityPinField(),id);fidelityFill(m.device_id);return;
  }
  if(action==='schedule')return fidelityForm(action,'Schedule occurrence','Occurrences are zero-based, unique and must be scheduled before their due time.',bankInput('occurrence','Occurrence index','0')+bankInput('amount','Amount in rupees','50.00'),id);
  if(action==='complaint')return fidelityForm(action,'Open payment complaint','The case keeps the original payment and never treats a timeout as a decline.',bankSelect('payment_id','Original participant payment',state.bankData.upi.payments,'id','id')+bankSelect('reason','Reason',[{id:'status_mismatch',name:'Status mismatch'},{id:'beneficiary_not_credited',name:'Beneficiary not credited'},{id:'duplicate_debit',name:'Duplicate debit'},{id:'refund_pending',name:'Refund pending'}]));
  if(action==='resolve')return fidelityForm(action,'Propose observed resolution','A different principal must check the proposal. Financial outcomes must be supported by retained evidence.',bankSelect('resolution','Observed outcome',[{id:'no_financial_error',name:'Confirmed payment; no financial discrepancy'},{id:'refunded',name:'Full linked refund confirmed'},{id:'declined',name:'Original payment declined'},{id:'reversed',name:'Original debit reversed'}]),id);
  if(action==='reconcile')return fidelityForm(action,'Reconcile independent positions','Enter the positions observed by your independent test participant. Differences remain in the exception report.','<label class="field">Observed bank positions, integer paise<textarea name="positions" class="bank-json" rows="6" required>{}</textarea></label>',id);
  if(action==='clock')return fidelityForm(action,'Advance synthetic business time','Only a virtual-clock environment supports this action.',bankInput('seconds','Seconds to advance','60'));
  if(action==='cycle-inspect'||action==='complaint-inspect'){
   const value=await api(base+(action==='cycle-inspect'?'/cycles/':'/complaints/')+id);if(version!==state.scopeVersion)return;
   return openDialog(dialogHeader(action==='cycle-inspect'?'Synthetic settlement report':'Complaint observations',action==='cycle-inspect'?'Positions and original native financial identities; no live settlement movement.':'Retained case history and independently observed payment legs.')+`<pre class="bank-json-output">${esc(JSON.stringify(value,null,2))}</pre>`+(action==='cycle-inspect'?`<div class="bank-actions">${bankButton('Download CSV','fidelity-csv',id)}</div>`:''));
  }
  if(action==='csv'){
   const response=await fetch(base+'/cycles/'+id+'/csv',{credentials:'same-origin'});if(!response.ok)throw new Error('Report download failed.');const blob=await response.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='avenra-synthetic-'+id+'.csv';a.click();URL.revokeObjectURL(url);return;
  }
  button.disabled=true;
  if(action==='enable')await api(base+'/profile',{});
  else if(action==='cycle'){const d=await api(base);await api(base+'/cycles',{cutoff:d.business_time,request_key:crypto.randomUUID()});}
  else if(action==='investigate')await api(base+'/complaints/'+id+'/investigate',{});
  else if(action==='release')await api(base+'/cycles/'+id+'/propose',{request_key:crypto.randomUUID()});
  else if(action==='approve')await api(base+'/proposals/'+id+'/approve',{});
  if(version===state.scopeVersion)await fidelityOpen(version);
 }catch(error){fidelityError(error.message);}finally{if(button.isConnected)button.disabled=false;}
});
document.addEventListener('change',event=>{
 const form=event.target.closest('#fidelity-form');if(!form)return;
 if(event.target.name==='device_id')fidelityFill(event.target.value);
 if(event.target.name==='link_id')fidelityFill(upiLinkOptions().find(l=>l.id===event.target.value)?.device_id);
});
document.addEventListener('submit',async event=>{
 const form=event.target;if(form.id!=='fidelity-form')return;event.preventDefault();
 const kind=form.dataset.kind,id=form.dataset.id,key=form.dataset.requestKey,version=Number(form.dataset.scope),base=fidelityBase(),v=Object.fromEntries(new FormData(form)),button=form.querySelector('button[type="submit"]');button.disabled=true;
 try{
  if(version!==state.scopeVersion)throw new Error('Environment changed; reopen this form in its original environment.');
  const link=upiLinkOptions().find(l=>l.id===v.link_id);
  if(kind==='discover'||kind==='credential'){
   if(!link)throw new Error('Select an active account link.');
   if(kind==='discover'){const result=await api(base+'/discover',{device_id:link.device_id,device_proof:v.device_proof});if(version===state.scopeVersion)openDialog(dialogHeader('Discovered customer accounts',result.verification)+bankTable(['Bank','Account address'],result.accounts.map(a=>`<tr><td>${esc(a.bank_id)}</td><td>${esc(a.vpa)}</td></tr>`)));return;}
   const challenge=await api(base+'/challenges',{device_id:link.device_id,account_id:link.account_id,device_proof:v.device_proof,purpose:v.purpose,synthetic_bank_verified:!!v.verified,request_key:key});if(version!==state.scopeVersion)return;
   fidelityForm('complete','Complete synthetic credential',`Test issuer challenge: ${challenge.synthetic_challenge}. Valid only for this scoped setup/reset request.`,fidelityProofField()+bankInput('synthetic_challenge','Synthetic issuer challenge',challenge.synthetic_challenge)+'<label class="field">New synthetic test PIN<input name="new_synthetic_pin" type="password" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" required autocomplete="off"><small>Never enter a real banking PIN.</small></label>',challenge.id);fidelityFill(link.device_id);return;
  }
  if(kind==='complete')await api(base+'/challenges/'+id+'/complete',{device_proof:v.device_proof,synthetic_challenge:v.synthetic_challenge,new_synthetic_pin:v.new_synthetic_pin});
  else if(kind==='authorize')await api(base+'/payments/'+id+'/authorize',{device_id:v.device_id,device_proof:v.device_proof,synthetic_pin:v.synthetic_pin,approved:v.decision==='approve'});
  else if(kind==='mandate'){
   if(!link)throw new Error('Select an active account link.');const current=await api(base);
   await api(base+'/mandates',{payer_id:link.account_id,payee_id:v.payee_id,device_id:link.device_id,maximum_paise:bankRupees(v.maximum),interval_seconds:Number(v.interval),starts_at:current.business_time+Number(v.starts_after),occurrences:Number(v.occurrences),request_key:key});
  }else if(kind.startsWith('mandate-'))await api(base+'/mandates/'+id+'/'+kind.slice(8),{device_proof:v.device_proof,synthetic_pin:v.synthetic_pin});
  else if(kind==='schedule')await api(base+'/mandates/'+id+'/schedule',{occurrence:Number(v.occurrence),amount_paise:bankRupees(v.amount),request_key:key});
  else if(kind==='complaint')await api(base+'/complaints',{payment_id:v.payment_id,reason:v.reason,request_key:key});
  else if(kind==='resolve')await api(base+'/complaints/'+id+'/propose',{resolution:v.resolution,request_key:key});
  else if(kind==='reconcile')await api(base+'/cycles/'+id+'/reconcile',{observed_positions:JSON.parse(v.positions),request_key:key});
  else if(kind==='clock')await api(bankPath()+'/simulation/clock',{seconds:Number(v.seconds),request_key:key});
  if(version===state.scopeVersion){await refresh(true);if(kind==='authorize'){$('#dialog').close();return;}await fidelityOpen(version);}
 }catch(error){const target=$('#fidelity-error');if(target)target.textContent=error.message;else toast(error.message);}finally{if(button.isConnected)button.disabled=false;}
});
