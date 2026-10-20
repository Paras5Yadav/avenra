#!/usr/bin/env python3
"""Report factual qualification gates; never promote a deployment by a flag."""
import json
import argparse
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from avenra.util import application_fingerprint
from readiness_evidence import COORDINATED_PROFILES, coordinated_evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=['local', 'institutional_hosting', 'bank_upi_uat', 'aa_interoperability', 'isolated_execution', 'live_upi'], default='institutional_hosting')
    args = parser.parse_args()
    fingerprint = application_fingerprint()
    reports = {}
    files = ['verification.json', 'banking-verification.json', 'banking-postgres-verification.json', 'banking-http-verification.json', 'simulation-verification.json', 'simulation-postgres-verification.json', 'simulation-http-verification.json', 'product-verification.json', 'service-recovery-verification.json', 'unit-verification.json', 'upi-verification.json', 'upi-postgres-verification.json', 'upi-http-verification.json', 'synthetic-bank-verification.json', 'fidelity-verification.json', 'fidelity-postgres-verification.json', 'asgi-verification.json', 'backup-verification.json', 'component-backup-verification.json', 'database-auth-verification.json']
    files.append('production-runtime-verification.json')
    files.extend(['aa-verification.json','aa-postgres-verification.json','accounting-clock-verification.json','financial-data-archive-verification.json'])
    files.append('release-verification.json')
    files.append('checkpoint-verification.json')
    files.extend(['operations-verification.json','operations-upgrade-verification.json','capacity-verification.json'])
    files.append('coordinated-recovery-verification.json')
    files.append('coordinated-upi-wire-recovery-verification.json')
    files.append('coordinated-peer-recovery-verification.json')
    files.append('aa-coordinated-recovery-verification.json')
    files.append('identity-coordinated-recovery-verification.json')
    files.extend(['upi-wire-verification.json', 'upi-wire-postgres-verification.json'])
    for name in files:
        path = ROOT / '.local' / name
        try:
            value = json.loads(path.read_text())
            if not isinstance(value, dict):
                raise ValueError('Qualification report must be an object.')
            independent = name in {'component-backup-verification.json', 'database-auth-verification.json'}
            reports[name] = {'passed': value.get('passed') is True, 'checks': len(value['checks']) if isinstance(value.get('checks'), list) else value.get('tests'), 'application_matches': None if independent else value.get('application_fingerprint') == fingerprint}
            if name in {'fidelity-verification.json', 'fidelity-postgres-verification.json'}:
                from avenra_fidelity.profile import case_pack_hash
                reports[name]['case_pack_matches'] = value.get('case_pack_sha256') == case_pack_hash()
                reports[name]['passed'] = reports[name]['passed'] and reports[name]['case_pack_matches']
            if name == 'production-runtime-verification.json':
                import hashlib
                reports[name]['case_pack_matches'] = value.get('case_pack_sha256') == hashlib.sha256((ROOT / 'scripts/verify_production_runtime.py').read_bytes()).hexdigest()
                reports[name]['passed'] = reports[name]['passed'] and reports[name]['case_pack_matches']
            if name in {'upi-wire-verification.json', 'upi-wire-postgres-verification.json'}:
                from verify_upi_wire import case_pack_hash as wire_case_pack_hash
                reports[name]['case_pack_matches'] = value.get('case_pack_sha256') == wire_case_pack_hash()
                reports[name]['passed'] = reports[name]['passed'] and reports[name]['case_pack_matches']
            if name in {'aa-verification.json','aa-postgres-verification.json'}:
                from verify_aa import case_pack_hash
                reports[name]['case_pack_matches']=value.get('case_pack_sha256')==case_pack_hash()
                reports[name]['passed']=reports[name]['passed'] and reports[name]['case_pack_matches']
            if name == 'release-verification.json':
                from verify_releases import case_pack_hash as release_case_pack_hash
                reports[name]['case_pack_matches'] = value.get('case_pack_sha256') == release_case_pack_hash()
                reports[name]['passed'] = reports[name]['passed'] and reports[name]['case_pack_matches']
            if name == 'checkpoint-verification.json':
                from verify_checkpoints import case_pack_hash as checkpoint_case_pack_hash
                reports[name]['case_pack_matches'] = value.get('case_pack_sha256') == checkpoint_case_pack_hash()
                reports[name]['passed'] = reports[name]['passed'] and reports[name]['case_pack_matches']
            if name in COORDINATED_PROFILES:
                if name == 'identity-coordinated-recovery-verification.json':
                    from verify_identity_recovery import case_pack_hash as recovery_case_pack_hash
                else:
                    from verify_coordinated_recovery import case_pack_hash as recovery_case_pack_hash
                reports[name].update(coordinated_evidence(ROOT, name, value, fingerprint, recovery_case_pack_hash()))
            if name == 'aa-coordinated-recovery-verification.json':
                from verify_aa_recovery import case_pack_hash as aa_recovery_case_pack_hash
                reports[name]['case_pack_matches'] = value.get('case_pack_sha256') == aa_recovery_case_pack_hash()
                reports[name]['passed'] = reports[name]['passed'] and reports[name]['case_pack_matches']
            if name in {'operations-verification.json','operations-upgrade-verification.json','capacity-verification.json'}:
                import hashlib
                script = {'operations-verification.json':'verify_operations.py','operations-upgrade-verification.json':'verify_operations_upgrade.py','capacity-verification.json':'verify_capacity.py'}[name]
                if name == 'capacity-verification.json':
                    from verify_capacity import case_pack_hash as capacity_case_pack_hash
                    expected_case_pack = capacity_case_pack_hash()
                else:
                    expected_case_pack = hashlib.sha256((ROOT / 'scripts' / script).read_bytes()).hexdigest()
                reports[name]['case_pack_matches'] = value.get('case_pack_sha256') == expected_case_pack
                reports[name]['passed'] = reports[name]['passed'] and reports[name]['case_pack_matches']
                if name == 'capacity-verification.json':
                    from verify_capacity import REFERENCE
                    from avenra.util import digest
                    reports[name]['reference_profile_matches'] = value.get('mode') == 'reference' and value.get('profile') == REFERENCE and value.get('reference_profile_sha256') == digest(REFERENCE) and value.get('reference_profile_passed') is True
                    reports[name]['passed'] = reports[name]['passed'] and reports[name]['reference_profile_matches']
                    details=value.get('details',{});detail_path=Path(details.get('path','')).resolve()
                    complete=json.loads(detail_path.read_text()) if detail_path.is_relative_to(ROOT/'.local/qa') and detail_path.is_file() and not detail_path.is_symlink() else {}
                    reports[name]['complete_evidence_matches'] = bool(complete) and hashlib.sha256(detail_path.read_bytes()).hexdigest()==details.get('sha256') and all(complete.get(key)==value.get(key) for key in ['passed','reference_profile_passed','application_fingerprint','case_pack_sha256','profile','reference_profile_sha256','checks','cohorts','offered','accepted','refused'])
                    reports[name]['passed'] = reports[name]['passed'] and reports[name]['complete_evidence_matches']
        except (OSError, ValueError):
            reports[name] = {'passed': False, 'reason': 'No retained qualification report.'}
    local_passed = all(x['passed'] and x.get('application_matches') is not False for x in reports.values())
    gaps = {
        'institutional_hosting': ['Production identity-provider deployment and role-policy acceptance', 'Institutional TLS ingress and internal authenticated/encrypted service deployment; local ASGI and separate-worker checks are exercised', 'Independent security review and dependency vulnerability remediation', 'Measured capacity, failover, restore-time objectives and operational acceptance', 'Encrypted off-host backup and retention policy; message-broker recovery qualification'],
        'bank_upi_uat': ['Versioned participant-specific UPI wire and security implementation with authorized test pack', 'Bank-issued UAT identity, certificates, endpoints and approved connectivity', 'Observed bank acceptance cases and reconciliation/settlement report qualification'],
        'aa_interoperability': ['Public FIP version metadata mismatch, error-schema inconsistencies and malformed interoperability schema resolution', 'Complete declared role/version coverage, other FI schemas, legacy curve and newer inter-AA contracts; selected synthetic deposit roles/signatures/encryption now execute', 'Managed participant registry/key custody and independent interoperability cases/participant acceptance'],
        'isolated_execution': ['Linux x86_64/KVM worker and full engine build/deployment', 'Observed VM boot, command, lifecycle, egress, tenant-isolation and recovery qualification', 'Run-scoped execution credentials and integration into scenario scheduling'],
        'live_upi': ['A qualified live participant integration and its required institutional authorization; this product currently has no live-rail connector']}
    value = {'created_at': time.time(), 'application_fingerprint': fingerprint, 'local_acceptance_passed': local_passed, 'production_ready': False, 'reports': reports, 'profiles': {key: {'ready': False, 'remaining': items} for key, items in gaps.items()}, 'public_contract_registry': str(ROOT / 'LICENSES/protocol-profiles.json'), 'scope': 'Engineering readiness inventory. Local test passes do not establish deployment qualification, regulatory approval or bank interoperability.'}
    observed = reports['production-runtime-verification.json']
    value['synthetic_runtime_qualification'] = {'passed_for_current_build': observed['passed'] and observed.get('application_matches') is True,
        'checks': observed.get('checks'), 'scope': 'Observed local TLS/SSO/roles, shared quotas, least-privilege database, deadline handling, worker failure and inactive private deployment recovery. Whole-product production qualification remains open.'}
    observed_aa=[reports[n] for n in ['aa-verification.json','aa-postgres-verification.json']]
    value['synthetic_financial_data_qualification']={'passed_for_current_build':all(x['passed'] and x.get('application_matches') is True for x in observed_aa),
        'checks':sum(x.get('checks') or 0 for x in observed_aa),'official_conformance':False,'scope':'Locally deployed AA/FIP/FIU deposit subset: actual mTLS/JWS, X25519/HKDF/AES-GCM, native statements, independent Node/OpenSSL decryption, bounded consent, concurrent admission, response-loss recovery, provider restart and inactive role recovery. Full official interoperability remains open.'}
    release_observed = reports['release-verification.json']
    value['synthetic_release_qualification'] = {'passed_for_current_build': release_observed['passed'] and release_observed.get('application_matches') is True,
        'checks': release_observed.get('checks'), 'scope': 'Observed exact-platform signed application packaging, offline dependency/runtime preparation, database-fenced schema-preserving upgrade, stale-node refusal, code rollback with forward financial history and actual operator-crash recovery. A separate receipt covers one reviewed additive operations migration; general schema evolution, native-service upgrades and fleet qualification remain open.'}
    ops_observed = reports['operations-verification.json']
    value['synthetic_operational_qualification'] = {'passed_for_current_build':ops_observed['passed'] and ops_observed.get('application_matches') is True,
        'checks':ops_observed.get('checks'),'scope':'Scoped monitoring, durable incident/acknowledgement history, authenticated pagination, actual worker/accounting outages, monitor-owner loss and original native financial recovery. Local synthetic deployment only.'}
    migration_observed = reports['operations-upgrade-verification.json']
    value['synthetic_additive_migration_qualification'] = {'passed_for_current_build':migration_observed['passed'] and migration_observed.get('application_matches') is True,
        'checks':migration_observed.get('checks'),'scope':'A preserved signed predecessor, one closed additive operations migration, process death inside DDL, rollback/retry and old-code rollback with new tables retained. General migrations and fleet rollouts remain open.'}
    capacity_observed = reports['capacity-verification.json']
    value['synthetic_capacity_qualification'] = {'passed_for_current_build':capacity_observed['passed'] and capacity_observed.get('application_matches') is True,
        'checks':capacity_observed.get('checks'),'scope':'The exact published local two-tenant/two-API/two-worker workload and latency/error budgets, controlled overload, real worker kills and native journal comparison. Smoke runs are ineligible; institutional capacity and fleet availability remain open.'}
    recovery_observed = reports['coordinated-recovery-verification.json']
    value['synthetic_coordinated_recovery_qualification'] = {'passed_for_current_build':recovery_observed['passed'] and recovery_observed.get('application_matches') is True,
        'checks':recovery_observed.get('checks'),'scope':'Cold selected local/TLS synthetic participant deployment: independent external recovery/application trust, encrypted set, new database roles, complete row/sequence comparison, rebuilt offline runtime, pinned owned native listeners and separate mTLS banks at authenticated new physical addresses. Actual operator crashes, key rotation and original-ID recovery retain byte-identical accepted bindings without source installation/listener use. AA/SSO/provider/service rebinding, broker replay, off-host retention and institutional RPO/RTO remain open.'}
    path = ROOT / '.local/release-readiness.json'
    aa_recovery_observed=reports['aa-coordinated-recovery-verification.json']
    value['synthetic_aa_recovery_qualification']={'passed_for_current_build':aa_recovery_observed['passed'] and aa_recovery_observed.get('application_matches') is True,
        'checks':aa_recovery_observed.get('checks'),'scope':'Owned cold local AA/FIP/FIU capture and address replacement with process fences, real TLS/signatures, original encrypted sessions/notices, expiry, operator death and source preservation. SSO/provider/monitoring/broker/off-host and institutional recovery qualification remain open.'}
    wire_observed = [reports[name] for name in ['upi-wire-verification.json', 'upi-wire-postgres-verification.json']]
    value['synthetic_upi_wire_qualification'] = {'passed_for_current_build':all(x['passed'] and x.get('application_matches') is True for x in wire_observed),
        'checks':sum(x.get('checks') or 0 for x in wire_observed), 'official_conformance':False,
        'scope':'Owned versioned XML contract and independent Go parser/signature oracle; scoped ASGI wire ingress/egress; original payment identity, real mTLS bank financial legs and post-commit process death. Official UPI XML/security and distributed PSP/network transport remain unqualified.'}
    xml_recovery_observed = reports['coordinated-upi-wire-recovery-verification.json']
    value['synthetic_upi_wire_recovery_qualification'] = {'passed_for_current_build':xml_recovery_observed['passed'] and xml_recovery_observed.get('application_matches') is True,
        'checks':xml_recovery_observed.get('checks'), 'official_conformance':False,
        'scope':'Cold local recovery with both XML participant contracts, unchanged original envelopes/signatures, new bank addresses, original-ID financial recovery and refusal of a genuine signed predecessor lacking retained contract support. Separate from institutional or off-host recovery qualification.'}
    peer_recovery_observed = reports['coordinated-peer-recovery-verification.json']
    value['synthetic_independent_participant_recovery_qualification'] = {
        'passed_for_current_build': peer_recovery_observed['passed'] and peer_recovery_observed.get('application_matches') is True,
        'checks': peer_recovery_observed.get('checks'), 'official_conformance': False,
        'scope': 'Five separately owned local mTLS PSP/network inboxes, authenticated address replacement, original signed receipts, real bank/network process loss and original financial recovery through two cold generations. This dedicated mode is mandatory and cannot substitute for legacy or XML-only recovery. Off-host, institutional RPO/RTO and independent acceptance remain open.'}
    identity_observed = reports['identity-coordinated-recovery-verification.json']
    value['synthetic_identity_recovery_qualification'] = {
        'passed_for_current_build': identity_observed['passed'] and identity_observed.get('application_matches') is True,
        'checks': identity_observed.get('checks'), 'official_conformance': False,
        'scope': 'Owned static synthetic issuer and HTTPS API at authenticated replacement addresses, unchanged logical issuer/subjects/keys, original sessions/grants/expiry, code/PKCE sign-in, same-person checking, operator/issuer death and original mTLS bank effects through two cold generations. Stable-origin discovery, external IdPs, combined AA/HTTPS recovery, off-host and institutional qualification remain open.'}
    qualified = local_passed if args.profile == 'local' else local_passed and value['profiles'][args.profile]['ready']
    value.update(selected_profile=args.profile, selected_profile_ready=qualified)
    path.write_text(json.dumps(value, indent=2) + '\n')
    print(json.dumps({'local_acceptance_passed': local_passed, 'production_ready': False, 'selected_profile': args.profile, 'selected_profile_ready': qualified, 'unqualified_profiles': list(gaps), 'report': str(path)}))
    return 0 if qualified else 2


if __name__ == '__main__':
    raise SystemExit(main())
