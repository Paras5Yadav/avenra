"""Synthetic complaint investigation, settlement files and two-principal approvals."""
import csv
import io
import json
import math

from avenra.errors import DomainError, require
from avenra.upi import fields
from avenra.util import canonical, digest, now, reference, uid
from .profile import PROFILE, PROFILE_HASH, fingerprint


class BackOffice:
    def complaint(self, tenant, env, data, actor):
        fields(data, ['payment_id', 'reason', 'request_key'])
        require(data['reason'] in {'status_mismatch', 'beneficiary_not_credited', 'duplicate_debit', 'refund_pending'}, 'INVALID_COMPLAINT', 'Choose a supported synthetic complaint category.')
        key, hashed = reference(data['request_key']), digest(data)
        with self.store.transaction() as db:
            self.require_enabled(tenant, env, db)
            old = self.bank._existing(db, 'fidelity_complaints', tenant, env, key, hashed)
            if old: return old
            self.get(db, 'upi_payments', tenant, env, data['payment_id'])
            identity = uid('complaint')
            db.execute('INSERT INTO fidelity_complaints VALUES(?,?,?,?,?,?,?,0,1,NULL,?,?,?)', (identity, tenant, env, data['payment_id'], data['reason'], 'open', self.time(tenant, env, db) + PROFILE['rules']['complaint_seconds'], key, hashed, now()))
            self.event(db, tenant, env, identity, 'complaint.opened', actor, {'payment_id': data['payment_id'], 'reason': data['reason']})
        return self.complaint_read(tenant, env, identity)

    def complaint_read(self, tenant, env, identity):
        with self.store.reader() as db:
            value = self.get(db, 'fidelity_complaints', tenant, env, identity)
            value['events'] = [dict(x) for x in db.execute('SELECT * FROM fidelity_events WHERE resource_id=? ORDER BY id', (identity,))]
        payment = self.upi.read(tenant, env, value['payment_id'])
        value['payment_observation'] = {'payment_id': payment['id'], 'state': payment['state'], 'evidence_valid': payment['evidence']['valid'] if payment['evidence'] else False,
                                        'financial_legs': payment['operation']['legs'] if payment['operation'] else []}
        return value

    def investigate(self, tenant, env, identity, data, actor):
        fields(data, [])
        with self.store.transaction() as db:
            self.require_enabled(tenant, env, db)
            value = self.get(db, 'fidelity_complaints', tenant, env, identity)
            require(value['state'] != 'resolved', 'COMPLAINT_CLOSED', 'A resolved case retains its final decision.', 409)
            if value['state'] != 'investigating':
                db.execute("UPDATE fidelity_complaints SET state='investigating',version=version+1 WHERE id=?", (identity,))
                self.event(db, tenant, env, identity, 'complaint.investigating', actor, {'original_payment': value['payment_id']})
        return self.complaint_read(tenant, env, identity)

    def _proposal(self, db, tenant, env, resource, kind, document, key, actor):
        key = reference(key)
        request_hash = digest({'resource': resource, 'kind': kind, 'document': document, 'maker': actor})
        old = self.bank._existing(db, 'fidelity_proposals', tenant, env, key, request_hash)
        if old: return old
        identity = uid('proposal')
        db.execute('INSERT INTO fidelity_proposals VALUES(?,?,?,?,?,?,?,?,?,NULL,NULL,?,?,?)',
                   (identity, tenant, env, resource, kind, actor, canonical(document), digest(document), 'pending', key, request_hash, now()))
        self.event(db, tenant, env, resource, kind + '.proposed', actor, {'proposal_id': identity, 'document_hash': digest(document)})
        return self.get(db, 'fidelity_proposals', tenant, env, identity)

    def propose_resolution(self, tenant, env, identity, data, actor):
        fields(data, ['resolution', 'request_key'])
        require(data['resolution'] in {'no_financial_error', 'refunded', 'declined', 'reversed'}, 'INVALID_RESOLUTION', 'Use an observable financial resolution.')
        case = self.complaint_read(tenant, env, identity)
        payment = self.upi.read(tenant, env, case['payment_id'])
        require(payment['evidence'] and payment['evidence']['valid'], 'RECOVERY_REQUIRED', 'Resolve the original financial uncertainty and retain evidence first.', 409)
        require((data['resolution'] == 'no_financial_error' and payment['state'] == 'succeeded') or data['resolution'] in {payment['state'], 'refunded'}, 'RESOLUTION_UNSUPPORTED', 'Resolution differs from the observed original payment.', 409)
        with self.store.transaction() as db:
            self.require_enabled(tenant, env, db)
            current = self.get(db, 'fidelity_complaints', tenant, env, identity)
            require(current['state'] == 'investigating', 'COMPLAINT_STATE', 'Investigate this case before proposing resolution.', 409)
            refunded = 0
            if data['resolution'] == 'refunded':
                refunded = db.execute("SELECT COALESCE(SUM(amount_paise),0) FROM upi_payments WHERE parent_id=? AND state='succeeded' AND evidence_json IS NOT NULL", (payment['id'],)).fetchone()[0]
                require(refunded == payment['amount_paise'], 'REFUND_NOT_CONFIRMED', 'A full refund resolution requires confirmed linked refunds for the original amount.', 409)
            document = {'case_id': identity, 'case_version': current['version'], 'resolution': data['resolution'], 'payment_id': payment['id'], 'evidence_hash': payment['evidence']['manifest_hash'], 'refunded_paise': refunded, 'profile_hash': PROFILE_HASH}
            return self._proposal(db, tenant, env, identity, 'complaint_resolution', document, data['request_key'], actor)

    def cycle(self, tenant, env, data, actor):
        fields(data, ['cutoff', 'request_key'])
        cutoff = data['cutoff']
        require(type(cutoff) in {int, float} and math.isfinite(cutoff), 'INVALID_TIME', 'Use a finite synthetic cutoff.')
        key, hashed = reference(data['request_key']), digest(data)
        with self.store.reader() as db:
            self.require_enabled(tenant, env, db)
            old = self.bank._existing(db, 'fidelity_cycles', tenant, env, key, hashed)
            if old: return self.cycle_read(tenant, env, old['id'])
            profile = self.enabled(tenant, env, db)
            previous = db.execute('SELECT MAX(cutoff) FROM fidelity_cycles WHERE tenant_id=? AND environment_id=?', (tenant, env)).fetchone()[0]
            require((previous is None or cutoff > previous) and profile['enabled_at'] <= cutoff <= self.time(tenant, env, db), 'INVALID_CUTOFF', 'Cutoffs advance monotonically within elapsed business time.', 409)
            rows = [dict(x) for x in db.execute('SELECT p.*,g.authorized_at,a.bank_id payer_bank,b.bank_id payee_bank FROM upi_payments p JOIN fidelity_guards g ON g.payment_id=p.id JOIN bank_accounts a ON a.account_id=p.payer_id JOIN bank_accounts b ON b.account_id=p.payee_id WHERE p.tenant_id=? AND p.environment_id=? AND g.authorized_at<? AND NOT EXISTS(SELECT 1 FROM fidelity_cycle_items i WHERE i.payment_id=p.id) ORDER BY g.authorized_at,p.id LIMIT ?', (tenant, env, cutoff, PROFILE['rules']['maximum_report_items'] + 1))]
        require(len(rows) <= PROFILE['rules']['maximum_report_items'], 'REPORT_CAPACITY', 'Cycle exceeds the explicit report budget; use smaller cutoffs.', 409)
        items, unresolved, positions = [], [], {}
        for row in rows:
            payment = self.upi.read(tenant, env, row['id'])
            if payment['state'] in {'declined', 'reversed', 'expired', 'cancelled'}: continue
            if payment['state'] != 'succeeded' or not payment['evidence']:
                unresolved.append({'payment_id': row['id'], 'state': payment['state'], 'amount_paise': row['amount_paise']}); continue
            require(payment['evidence']['valid'], 'EVIDENCE_INVALID', 'Payment evidence did not verify.', 409)
            legs = [x for x in payment['operation']['legs'] if x['state'] == 'confirmed']
            require({x['kind'] for x in legs} == {'debit', 'credit'}, 'FINANCIAL_INVARIANT', 'Settlement requires distinct confirmed debit and credit.', 409)
            for bank, delta in [(row['payer_bank'], -row['amount_paise']), (row['payee_bank'], row['amount_paise'])]:
                positions[bank] = positions.get(bank, 0) + delta
            items.append({'payment_id': row['id'], 'mode': row['mode'], 'parent_id': row['parent_id'], 'amount_paise': row['amount_paise'],
                          'payer_bank': row['payer_bank'], 'payee_bank': row['payee_bank'], 'authorized_at': row['authorized_at'],
                          'late': previous is not None and row['authorized_at'] < previous, 'native_legs': [{k: leg[k] for k in ['id', 'kind', 'ledger_id']} for leg in legs], 'evidence_hash': payment['evidence']['manifest_hash']})
        require(sum(positions.values()) == 0, 'FINANCIAL_INVARIANT', 'Synthetic clearing positions do not balance.', 409)
        identity = uid('cycle')
        manifest = {'format': 'avenra-synthetic-settlement/1', 'id': identity, 'tenant_id': tenant, 'environment_id': env, 'profile_hash': PROFILE_HASH,
                    'fingerprint': fingerprint(), 'previous_cutoff': previous, 'cutoff': cutoff, 'interval': 'authorized_at < cutoff; unallocated late completions carry into a subsequent cycle',
                    'positions': positions, 'items': items, 'unresolved': unresolved, 'official_conformance': False, 'scope': 'Synthetic clearing positions only; no settlement-account transfer is executed.'}
        manifest_hash, signature = self.sign(manifest)
        with self.store.transaction() as db:
            # Recheck after external reads: competing makers cannot allocate twice.
            old = self.bank._existing(db, 'fidelity_cycles', tenant, env, key, hashed)
            if old: return self.cycle_read(tenant, env, old['id'])
            latest = db.execute('SELECT MAX(cutoff) FROM fidelity_cycles WHERE tenant_id=? AND environment_id=?', (tenant, env)).fetchone()[0]
            require(latest == previous, 'CYCLE_CONFLICT', 'A concurrent cycle changed the allocation boundary; inspect it and retry.', 409)
            db.execute('INSERT INTO fidelity_cycles VALUES(?,?,?,?,?,?,?,?,?,?,?)', (identity, tenant, env, cutoff, 'prepared', canonical(manifest), manifest_hash, signature, key, hashed, now()))
            for item in items: db.execute('INSERT INTO fidelity_cycle_items VALUES(?,?)', (item['payment_id'], identity))
            self.event(db, tenant, env, identity, 'cycle.prepared', actor, {'manifest_hash': manifest_hash, 'items': len(items), 'unresolved': len(unresolved)})
        return self.cycle_read(tenant, env, identity)

    def cycle_read(self, tenant, env, identity):
        with self.store.reader() as db:
            value = self.get(db, 'fidelity_cycles', tenant, env, identity)
            value['reconciliations'] = [dict(x) for x in db.execute('SELECT * FROM fidelity_reconciliations WHERE cycle_id=? ORDER BY created_at,id', (identity,))]
            value['releases'] = [dict(x) for x in db.execute("SELECT * FROM fidelity_proposals WHERE resource_id=? AND kind='cycle_release' ORDER BY created_at,id", (identity,))]
        value['manifest'] = json.loads(value.pop('manifest_json'))
        hashed, signature = self.sign(value['manifest'])
        value['valid'] = hashed == value['manifest_hash'] and signature == value['signature']
        return value

    def reconcile(self, tenant, env, identity, data, actor):
        fields(data, ['observed_positions', 'request_key'])
        observed = data['observed_positions']
        require(isinstance(observed, dict) and len(observed) <= 1000 and all(isinstance(k, str) and type(v) is int and abs(v) <= 10**15 for k, v in observed.items()), 'INVALID_POSITIONS', 'Supply independently observed bank-to-integer-paise positions.')
        key, hashed = reference(data['request_key']), digest({'cycle_id': identity, **data})
        with self.store.transaction() as db:
            self.require_enabled(tenant, env, db)
            cycle = self.get(db, 'fidelity_cycles', tenant, env, identity)
            old = self.bank._existing(db, 'fidelity_reconciliations', tenant, env, key, hashed)
            if old: return old
            expected = json.loads(cycle['manifest_json'])['positions']
            exceptions = [{'bank_id': bank, 'expected_paise': expected.get(bank), 'observed_paise': observed.get(bank)} for bank in sorted(set(expected) | set(observed)) if expected.get(bank) != observed.get(bank)]
            report = uid('reconciliation')
            document = {'cycle_hash': cycle['manifest_hash'], 'observed_positions': observed, 'exceptions': exceptions}
            report_hash, signature = self.sign(document)
            db.execute('INSERT INTO fidelity_reconciliations VALUES(?,?,?,?,?,?,?,?,?,?,?)', (report, tenant, env, identity, canonical(observed), canonical(exceptions), report_hash, signature, key, hashed, now()))
            self.event(db, tenant, env, identity, 'cycle.reconciled', actor, {'report_id': report, 'exceptions': len(exceptions)})
            return self.get(db, 'fidelity_reconciliations', tenant, env, report)

    def propose_release(self, tenant, env, identity, data, actor):
        fields(data, ['request_key'])
        with self.store.transaction() as db:
            self.require_enabled(tenant, env, db)
            cycle = self.get(db, 'fidelity_cycles', tenant, env, identity)
            require(not db.execute("SELECT 1 FROM fidelity_proposals WHERE resource_id=? AND kind='cycle_release' AND state='approved'", (identity,)).fetchone(), 'CYCLE_RELEASED', 'This synthetic settlement report has already been released.', 409)
            report = db.execute('SELECT * FROM fidelity_reconciliations WHERE cycle_id=? ORDER BY created_at DESC,id DESC LIMIT 1', (identity,)).fetchone()
            require(report and json.loads(report['exceptions_json']) == [], 'RECONCILIATION_REQUIRED', 'The latest independent position report must match before release.', 409)
            return self._proposal(db, tenant, env, identity, 'cycle_release', {'cycle_id': identity, 'cycle_hash': cycle['manifest_hash'], 'reconciliation_id': report['id'], 'reconciliation_hash': report['manifest_hash']}, data['request_key'], actor)

    def approve(self, tenant, env, identity, data, actor):
        fields(data, [])
        with self.store.transaction() as db:
            self.require_enabled(tenant, env, db)
            proposal = self.get(db, 'fidelity_proposals', tenant, env, identity)
            require(actor and actor != proposal['maker'], 'SECOND_PRINCIPAL_REQUIRED', 'A different authenticated principal must check this proposal.', 403)
            if proposal['state'] == 'approved':
                require(proposal['checker'] == actor, 'APPROVAL_CONFLICT', 'This proposal already has a different checker.', 409)
                return proposal
            document = json.loads(proposal['document_json'])
            if proposal['kind'] == 'complaint_resolution':
                complaint = self.get(db, 'fidelity_complaints', tenant, env, proposal['resource_id'])
                require(complaint['state'] == 'investigating' and complaint['version'] == document['case_version'], 'PROPOSAL_STALE', 'Complaint changed after this proposal.', 409)
                payment = self.get(db, 'upi_payments', tenant, env, complaint['payment_id'])
                require(payment['evidence_json'] and json.loads(payment['evidence_json'])['manifest_hash'] == document['evidence_hash'], 'PROPOSAL_STALE', 'Financial evidence changed.', 409)
                db.execute("UPDATE fidelity_complaints SET state='resolved',version=version+1,resolution_json=? WHERE id=?", (canonical(document), complaint['id']))
            else:
                require(proposal['kind'] == 'cycle_release', 'INVALID_PROPOSAL', 'Unsupported proposal type.')
                cycle = self.get(db, 'fidelity_cycles', tenant, env, proposal['resource_id'])
                report = db.execute('SELECT * FROM fidelity_reconciliations WHERE cycle_id=? ORDER BY created_at DESC,id DESC LIMIT 1', (cycle['id'],)).fetchone()
                require(cycle['manifest_hash'] == document['cycle_hash'] and report and report['id'] == document['reconciliation_id'] and not json.loads(report['exceptions_json']), 'PROPOSAL_STALE', 'Settlement observations changed after the proposal.', 409)
            decision = {'proposal_id': identity, 'document_hash': proposal['document_hash'], 'maker': proposal['maker'], 'checker': actor, 'at': now(), 'profile_hash': PROFILE_HASH}
            hashed, signature = self.sign(decision)
            decision.update(manifest_hash=hashed, signature=signature)
            db.execute("UPDATE fidelity_proposals SET state='approved',checker=?,decision_json=? WHERE id=?", (actor, canonical(decision), identity))
            self.event(db, tenant, env, proposal['resource_id'], proposal['kind'] + '.approved', actor, {'proposal_id': identity, 'decision_hash': hashed})
            return self.get(db, 'fidelity_proposals', tenant, env, identity)

    def csv_report(self, tenant, env, identity):
        report = self.cycle_read(tenant, env, identity)
        require(report['valid'], 'EVIDENCE_INVALID', 'Cycle report did not verify.', 409)
        buffer = io.StringIO(newline='')
        writer = csv.writer(buffer)
        writer.writerow(['synthetic_cycle', 'payment_id', 'mode', 'parent_id', 'payer_bank', 'payee_bank', 'amount_paise', 'late', 'evidence_hash'])
        for row in report['manifest']['items']:
            writer.writerow([identity, row['payment_id'], row['mode'], row['parent_id'], row['payer_bank'], row['payee_bank'], row['amount_paise'], row['late'], row['evidence_hash']])
        return buffer.getvalue().encode()
