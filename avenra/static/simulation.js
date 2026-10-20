/* Avenra's reviewed synthetic institution experiments. */
function simulationPage(lab){
 if(!lab)return '<div class="empty">Refresh the environment to load institution experiments.</div>';
 let content=bankPanel('Versioned participant profiles',`<div class="bank-actions">${bankButton('Install lab profile','simulation-profile')}${bankButton('＋ Publish multistep plan','simulation-plan')}${bankButton('Publish UPI journey','simulation-upi-plan')}</div>`,bankTable(['Profile','Authority','Document hash','Requirements'],lab.profiles.map(x=>`<tr><td>${esc(x.profile_key)}</td><td>Synthetic lab contract</td><td class="mono">${esc(x.document_hash.slice(0,20))}…</td><td>${JSON.parse(x.document_json).requirements.length} declared lab rules</td></tr>`)));
 content+='<p class="bank-note">Participant envelopes are an Avenra test protocol. They do not implement official UPI or AA wire formats. Business time advances inside each fresh fixture; infrastructure leases continue using real time.</p>';
 content+=bankPanel('Multistep experiments','',bankTable(['Plan','Version','Steps',''],lab.plans.map(x=>`<tr><td>${esc(x.name)}</td><td>${x.version}</td><td>${JSON.parse(x.plan_json).steps.length}</td><td>${bankButton('Run in fresh fixture','simulation-run',x.id)}</td></tr>`)));
 content+=bankPanel('Retained experiment results','',bankTable(['Run','State','Completed steps',''],lab.runs.map(x=>`<tr><td class="mono">${esc(x.id)}</td><td>${badge(x.blocking_reason?'blocked':x.state)}</td><td>${x.cursor}</td><td>${bankButton('Inspect evidence','simulation-inspect',x.id)}</td></tr>`)));
 content+=bankPanel('External qualification boundaries','',bankTable(['Profile','State','Required evidence'],lab.official_profiles.map(x=>`<tr><td>${esc(x.name)}</td><td>${badge(x.state)}</td><td>${esc(x.missing)}</td></tr>`)));
 return content;
}
function simulationSummary(value){
 if(value.expected_error)return 'Expected denial: '+value.expected_error;
 if(value.error)return value.error+': '+(value.detail||'');
 if(value.balances)return Object.entries(value.balances).map(([name,amount])=>name+' '+currency(amount)).join(' · ');
 if(value.after_epoch!==undefined)return 'Business clock advanced '+(value.after_epoch-value.before_epoch)+' seconds';
 if(value.data_hash)return value.accounts+' account(s) · '+value.transactions+' transaction(s) · data digest retained';
 if(value.sender)return [value.state,value.sender+' → '+value.recipient,value.reason,value.replayed?'original receipt replayed':''].filter(Boolean).join(' · ');
 return [value.state,value.amount_paise!==undefined?currency(value.amount_paise):'',value.observations?'Independent journal observations retained':'',value.id||value.consent_id||''].filter(Boolean).join(' · ')||'Recorded';
}
document.addEventListener('click',async event=>{
 const button=event.target.closest('[data-bank-action^="simulation-"]');if(!button)return;
 const action=button.dataset.bankAction,id=button.dataset.bankId,base=bankPath()+'/simulation',scopeVersion=state.scopeVersion;
 if(action==='simulation-plan'||action==='simulation-upi-plan'){
  const lab=state.bankData.simulation;
  if(!lab.profiles.length)return toast('Install the reviewed lab profile first.');
  openDialog(dialogHeader('Publish institution experiment','Declare fixture funds, participant messages, financial operations, virtual time and independent expectations.')+`<form id="simulation-plan-form" data-request-key="${crypto.randomUUID()}">${bankInput('name','Plan name','Payment recovery, refund and consent expiry')}${bankSelect('profile_id','Immutable profile',lab.profiles,'id','profile_key')}<label class="field">Multistep plan JSON<textarea name="plan" class="bank-json" required>${esc(JSON.stringify(action==='simulation-upi-plan'?lab.upi_template:lab.template,null,2))}</textarea></label><div id="form-error" class="form-error" role="alert"></div><div class="form-actions"><span>Synthetic funds · bounded operations</span><button class="btn primary" type="submit">Publish plan →</button></div></form>`);
  return;
 }
 button.disabled=true;
 try{
  if(action==='simulation-profile')await api(base+'/profiles',{});
  else if(action==='simulation-run')await api(base+'/runs',{plan_id:id,request_key:crypto.randomUUID()});
  else if(action==='simulation-inspect'){
   const run=await api(base+'/runs/'+id),e=run.evidence;
   if(scopeVersion!==state.scopeVersion)return;
   openDialog(dialogHeader('Institution experiment',e?.valid?'Retained evidence signature verified.':run.state)+`<div class="bank-note">${badge(run.state)}<p>${esc(run.blocking_reason||run.error||'Step results are recorded against independent expectations.')}</p><p>Fixture ${esc(run.fixture_environment_id)}</p></div>${bankTable(['Step','Virtual business time','Observation'],run.steps.map(s=>{const output=JSON.parse(s.output_json);return `<tr><td>${esc(s.step_id)}</td><td>${esc(new Date(s.virtual_time*1000).toISOString())}</td><td>${esc(simulationSummary(output))}<details class="bank-message-detail"><summary>Raw observation</summary><pre>${esc(JSON.stringify(output,null,2))}</pre></details></td></tr>`;}))}${e?`<p class="bank-note mono">SHA-256 ${esc(e.manifest_hash)}</p>${bankButton('Download signed evidence','simulation-download',id)}`:bankButton('Resume interrupted run','simulation-resume',id)}<p class="bank-note">Synthetic lab validation. Official conformance and institutional acceptance remain unqualified.</p>`);
  }else if(action==='simulation-resume'){
   await api(base+'/runs/'+id+'/resume',{});$('#dialog').close();
  }else if(action==='simulation-download'){
   const evidence=await api(base+'/runs/'+id+'/evidence');
   const url=URL.createObjectURL(new Blob([JSON.stringify(evidence,null,2)],{type:'application/json'})),link=document.createElement('a');
   link.href=url;link.download='avenra-'+id+'.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  if(scopeVersion===state.scopeVersion)await refresh(true);
 }catch(error){toast(error.message);}finally{if(button.isConnected)button.disabled=false;}
});
document.addEventListener('submit',async event=>{
 if(event.target.id!=='simulation-plan-form')return;event.preventDefault();
 const form=event.target,values=Object.fromEntries(new FormData(form)),button=form.querySelector('button[type="submit"]'),scopeVersion=state.scopeVersion;
 button.disabled=true;
 try{
  await api(bankPath()+'/simulation/plans',{name:values.name,profile_id:values.profile_id,plan:JSON.parse(values.plan)});
  $('#dialog').close();if(scopeVersion===state.scopeVersion)await refresh(true);
 }catch(error){$('#form-error').textContent=error.message;}finally{if(button.isConnected)button.disabled=false;}
});
