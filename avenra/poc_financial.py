"""Working participant and lifecycle journeys for the existing scenario runner."""
import json
import time

from .errors import DomainError
from .util import uid


def denied(check,key,name,callback,code):
    try:callback();observed=None
    except DomainError as error:observed=error.code
    check(key,name,{'error_code':code},{'error_code':observed},observed==code)


def participants(app,tenant,env,run_id,check):
    from .synthetic_lab import fixture,provision,start,stop,connect,prepare,authorize,advance
    from . import participant_peer_lab as peers
    from .upi_wire import WIRE_TLS
    value=fixture(app,tenant,env);processes=[];peer_processes={}
    try:
        banks=provision(app,value)
        for bank in banks:processes.append(start(app,bank))
        bank_revisions=connect(app,value,banks,WIRE_TLS)
        workers=peers.provision_all(app,tenant,env)
        for worker in workers:peer_processes[worker['worker_id']]=peers.start(app,worker)
        peer_revisions=peers.connect(app,tenant,env,workers)
        check('01-participant-processes','All supported bank, PSP and network actors use separately authenticated services',
              {'banks':2,'psp_network':5,'connections':9},
              {'banks':len(processes),'psp_network':len(peer_processes),'connections':len(bank_revisions+peer_revisions),
               'peer_pids':[p.pid for p in peer_processes.values()]},
              len(processes)==2 and len(peer_processes)==5 and len(bank_revisions+peer_revisions)==9)
        def balances():return {r['id']:r['balance_paise'] for r in app.accounts(tenant,env)}
        before=balances();amount=525
        payment=prepare(app,value,amount,mode='collect',financial_fault='lost_credit_response')
        presented=advance(app,value,payment,predicate=lambda p:any(m['step']==0 and m['delivery_state']=='delivered' for m in p['messages']))
        check('02-collect-approval','Independent collect presentation occurs before authorization or money movement',
              {'financial_operation':None,'balances_unchanged':True},
              {'financial_operation':presented['operation_id'],'balances_unchanged':before==balances()},presented['operation_id'] is None and before==balances())
        authorize(app,value,payment);unknown=advance(app,value,payment,'unknown')
        check('03-lost-credit-response','Real response loss retains uncertainty after the beneficiary credit committed',
              {'state':'unknown','beneficiary_increase':amount},
              {'state':unknown['state'],'beneficiary_increase':balances()[value['accounts'][1]]-before[value['accounts'][1]],'operation_id':unknown['operation_id']},
              unknown['state']=='unknown' and balances()[value['accounts'][1]]==before[value['accounts'][1]]+amount)
        network=next(w for w in workers if w['role']=='network')
        dead=peer_processes[network['worker_id']];dead.kill();dead.wait(timeout=5)
        waiting=advance(app,value,payment,predicate=lambda p:any(e['event']=='peer.unconfirmed' for e in p['events']))
        check('04-independent-outage','A genuinely stopped network cannot be replaced by a local receipt',
              {'peer_unconfirmed':True,'same_operation':True},
              {'peer_unconfirmed':any(e['event']=='peer.unconfirmed' for e in waiting['events']),'same_operation':waiting['operation_id']==unknown['operation_id']},
              any(e['event']=='peer.unconfirmed' for e in waiting['events']) and waiting['operation_id']==unknown['operation_id'] and not waiting['evidence'])
        peer_processes[network['worker_id']]=peers.start(app,network)
        app.upi.reconcile(tenant,env,payment['id'],{});completed=advance(app,value,payment)
        peer_messages=[m for m in completed['messages'] if m['step'] in {-1,0,1,3,5,6,7}]
        acknowledgements=[e for e in completed['events'] if e['event']=='peer.accepted']
        check('05-original-recovery','Restarting the same independent service completes the original operation with authenticated receipts',
              {'state':'succeeded','same_operation':True,'peer_receipts':7},
              {'state':completed['state'],'same_operation':completed['operation_id']==unknown['operation_id'],'peer_receipts':len(acknowledgements),'evidence_valid':completed['evidence']['valid']},
              completed['state']=='succeeded' and completed['operation_id']==unknown['operation_id'] and completed['evidence']['valid'] and
              len(acknowledgements)==len(peer_messages)==7)
        ledger=app.environment(tenant,env)['ledger_name']
        effects={l['id']:len(app.accounting.transactions(ledger,l['id'])) for l in completed['operation']['legs']}
        check('06-native-oracle','Native accounting contains exactly one posting per original financial leg',{'all_counts':1},effects,
              bool(effects) and all(n==1 for n in effects.values()))
        refund=app.upi.refund(tenant,env,completed['id'],{'request_key':uid('refund'),'amount_paise':125,'device_id':value['devices'][1]})
        authorize(app,value,refund,1);returned=advance(app,value,refund)
        check('07-partial-refund','A linked partial refund traverses reversed PSP and bank roles',
              {'state':'succeeded','net_paise':400},
              {'state':returned['state'],'parent_id':returned['operation']['parent_id'],'net_paise':before[value['accounts'][0]]-balances()[value['accounts'][0]]},
              returned['state']=='succeeded' and returned['operation']['parent_id']==completed['operation_id'] and returned['evidence']['valid'] and
              balances()[value['accounts'][0]]==before[value['accounts'][0]]-400 and balances()[value['accounts'][1]]==before[value['accounts'][1]]+400)
        check('08-peer-authority','PSP/network receipts do not claim independent ownership of money',{'financial_confirmation':False},
              {'receipts':[{k:json.loads(e['details_json'])['receipt'][k] for k in ['message_id','step','observed_state','financial_confirmation']} for e in acknowledgements]},
              all(json.loads(e['details_json'])['receipt']['financial_confirmation'] is False for e in acknowledgements))
    finally:
        for process in [*peer_processes.values(),*processes]:stop(process)


def lifecycle(app,tenant,env,run_id,check):
    from .synthetic_lab import fixture,prepare,advance
    value=fixture(app,tenant,env);lab=app.fidelity;lab.enable(tenant,env,{})
    pin='739152'
    # Run fixtures use real deadlines. The existing virtual-clock API stays
    # restricted to its original isolated simulation environments.
    def clock(seconds):time.sleep(seconds)
    def setup(index):
        challenge=lab.challenge(tenant,env,{'device_id':value['devices'][index],'account_id':value['accounts'][index],
                    'device_proof':value['proofs'][index],'purpose':'setup','request_key':uid('challenge'),'synthetic_bank_verified':True})
        return lab.complete_credential(tenant,env,challenge['id'],{'device_proof':value['proofs'][index],
                    'synthetic_challenge':challenge['synthetic_challenge'],'new_synthetic_pin':pin})
    for i in range(2):setup(i)
    found=lab.discover(tenant,env,{'device_id':value['devices'][0],'device_proof':value['proofs'][0]})
    check('01-device-discovery','A proved device discovers only its linked customer account',{'accounts':value['accounts'][:1]},
          {'accounts':[r['account_id'] for r in found['accounts']]},[r['account_id'] for r in found['accounts']]==value['accounts'][:1])
    denied(check,'02-device-proof','Another customer cannot borrow the first device proof',
           lambda:lab.discover(tenant,env,{'device_id':value['devices'][1],'device_proof':value['proofs'][0]}),'DEVICE_PROOF')
    payment=prepare(app,value,525)
    denied(check,'03-credential-gate','Enabled credential rules cannot be bypassed through direct payment authorization',
           lambda:app.upi.authorize(tenant,env,payment['id'],{'device_id':value['devices'][0],'device_proof':value['proofs'][0],'approved':True}),'CREDENTIAL_REQUIRED')
    def approve(p,credential=pin):return lab.authorize_payment(tenant,env,p['id'],{'device_id':value['devices'][0],'device_proof':value['proofs'][0],'synthetic_pin':credential,'approved':True})
    denied(check,'04-wrong-pin','Incorrect synthetic PIN is refused by the actual credential verifier',lambda:approve(payment,'000000'),'CREDENTIAL_INVALID')
    approve(payment);paid=advance(app,value,payment)
    check('05-credential-payment','Correct bound credentials complete the original participant payment',{'state':'succeeded'},
          {'state':paid['state'],'evidence_valid':paid['evidence']['valid']},paid['state']=='succeeded' and paid['evidence']['valid'])
    agreement=lab.mandate(tenant,env,{'payer_id':value['accounts'][0],'payee_id':value['accounts'][1],
                'device_id':value['devices'][0],'maximum_paise':1000,'interval_seconds':60,'starts_at':lab.time(tenant,env)+5,'occurrences':2,'request_key':uid('agreement')})
    lab.mandate_action(tenant,env,agreement['id'],'approve',{'device_proof':value['proofs'][0],'synthetic_pin':pin})
    body={'occurrence':0,'amount_paise':125,'request_key':uid('occurrence')};scheduled=lab.schedule(tenant,env,agreement['id'],body)
    check('06-mandate-replay','Recurring occurrence replay preserves one original identity',{'same_identity':True},
          {'same_identity':lab.schedule(tenant,env,agreement['id'],body)['id']==scheduled['id']},lab.schedule(tenant,env,agreement['id'],body)['id']==scheduled['id'])
    denied(check,'07-not-due','A scheduled payment cannot execute before its due time',lambda:lab.execute_occurrence(tenant,env,scheduled['id']),'NOT_DUE')
    clock(5);occurrence=lab.execute_occurrence(tenant,env,scheduled['id']);recurring=advance(app,value,{'id':occurrence['payment_id']})
    check('08-recurring-payment','An approved due occurrence creates one valid financial operation',{'state':'succeeded'},
          {'state':recurring['state'],'evidence_valid':recurring['evidence']['valid']},recurring['state']=='succeeded' and recurring['evidence']['valid'])
    lab.mandate_action(tenant,env,agreement['id'],'revoke',{'device_proof':value['proofs'][0],'synthetic_pin':pin})
    denied(check,'09-revoked-mandate','A revoked agreement cannot schedule another debit',
           lambda:lab.schedule(tenant,env,agreement['id'],{'occurrence':1,'amount_paise':125,'request_key':uid('revoked')}),'MANDATE_INACTIVE')
    complaint=lab.complaint(tenant,env,{'payment_id':paid['id'],'reason':'status_mismatch','request_key':uid('complaint')},'poc-maker')
    lab.investigate(tenant,env,complaint['id'],{},'poc-maker')
    proposal=lab.propose_resolution(tenant,env,complaint['id'],{'resolution':'no_financial_error','request_key':uid('resolution')},'poc-maker')
    denied(check,'10-maker-checker','A complaint maker cannot approve its own resolution',lambda:lab.approve(tenant,env,proposal['id'],{},'poc-maker'),'SECOND_PRINCIPAL_REQUIRED')
    lab.approve(tenant,env,proposal['id'],{},'poc-checker')
    check('11-complaint-resolution','A distinct synthetic checker resolves the evidence-backed complaint',{'state':'resolved'},
          {'state':lab.complaint_read(tenant,env,complaint['id'])['state']},lab.complaint_read(tenant,env,complaint['id'])['state']=='resolved')
    clock(1);cycle=lab.cycle(tenant,env,{'cutoff':lab.time(tenant,env),'request_key':uid('cycle')},'poc-maker')
    positions={value['banks'][0]['id']:-650,value['banks'][1]['id']:650}
    check('12-settlement-oracle','Settlement positions match an independent integer-paise oracle',positions,cycle['manifest']['positions'],cycle['manifest']['positions']==positions)
    lab.reconcile(tenant,env,cycle['id'],{'observed_positions':positions,'request_key':uid('match')},'poc-maker')
    release=lab.propose_release(tenant,env,cycle['id'],{'request_key':uid('release')},'poc-maker')
    lab.approve(tenant,env,release['id'],{},'poc-checker')
    check('13-settlement-evidence','The matched cycle retains a released report with exact allocated payment identities',
          {'payments':2,'csv_available':True},{'payments':len(cycle['manifest']['items']),'csv_bytes':len(lab.csv_report(tenant,env,cycle['id']))},
          len(cycle['manifest']['items'])==2 and bool(lab.csv_report(tenant,env,cycle['id'])))
