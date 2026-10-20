# Full-scope synthetic POC

The POC is part of the existing app. It retains the full original synthetic
UPI/NPCI/RBI-oriented production objective. It is not limited to cryptography or
to the cases that already pass. The Scenario library displays every scope area,
and implemented demonstrations produce ordinary Runs & evidence records.

The checklist must include payments/refunds/reversals and uncertainty;
independent banks/PSPs/network transport and recovery; customer/device and
credential journeys; mandates/complaints/settlement; specialized stored-value,
delegated, credit and cross-border profiles; AA; cryptography and key lifecycle;
identity/permissions/service trust; VM execution; load/failover/disaster recovery;
monitoring/incidents; releases/rollback; and contract/acceptance evidence.

Each area needs an executable demonstration with observed outcomes. An area
without one remains `remaining`. A current-build passing demonstration is scoped
to its initiating tenant/environment and locally authenticated evidence. The
whole POC remains incomplete while required demonstrations are missing.

The current candidate adds eight executable cases to the existing Scenario
library and a button to queue all available demonstrations. Their outcomes and
locally authenticated evidence use the existing Runs & evidence screen. The
same-app HTTP acceptance pack is `scripts/verify_poc.py`; it rejects run identity
substitution, checks workspace access, proves that source-environment balances
remain unchanged, and binds every successful result to the loaded source build.

The cases demonstrate independent bank/PSP/network payment and refund recovery;
customer credentials, recurring payments, complaint approvals and settlement;
deposit consent and encrypted AA information with independent decryption;
scoped authorization and revocation, admission, incidents and concurrent native
effects; deterministic signed application packaging; hostile XML vectors checked
by an independent implementation; actual VM execution; and cryptography. A
passing case proves its stated assertions. It does not qualify every function
listed in a broader scope area. In particular, local encrypted evidence restore
does not establish whole-host DR, packaging does not deploy a rollback, and
capability fixtures do not qualify an institution's identity provider.

Specialized stored-value/offline, delegated, credit and cross-border journeys
remain unsupported. Full POC completion therefore remains false. Broader
production qualification stays in `PRODUCTION_COMPLETION_MATRIX.md`.

## Real execution

The ARM64 KVM POC uses a dedicated Linux worker on this Mac. It has no mounted
host directories. Submitted scripts run as an unprivileged user inside an
ephemeral second-level Linux guest with KVM acceleration. The VMM reports actual
CPU/memory/KVM state; the guest has no network device or host filesystem share.
Requests and receipts are authenticated and bind the scope, script and limits.
Duplicate execution identities return their original results. Changed content,
expired credentials and invalid authentication are refused. Deadlines and output
limits terminate and reap the owned VMM process.

This is a distinct ARM64 POC backend. It does not qualify the included x86_64
execution engine, prove absence of hypervisor vulnerabilities, or replace
independent isolation/security assessment. Those production requirements remain
in `PRODUCTION_COMPLETION_MATRIX.md`.

## Cryptography and security

The cryptography demonstration uses real Ed25519 verification,
X25519/HKDF/AES-GCM and authenticated scope binding. It also rotates and revokes
the actual participant key used by an accepted payment, observes refusal before
a first debit, and recovers the original financial operation with the new key.
PINs and money are synthetic; the underlying algorithms and native accounting
effects are real. Secret keys and synthetic device proofs are not exported in
the demonstration evidence.

## External judgments

The POC can exercise owned contracts, hostile requests, security controls,
failure/recovery and an acceptance workflow. It cannot grant itself an independent
assessment, bank/NPCI acceptance or RBI admission. An external decision may be
rehearsed as explicitly synthetic data; it must never be displayed as an approval
that has actually been received. Unknown official details remain unverified.
