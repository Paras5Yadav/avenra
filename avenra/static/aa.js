/* Financial-data workflows in the current banking workspace. */
let aaData=null;
const aaStyle=document.createElement('style');aaStyle.textContent='#aa-form input[type=checkbox]{display:inline-block;width:auto;margin:6px 8px 6px 0;padding:0}';document.head.append(aaStyle);
const aaBase=()=>bankPath()+'/aa';
const aaButton=(text,action,id='')=>`<button class="btn small" data-aa-action="${esc(action)}" data-aa-id="${esc(id)}">${esc(text)}</button>`;
function aaError(message){let target=$('#aa-error');if(target){target.textContent=message;target.scrollIntoView({block:'nearest'});}else toast(message);}
function aaForm(kind,title,description,fields,id=''){
 openDialog(dialogHeader(title,description)+`<form id="aa-form" data-kind="${kind}" data-id="${esc(id)}" data-scope="${state.scopeVersion}" data-request="${crypto.randomUUID()}">${fields}<div class="form-error" id="aa-error" role="alert"></div><div class="form-actions"><span>Synthetic participants and financial information</span><button class="btn primary" type="submit">Continue →</button></div></form>`);
}
async function aaOpen(version=state.scopeVersion){
 const data=await api(aaBase());if(version!==state.scopeVersion)return;aaData=data;
 let html=dialogHeader('AA encrypted data exchange','Consent, provider statements and recipient verification in this environment.');
 if(!data.configured){openDialog(html+'<p class="bank-note">Synthetic financial-data participants have not been configured for this environment. Existing local consent tools remain available.</p>');return;}
 html+='<p class="bank-note">Synthetic AA, information providers and information users. The aggregator routes ciphertext. Recipient keys stay with the information user; statement review decrypts in your browser. Official interoperability qualification remains open.</p>';
 html+='<div class="bank-actions">'+aaButton('Discover & link accounts','discover')+aaButton('Request data-sharing consent','consent')+aaButton('Participant deliveries','deliveries')+aaButton('Refresh','open')+'</div>';
 html+='<h3>Linked provider accounts</h3>'+bankTable(['Customer','Provider / masked account','State','Action'],data.links.map(l=>`<tr><td>${esc(l.customer)}</td><td>${esc(l.fip_id)}<br>${esc(l.masked)}</td><td>${badge(l.state.toLowerCase())}</td><td>${l.state==='LINKED'?aaButton('Delink','delink',l.id):''}</td></tr>`));
 html+='<h3>Customer consents</h3>'+bankTable(['Recipient','Customer','State','Actions'],data.consents.map(c=>`<tr><td>${esc(c.fiu_id)}</td><td>${esc(c.customer)}</td><td>${badge(c.state.toLowerCase())}</td><td><div class="bank-actions">${c.state==='PENDING'?aaButton('Review & approve','approve',c.id):''}${c.state==='ACTIVE'?aaButton('Request statement','request',c.id)+aaButton('Pause','pause',c.id):''}${c.state==='PAUSED'?aaButton('Resume','resume',c.id):''}${['ACTIVE','PAUSED'].includes(c.state)?aaButton('Revoke','revoke',c.id):''}</div></td></tr>`));
 html+='<h3>Data sessions</h3>'+bankTable(['Session','State','Expires','Action'],data.sessions.map(s=>`<tr><td class="mono">${esc(s.id)}</td><td>${badge(s.state.toLowerCase())}</td><td>${esc(new Date(s.expires_at*1000).toLocaleTimeString())}</td><td>${s.state==='COMPLETED'?aaButton('Review recipient result','inspect',s.id):s.state==='FAILED'?aaButton('Resume original session','recover-session',s.id):'Refresh to observe progress'}</td></tr>`));
 html+='<h3>Contract versions</h3>'+bankTable(['Role','Selected version','Document metadata'],data.contract_metadata.map(c=>`<tr><td>${esc(c.role.toUpperCase())}</td><td>${esc(c.selected_version)}</td><td>${c.metadata_matches?'Version metadata matches':`Declares ${esc(c.declared_version)}; upstream discrepancy retained`}</td></tr>`));
 html+='<details><summary>Application delivery observations</summary>'+bankTable(['Operation','State','Last error','Action'],data.jobs.map(j=>`<tr><td>${esc(j.kind)}</td><td>${esc(j.state)}</td><td>${esc(j.error||'—')}</td><td>${j.state==='failed'&&['notice','consent'].includes(j.kind)?aaButton('Retry original delivery','application-retry',j.id):''}</td></tr>`))+'</details><div class="form-error" id="aa-error" role="alert"></div>';
 openDialog('<section class="fidelity-view" aria-label="AA encrypted data exchange">'+html+'</section>');
}
function aaBytes(value){const raw=atob(value.replace(/-/g,'+').replace(/_/g,'/'));return Uint8Array.from(raw,c=>c.charCodeAt(0));}
async function aaDeliveries(peer,version=state.scopeVersion){
 if(!peer){aaForm('deliveries','Participant notification deliveries','Inspect retained attempts, uncertain outcomes and exhausted notices.',bankSelect('peer','Participant',aaData.peers));return;}
 const result=await api(aaBase()+'/peers/'+encodeURIComponent(peer)+'/notices');if(version!==state.scopeVersion)return;
 let html=dialogHeader('Participant notification deliveries','Retries preserve the original signed notification identity and its immutable payload.');
 html+=`<p class="bank-note">${esc(peer)} · ${esc(result.counts.pending||0)} pending · ${esc(result.counts.failed||0)} need attention · ${esc(result.counts.delivered||0)} delivered</p>`;
 html+='<div class="bank-actions">'+aaButton('Refresh','delivery-refresh',peer)+aaButton('Back to financial data','open')+'</div>';
 html+=bankTable(['Notice / route','State','Attempts','Last result','Action'],result.notices.map(n=>`<tr><td class="mono">${esc(n.id)}<br>${esc(n.path)}</td><td>${esc(n.state)}${n.lease_until?' · in flight':''}</td><td>${n.attempts} · generation ${n.generation}</td><td>${esc(n.last_error||'—')}${n.state==='pending'&&!n.lease_until?'<br>Next attempt '+esc(new Date(n.next_at*1000).toLocaleTimeString()):''}</td><td>${aaButton('Inspect attempts','delivery-inspect',peer+'/'+n.id)}${n.state==='failed'?aaButton('Retry original notice','delivery-retry',peer+'/'+n.id):''}</td></tr>`));
 html+='<p class="bank-note">Up to 100 retained notices are shown. Counters include the full participant journal. Failed attempts remain retained after retry. Automatic retries use bounded exponential backoff and a persisted attempt budget.</p><div class="form-error" id="aa-error" role="alert"></div>';
 openDialog('<section class="fidelity-view" aria-label="Participant notification deliveries">'+html+'</section>');
}
async function aaReview(id,version){
 const keys=await crypto.subtle.generateKey({name:'RSA-OAEP',modulusLength:2048,publicExponent:new Uint8Array([1,0,1]),hash:'SHA-256'},false,['encrypt','decrypt']);
 const publicKey=await crypto.subtle.exportKey('jwk',keys.publicKey);
 const result=await api(aaBase()+'/sessions/'+id+'/inspect',{recipient_jwk:publicKey});if(version!==state.scopeVersion)return;
 const parts=result.encryptedResult.split('.');if(parts.length!==5)throw new Error('Invalid encrypted review response.');
 const header=JSON.parse(new TextDecoder().decode(aaBytes(parts[0])));if(header.alg!=='RSA-OAEP-256'||header.enc!=='A256GCM')throw new Error('Unsupported encrypted review profile.');
 const contentKey=await crypto.subtle.decrypt({name:'RSA-OAEP'},keys.privateKey,aaBytes(parts[1]));
 const aes=await crypto.subtle.importKey('raw',contentKey,'AES-GCM',false,['decrypt']);
 const encrypted=aaBytes(parts[3]),tag=aaBytes(parts[4]),combined=new Uint8Array(encrypted.length+tag.length);combined.set(encrypted);combined.set(tag,encrypted.length);
 const plain=await crypto.subtle.decrypt({name:'AES-GCM',iv:aaBytes(parts[2]),additionalData:new TextEncoder().encode(parts[0]),tagLength:128},aes,combined);
 if(version!==state.scopeVersion)return;const decoded=JSON.parse(new TextDecoder().decode(plain));
 let html=dialogHeader('Recipient statement review','The information user authenticated these statements; this view decrypted in your browser.');
 html+=`<p class="bank-note">${decoded.documents.length} verified deposit documents · ${decoded.retained?'Stored at the recipient under consent data-life rules':'View mode; no recipient result retained'}</p>`;
 for(const d of decoded.documents){
  const xml=new DOMParser().parseFromString(d.xml,'application/xml');if(xml.querySelector('parsererror'))throw new Error('Invalid financial XML in review.');
  const account=xml.documentElement,summary=xml.getElementsByTagNameNS('*','Summary')[0],count=xml.getElementsByTagNameNS('*','Transaction').length;
  html+=`<section class="receipt"><strong>${esc(account.getAttribute('maskedAccNumber'))}</strong><p>${summary?esc(summary.getAttribute('currency'))+' '+esc(summary.getAttribute('currentBalance')):'Summary was not requested'} · ${count} included transactions</p><details><summary>Inspect deposit XML</summary><pre>${esc(d.xml)}</pre></details><small class="mono">SHA-256 ${esc(d.sha256)}</small></section>`;
 }
 openDialog('<section class="fidelity-view" aria-label="Recipient statement review">'+html+'</section>');
}
document.addEventListener('click',async event=>{
 const button=event.target.closest('[data-aa-action]');if(!button)return;const action=button.dataset.aaAction,id=button.dataset.aaId,version=state.scopeVersion;
 try{
  if(action==='open')return await aaOpen(version);
  if(!aaData?.configured)throw new Error('Open this environment’s financial-data profile first.');
  if(action==='deliveries')return await aaDeliveries(null,version);
  if(action==='delivery-refresh')return await aaDeliveries(id,version);
  if(action==='delivery-inspect'){
   const [peer,notice]=id.split('/'),result=await api(aaBase()+'/peers/'+peer+'/notices/'+notice);if(version!==state.scopeVersion)return;
   openDialog(dialogHeader('Notification attempt history','The original payload stays immutable. An unconfirmed attempt may have been accepted; retry uses the same notice identity.')+bankTable(['Attempt / generation','Outcome','Result','Started'],result.attempts.map(a=>`<tr><td>${a.attempt} / ${a.generation}</td><td>${esc(a.outcome||'in flight')}</td><td>${esc(a.error||a.status||'—')}</td><td>${esc(new Date(a.started_at*1000).toLocaleString())}</td></tr>`))+'<div class="bank-actions">'+aaButton('Back to participant deliveries','delivery-refresh',peer)+'</div>');return;
  }
  if(action==='delivery-retry'){
   const [peer,notice]=id.split('/');button.disabled=true;button.dataset.request||=crypto.randomUUID();
   await api(aaBase()+'/peers/'+peer+'/notices/'+notice+'/retry',{request_id:button.dataset.request});await aaDeliveries(peer,version);return;
  }
  if(action==='application-retry'){button.disabled=true;button.dataset.request||=crypto.randomUUID();await api(aaBase()+'/deliveries/'+id+'/retry',{request_id:button.dataset.request});await aaOpen(version);return;}
  const customers=aaData.customers.map(c=>({id:c.vua,name:state.bankData?.customers.find(x=>x.id===c.id)?.name||c.vua}));
  if(action==='discover')aaForm('discover','Discover provider accounts','The synthetic provider verifies the selected customer identifier.',bankSelect('customer','Customer',customers)+bankSelect('fip_id','Information provider',aaData.peers.filter(p=>p.role==='fip')));
  else if(action==='consent')aaForm('consent','Request data-sharing consent','An information user requests a purpose, time range and usage frequency. The customer approves linked accounts next.',bankSelect('customer','Customer',customers)+bankSelect('fiu_id','Information user',aaData.peers.filter(p=>p.role==='fiu'))+bankSelect('mode','Consent mode',[{id:'VIEW',name:'View requested information'},{id:'STORE',name:'Store under the consent data-life rules'}])+bankInput('purpose','Purpose code','101')+bankInput('hours','Consent duration (hours)','1','number')+bankInput('days','Statement lookback (days)','1','number')+bankInput('frequency','Maximum requests per hour','3','number'));
  else if(action==='approve'){
   const consent=aaData.consents.find(c=>c.id===id),links=aaData.links.filter(l=>l.customer===consent.customer&&l.state==='LINKED');
   if(!links.length)throw new Error('Discover and link an account for this customer first.');
   aaForm('approve','Approve synthetic data sharing','Choose which linked provider accounts this recipient may access.',links.map(l=>`<label class="field"><input type="checkbox" name="links" value="${esc(l.id)}" checked>${esc(l.fip_id)} · ${esc(l.masked)}</label>`).join('')+'<label class="field"><input type="checkbox" name="approved" required>I approve as the synthetic customer for this test.</label>',id);
  }else if(action==='request'){
   const c=await api(aaBase()+'/consents/'+id),range=c.request.ConsentDetail.FIDataRange;
   button.disabled=true;await api(aaBase()+'/consents/'+id+'/request',{request_id:crypto.randomUUID(),range:{from:range.from,to:new Date(Math.min(Date.now(),Date.parse(range.to))).toISOString()}});await aaOpen(version);toast('Recipient keys and the original data request are persisted.');
  }else if(action==='inspect'){button.disabled=true;await aaReview(id,version);}
  else if(action==='recover-session'){button.disabled=true;await api(aaBase()+'/sessions/'+id+'/resume',{});await aaOpen(version);}
  else if(['pause','resume','revoke'].includes(action)){button.disabled=true;await api(aaBase()+'/consents/'+id+'/state',{state:{pause:'PAUSED',resume:'ACTIVE',revoke:'REVOKED'}[action]});await aaOpen(version);}
  else if(action==='delink'){button.disabled=true;await api(aaBase()+'/links/'+id+'/delink',{});await aaOpen(version);}
 }catch(error){aaError(error.message);}finally{if(button.isConnected)button.disabled=false;}
});
document.addEventListener('submit',async event=>{
 const form=event.target;if(form.id!=='aa-form')return;event.preventDefault();const button=form.querySelector('button[type="submit"]');button.disabled=true;
 try{
  const version=Number(form.dataset.scope);if(version!==state.scopeVersion)throw new Error('The environment changed. Reopen its financial-data controls.');
  const fields=new FormData(form),values=Object.fromEntries(fields),kind=form.dataset.kind,id=form.dataset.id;
  if(kind==='deliveries'){await aaDeliveries(values.peer,version);return;}
  if(kind==='discover'){
   const found=await api(aaBase()+'/discover',{customer:values.customer,fip_id:values.fip_id});
   if(!found.DiscoveredAccounts.length)throw new Error('The selected provider has no matching accounts for this customer.');
   await api(aaBase()+'/links',{customer:values.customer,fip_id:values.fip_id,accounts:found.DiscoveredAccounts});
  }else if(kind==='approve')await api(aaBase()+'/consents/'+id+'/approve',{synthetic_customer_approved:fields.has('approved'),link_ids:fields.getAll('links')});
  else if(kind==='consent'){
   const now=Date.now(),end=now+Number(values.hours)*3600000;
   await api(aaBase()+'/consents',{fiu_id:values.fiu_id,request_id:form.dataset.request,ConsentDetail:{consentStart:new Date(now-1000).toISOString(),consentExpiry:new Date(end).toISOString(),consentMode:values.mode,fetchType:'PERIODIC',consentTypes:['SUMMARY','TRANSACTIONS'],fiTypes:['DEPOSIT'],DataConsumer:{id:values.fiu_id,type:'FIU'},Customer:{id:values.customer},Purpose:{code:values.purpose,text:'Synthetic financial-data review'},FIDataRange:{from:new Date(now-Number(values.days)*86400000).toISOString(),to:new Date(end).toISOString()},DataLife:{unit:'DAY',value:1},Frequency:{unit:'HOUR',value:Number(values.frequency)}}});
  }
  await aaOpen(version);toast('Financial-data workflow recorded.');
 }catch(error){aaError(error.message);}finally{if(button.isConnected)button.disabled=false;}
});
