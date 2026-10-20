/* Participant simulation controls. Device proofs remain in this tab's memory. */
const upiProofs=new Map();
const upiBase=()=>bankPath()+'/upi';
const upiProofKey=id=>state.tenant+':'+state.environment+':'+id;
function upiPage(lab){
 if(!lab)return '<div class="empty">Refresh this environment to load participant payments.</div>';
 let body=bankPanel('UPI participant lab',`<div class="bank-actions">${bankButton('Create test fixture','upi-fixture')}${bankButton('Install participant topology','upi-topology')}${bankButton('Connection settings','upi-connections')}${bankButton('Lifecycle workflows','fidelity-open')}</div>`,`<p class="bank-note">${esc(lab.profile.id)} · Authenticated participants drive separate debit and credit phases. Synthetic device verification and lab messages are enabled. Current official protocol compatibility remains unqualified.</p>`);
 body+=bankPanel('Participant topology','',bankTable(['Participant','Role','Availability','Signing key','Actions'],lab.participants.map(x=>`<tr><td>${esc(x.name)}</td><td>${esc(x.role)}</td><td>${badge(x.status)}</td><td>Epoch ${x.key_epoch}</td><td><div class="bank-actions">${bankButton(x.status==='active'?'Pause':'Resume',x.status==='active'?'upi-pause':'upi-resume',x.id)}${bankButton('Rotate key','upi-rotate-key',x.id)}</div></td></tr>`)));
 body+=bankPanel('Devices & account links',`<div class="bank-actions">${bankButton('Enrol device','upi-device')}${bankButton('Link account','upi-link')}</div>`,bankTable(['Device','Customer','State','Linked accounts','Actions'],lab.devices.map(x=>`<tr><td>${esc(x.device_reference)}</td><td>${esc(state.bankData.customers.find(c=>c.id===x.customer_id)?.name||x.customer_id)}</td><td>${badge(x.state)}</td><td>${lab.links.filter(l=>l.device_id===x.id).map(l=>esc(state.bankData.accounts.find(a=>a.account_id===l.account_id)?.vpa||l.account_id)).join('<br>')||'—'}</td><td><div class="bank-actions">${x.state==='pending'?bankButton('Verify lab device','upi-activate',x.id):''}${x.state!=='revoked'?bankButton('Revoke','upi-revoke-device',x.id):''}</div></td></tr>`)));
 body+=bankPanel('Participant payments',`<div class="bank-actions">${bankButton('Create payment QR','upi-qr')}${bankButton('New payment / collect','upi-payment','',true)}</div>`,bankTable(['Payment','Mode','Amount','Payer PSP outcome','Actions'],lab.payments.map(x=>`<tr><td class="mono">${esc(x.id)}</td><td>${esc(x.mode)}</td><td>${currency(x.amount_paise)}</td><td>${badge(x.state)}</td><td><div class="bank-actions">${bankButton('Inspect flow','upi-inspect',x.id)}${x.state==='awaiting_authorization'?bankButton('Authorize','upi-authorize',x.id):''}</div></td></tr>`)));
 body+=bankPanel('Protocol qualification','',bankTable(['Requirement','Status'],lab.profile.unqualified.map(x=>`<tr><td>${esc(x)}</td><td>${badge('unqualified')}</td></tr>`)));
 return body;
}
function upiForm(kind,title,description,fields,id=''){
 openDialog(dialogHeader(title,description)+`<form id="upi-form" data-kind="${esc(kind)}" data-id="${esc(id)}" data-request-key="${crypto.randomUUID()}">${fields}<div class="form-error" id="form-error" role="alert"></div><div class="form-actions"><span>Synthetic funds · participant lab</span><button class="btn primary" type="submit">Continue →</button></div></form>`);
}
function upiProofField(){return '<label class="field">Synthetic device proof<input name="device_proof" type="password" required autocomplete="off"></label>';}
function upiFillProof(device){const input=$('#upi-form [name="device_proof"]');if(input)input.value=upiProofs.get(upiProofKey(device))||'';}
function upiLinkOptions(){const b=state.bankData;return b.upi.links.filter(l=>b.upi.devices.some(d=>d.id===l.device_id&&d.state==='active')).map(l=>({...l,name:(b.accounts.find(a=>a.account_id===l.account_id)?.vpa||l.account_id)+' · '+(b.upi.devices.find(d=>d.id===l.device_id)?.device_reference||l.device_id)}));}
async function upiInspect(id,version){
 const p=await api(upiBase()+'/payments/'+id);if(version!==state.scopeVersion)return;
 openDialog(dialogHeader('Participant payment',`${p.mode} · ${currency(p.amount_paise)} · ${p.evidence?.valid?'retained evidence verified':p.effective_state}`)+`<div class="bank-note">${badge(p.effective_state)}<p>${esc(p.reason||'Each recipient retains its own observed result.')}</p><p class="mono">Financial operation: ${esc(p.operation_id||'No financial dispatch')}</p></div>`+
 bankTable(['Participant','Observed state'],p.actor_views.map(x=>`<tr><td>${esc(x.name)}</td><td>${badge(x.state)}</td></tr>`))+
 bankTable(['Step','Message','Delivery','Attempts'],p.messages.map(x=>`<tr><td>${x.step}${x.generation?' / retry '+x.generation:''}</td><td>${esc(x.kind)}</td><td>${badge(x.delivery_state)}${x.delivery_error?'<br>'+esc(x.delivery_error):''}</td><td>${x.attempts}</td></tr>`))+
 (p.operation?bankTable(['Financial leg','Outcome','Native journal identity'],p.operation.legs.map(x=>`<tr><td>${esc(x.kind)}</td><td>${badge(x.state)}</td><td class="mono">${esc(x.ledger_id||'Unconfirmed')}</td></tr>`)):'')+
 `<div class="bank-actions bank-note">${p.state==='awaiting_authorization'?bankButton('Authorize','upi-authorize',id):''}${!p.evidence?bankButton('Request status recovery','upi-reconcile',id):bankButton('Download evidence','upi-download',id)}${p.state==='succeeded'&&p.mode!=='refund'?bankButton('Partial refund','upi-refund',id):''}</div><details class="bank-message-detail"><summary>Retained trace and evidence</summary><pre class="bank-json-output">${esc(JSON.stringify(p,null,2))}</pre></details>`);
}
async function upiConnections(version){
 const registry=await api(upiBase()+'/connections');if(version!==state.scopeVersion)return;
 openDialog(dialogHeader('Participant connections','Simulation runs without bank credentials. Synthetic bank services use loopback mutual TLS. Institutional profiles remain preparation drafts.')+
 `<div class="bank-actions bank-note">${bankButton('Add connection revision','upi-connection-new')}</div>`+
 bankTable(['Revision','Participant','Adapter','State','Actions'],registry.connections.map(c=>`<tr><td>${esc(c.name)}</td><td>${esc(state.bankData.upi.participants.find(p=>p.id===c.participant_id)?.name||c.participant_id)}</td><td>${esc(c.adapter_id)}</td><td>${badge(c.active?'active':c.executable?'local draft':'external draft')}</td><td><div class="bank-actions">${bankButton('Check configuration','upi-connection-check',c.id)}${c.executable&&c.last_check?.manifest.passed?bankButton('Use for new payments','upi-connection-use',c.id):''}${bankButton('Inspect requirements','upi-connection-inspect',c.id)}</div></td></tr>`))+
 '<p class="bank-note">Each payment retains its original connection revisions. Changing a binding affects new payments. Synthetic adapters remain available for regression tests.</p>');
}
document.addEventListener('click',async event=>{
 const button=event.target.closest('[data-bank-action^="upi-"]');if(!button)return;
 const action=button.dataset.bankAction.slice(4),id=button.dataset.bankId,b=state.bankData,lab=b.upi,version=state.scopeVersion,base=upiBase();
 if(action==='connection-new')return upiForm(action,'Add connection revision','Synthetic bank services require a provisioned private lab identity and a loopback HTTPS origin. Institutional drafts record future settings without contacting a bank.',bankInput('name','Revision name','Participant connection')+bankSelect('participant_id','Participant',lab.participants)+bankSelect('adapter_id','Implementation',[{id:'avenra-local-participant/1',name:'Local synthetic participant'},{id:'avenra-tls-synthetic-bank/1',name:'Synthetic bank over mutual TLS'},{id:'avenra-xml-participant/1',name:'XML synthetic participant'},{id:'avenra-xml-tls-synthetic-bank/1',name:'XML synthetic bank over mutual TLS'},{id:'avenra-external-upi-pending/1',name:'Institutional UPI preparation draft'}])+'<label class="field">HTTPS endpoint, for service connections<input name="endpoint" type="url" placeholder="https://127.0.0.1:port" autocomplete="off"></label><label class="field">Secret reference, for service connections<input name="secret_reference" placeholder="secret://lab-identity" autocomplete="off"></label>');
 if(action==='fixture')return upiForm(action,'Create a participant test fixture','Create two configurable virtual banks, two funded accounts and verified synthetic devices.',bankInput('payer_name','Payer name','Test Payer')+bankInput('merchant_name','Merchant name','Test Merchant')+bankInput('payer_funds','Payer opening funds in rupees','10000.00')+bankInput('merchant_funds','Merchant opening funds in rupees','1000.00')+'<label class="field"><span><input name="verify_devices" type="checkbox" required> Simulate successful device verification for these test customers.</span></label>');
 if(action==='device')return upiForm(action,'Enrol a synthetic device','Enrolment creates a time-limited challenge. Verify the device before linking an account.',bankSelect('customer_id','Customer',b.customers)+bankInput('device_reference','Device reference','Test handset'));
 if(action==='activate'){upiForm(action,'Verify lab device','This records an explicit synthetic device-verification result.',upiProofField(),id);upiFillProof(id);return;}
 if(action==='link')return upiForm(action,'Link a payer account','Device and account must belong to the same synthetic customer.',bankSelect('device_id','Active device',lab.devices.filter(d=>d.state==='active'),'id','device_reference')+bankSelect('account_id','Account',b.accounts.filter(a=>a.status==='active'),'account_id','vpa')+bankSelect('psp_id','Payer PSP',lab.participants.filter(p=>p.role==='payer_psp')));
 if(action==='qr')return upiForm(action,'Create payment QR / intent','Generate the supported payment URI subset for a registered synthetic merchant.',bankSelect('payee_vpa','Merchant address',b.accounts.filter(a=>b.customers.some(c=>c.id===a.customer_id&&c.kind==='merchant')),'vpa','vpa')+bankInput('amount','Amount in rupees','100.00')+bankInput('reference','Merchant reference',crypto.randomUUID()));
 if(action==='payment'){
  if(!upiLinkOptions().length)return toast('Create a fixture or enrol, verify and link a device first.');
  upiForm(action,'Prepare participant payment','Funds move only after a separate explicit payer authorization.',bankSelect('link_id','Payer account / device',upiLinkOptions())+bankSelect('payee_id','Beneficiary',b.accounts.filter(a=>a.status==='active'),'account_id','vpa')+bankSelect('mode','Journey',[{id:'pay',name:'Push payment'},{id:'collect',name:'Merchant collect'},{id:'qr',name:'QR / intent subset'}])+bankInput('amount','Amount in rupees','100.00')+'<label class="field">QR URI, when using QR mode<textarea name="qr_uri" class="bank-json" rows="2"></textarea></label>'+bankSelect('financial_fault','Bank response condition',[{id:'none',name:'Normal'},{id:'lost_debit_response',name:'Lose real debit response'},{id:'lost_credit_response',name:'Lose real credit response'},{id:'reject_credit',name:'Reject credit and reverse debit'},{id:'reject_authorization',name:'Bank rejects authorization'}])+bankSelect('delivery_fault','Participant delivery condition',[{id:'',name:'Normal delivery'},{id:'drop_once',name:'Drop first delivery'},{id:'duplicate_once',name:'Duplicate delivery'},{id:'lose_ack_once',name:'Lose acknowledgement after receipt commit'},{id:'corrupt_once',name:'Corrupt first signed delivery'},{id:'delay_once',name:'Delay first delivery'}])+bankSelect('fault_step','Delivery boundary',lab.routes.filter(r=>r.step>0).map(r=>({id:r.step,name:r.kind}))));
  const payer=lab.links.find(l=>l.id===$('#upi-form [name="link_id"]').value)?.account_id;const select=$('#upi-form [name="payee_id"]');const other=[...select.options].find(o=>o.value!==payer);if(other)select.value=other.value;return;
 }
 if(action==='authorize'){
  const p=await api(base+'/payments/'+id);if(version!==state.scopeVersion)return;
  const links=upiLinkOptions().filter(l=>l.account_id===p.payer_id&&l.psp_id===JSON.parse(p.topology_json).payer_psp&&(!p.device_id||l.device_id===p.device_id));
  if(!links.length)return toast('An active linked payer device is required.');
  const lifecycle=await api(bankPath()+'/fidelity');if(version!==state.scopeVersion)return;
  if(lifecycle.enabled)return fidelityAuthorize(id,p,links);
  upiForm(action,'Authorize exact payment',`${currency(p.amount_paise)} · ${b.accounts.find(a=>a.account_id===p.payee_id)?.vpa||p.payee_id}. Verify the destination and amount.`,bankSelect('device_id','Payer device',links.map(l=>({id:l.device_id,name:l.name})))+upiProofField()+bankSelect('decision','Synthetic decision',[{id:'approve',name:'Approve this exact payment'},{id:'reject',name:'Reject payment'}]),id);upiFillProof(links[0].device_id);return;
 }
 if(action==='refund')return upiForm(action,'Prepare linked partial refund','The merchant must authorize the refund using its linked device.',bankInput('amount','Refund amount in rupees','10.00'),id);
 button.disabled=true;
 try{
  if(action==='connections'){await upiConnections(version);return;}
  else if(action==='connection-check'){
   const result=await api(base+'/connections/'+id+'/validate',{});if(version!==state.scopeVersion)return;
   openDialog(dialogHeader('Connection configuration check',result.manifest.passed?`Synthetic connection passed${result.manifest.probe?.channel?.tls_version?' over '+result.manifest.probe.channel.tls_version:''}. Institutional acceptance is separate.`:'Connection checks remain incomplete; local simulation remains available.')+bankTable(['Check','Result'],result.manifest.checks.map(c=>`<tr><td>${esc(c.name)}${c.error_code?' · '+esc(c.error_code):''}</td><td>${badge(c.passed?'passed':c.error_code?'failed':'required')}</td></tr>`))+`<div class="bank-actions bank-note">${bankButton('Back to connections','upi-connections')}</div>`);return;
  }else if(action==='connection-use'){
   const current=await api(base+'/connections');if(version!==state.scopeVersion)return;const revision=current.connections.find(c=>c.id===id);
   if(!revision?.last_check)throw new Error('Check this revision first.');await api(base+'/connections/'+id+'/activate',{check_id:revision.last_check.id});await upiConnections(version);return;
  }else if(action==='connection-inspect'){
   const registry=await api(base+'/connections');if(version!==state.scopeVersion)return;const revision=registry.connections.find(c=>c.id===id);if(!revision)throw new Error('Connection revision not found.');
   const profile=registry.profiles.find(p=>p.hash===revision.profile_hash);
   openDialog(dialogHeader('Connection requirements',revision.executable?'Owned local adapter. No institutional qualification is implied.':'Future connection draft. Credentials do not implement missing protocol behaviour.')+bankTable(['Requirement','Type','Status'],(revision.remaining||[]).map(r=>`<tr><td>${esc(r.description)}</td><td>${esc(r.type)}</td><td>${badge(r.status)}</td></tr>`))+`<details class="bank-message-detail"><summary>Revision and specification access routes</summary><pre class="bank-json-output">${esc(JSON.stringify({revision,profile},null,2))}</pre></details><div class="bank-actions bank-note">${bankButton('Back to connections','upi-connections')}</div>`);return;
  }else if(action==='topology')await api(base+'/participants',{});
  else if(action==='inspect'){await upiInspect(id,version);return;}
  else if(action==='reconcile'){await api(base+'/payments/'+id+'/reconcile',{});$('#dialog').close();}
  else if(['pause','resume','rotate-key'].includes(action))await api(base+'/participants/'+id+'/'+action,{});
  else if(action==='revoke-device')await api(base+'/devices/'+id+'/revoke',{});
  else if(action==='download'){
   const evidence=await api(base+'/payments/'+id+'/evidence');const url=URL.createObjectURL(new Blob([JSON.stringify(evidence,null,2)],{type:'application/json'})),link=document.createElement('a');link.href=url;link.download='avenra-'+id+'.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  if(version===state.scopeVersion)await refresh(true);
 }catch(error){toast(error.message);}finally{if(button.isConnected)button.disabled=false;}
});
document.addEventListener('change',event=>{if(event.target.matches('#upi-form [name="device_id"]'))upiFillProof(event.target.value);});
document.addEventListener('submit',async event=>{
 if(event.target.id!=='upi-form')return;event.preventDefault();
 const form=event.target,v=Object.fromEntries(new FormData(form)),kind=form.dataset.kind,id=form.dataset.id,key=form.dataset.requestKey,base=upiBase(),version=state.scopeVersion,button=form.querySelector('button[type="submit"]');button.disabled=true;
 try{
  if(kind==='connection-new'){
   const body={name:v.name,participant_id:v.participant_id,adapter_id:v.adapter_id,request_key:key};if(!['avenra-local-participant/1','avenra-xml-participant/1'].includes(v.adapter_id)){body.endpoint=v.endpoint;if(v.secret_reference)body.secret_reference=v.secret_reference;}
   await api(base+'/connections',body);if(version===state.scopeVersion)await upiConnections(version);return;
  }else if(kind==='fixture'){
   if(!v.verify_devices)throw new Error('Choose an explicit synthetic device-verification result.');
   const payerFunds=bankRupees(v.payer_funds),merchantFunds=bankRupees(v.merchant_funds),tag=key.replace(/-/g,'').slice(0,10),prefix=bankPath();
   const banks=[],customers=[],accounts=[];
   for(const [i,name,type,opening] of [[0,v.payer_name,'customer',payerFunds],[1,v.merchant_name,'merchant',merchantFunds]]){
    const code='upi_'+(i?'b_':'a_')+tag;
    banks.push(await api(prefix+'/banks',{name:name+' bank',code,request_key:key+':bank:'+i}));
    customers.push(await api(prefix+'/customers',{name,kind:type,request_key:key+':customer:'+i}));
    accounts.push((await api(prefix+'/accounts',{bank_id:banks[i].id,customer_id:customers[i].id,vpa:(i?'merchant':'payer')+'@'+code,opening_paise:opening,request_key:key+':account:'+i})).account_id);
   }
   let ready=false;
   for(let attempt=0;attempt<100;attempt++){
    if(version!==state.scopeVersion)throw new Error('Environment changed; return to the original environment to inspect this fixture.');
    const current=await api(prefix);if(accounts.every(id=>current.accounts.some(a=>a.account_id===id&&a.status==='active'))){ready=true;break;}await new Promise(resolve=>setTimeout(resolve,150));
   }
   if(!ready)throw new Error('Accounts are still provisioning. Their original request identities have been retained.');
   const lab=await api(base+'/participants',{});
   for(let i=0;i<2;i++){
    const device=await api(base+'/devices',{customer_id:customers[i].id,device_reference:'Test handset '+(i+1),request_key:key+':device:'+i});upiProofs.set(upiProofKey(device.id),device.enrolment_challenge);
    await api(base+'/devices/'+device.id+'/activate',{device_proof:device.enrolment_challenge,synthetic_verification:true});
    await api(base+'/links',{device_id:device.id,account_id:accounts[i],psp_id:lab.participants.find(p=>p.bank_id===banks[i].id&&p.role==='payer_psp').id});
   }
  }else if(kind==='device'){
   const device=await api(base+'/devices',{customer_id:v.customer_id,device_reference:v.device_reference,request_key:key});upiProofs.set(upiProofKey(device.id),device.enrolment_challenge);
  }else if(kind==='activate')await api(base+'/devices/'+id+'/activate',{device_proof:v.device_proof,synthetic_verification:true});
  else if(kind==='link')await api(base+'/links',{device_id:v.device_id,account_id:v.account_id,psp_id:v.psp_id});
  else if(kind==='payment'){
   const link=state.bankData.upi.links.find(l=>l.id===v.link_id);if(!link)throw new Error('Payer link changed; reopen this form.');
   const body={request_key:key,payer_id:link.account_id,payee_id:v.payee_id,device_id:link.device_id,amount_paise:bankRupees(v.amount),mode:v.mode,financial_fault:v.financial_fault};
   if(v.delivery_fault)body.delivery_fault={step:Number(v.fault_step),type:v.delivery_fault};if(v.mode==='qr')body.qr_uri=v.qr_uri;
   await api(base+'/payments',body);
  }else if(kind==='authorize')await api(base+'/payments/'+id+'/authorize',{device_id:v.device_id,device_proof:v.device_proof,approved:v.decision==='approve'});
  else if(kind==='refund')await api(base+'/payments/'+id+'/refund',{request_key:key,amount_paise:bankRupees(v.amount)});
  else if(kind==='qr'){
   const qr=await api(base+'/qr',{payee_vpa:v.payee_vpa,amount_paise:bankRupees(v.amount),reference:v.reference});
   if(version!==state.scopeVersion)return;openDialog(dialogHeader('Payment QR / intent URI','Use this URI in QR mode. The payer must separately authorize its exact amount and destination.')+`<p class="bank-note">${esc(qr.contract)}</p><pre class="bank-json-output">${esc(qr.uri)}</pre><p class="bank-note">${esc(qr.decoded.resolved_name)} · ${currency(qr.decoded.amount_paise)}</p>`);return;
  }
  $('#dialog').close();if(version===state.scopeVersion)await refresh(true);
 }catch(error){const target=$('#form-error');if(target)target.textContent=error.message;else toast(error.message);}finally{if(button.isConnected)button.disabled=false;}
});
