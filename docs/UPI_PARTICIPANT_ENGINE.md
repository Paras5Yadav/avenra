# Avenra participant payment engine

This document describes executable software in `avenra/upi.py`. It is a synthetic participant contract, `avenra-upi-participants/1`. Current official UPI wire compatibility, bank acceptance and institutional production qualification remain incomplete. This implementation must not be described as an exact reproduction of an undisclosed or unspecified network version.

The [participant connection boundary](PARTICIPANT_CONNECTIONS.md) adds immutable revisions, signed configuration checks and per-intent adapter snapshots. The local adapter executes; external preparation records cannot send requests or be activated. This boundary does not supply the missing institutional adapter or specifications.

## Executable financial journey

An operator configures virtual institutions, customers and accounts. Installing a topology creates independent payer PSP, network, remitter, beneficiary and payee PSP actors. The two bank actors use separate account addresses in the authoritative accounting engine. The control database stores intent, delivery and observations; it has no editable customer balance.

1. Enrol a synthetic device, prove its enrolment challenge and record an explicit simulated verification result.
2. Link that active device to its customer's account and payer PSP. Account ownership is checked.
3. Prepare a push payment, merchant collect or supported QR/intent payment. Preparation cannot spend funds.
4. For collect, deliver the payee PSP request through the network to the payer PSP before accepting approval. Person-to-person collect is unsupported by this profile.
5. Approve or reject the exact intent using the linked device proof. The recorded signed decision binds the intent hash, device, profile and expiry. No real PIN, bank OTP, SIM verification or Common Library implementation is implied.
6. Deliver `payment.submit`, `debit.request`, `debit.response`, `credit.request`, `credit.response`, `payment.result` and `merchant.notification` through durable recipient inboxes.
7. Only the remitter request executes the debit. Only an authenticated subsequent beneficiary request executes the credit, after the debit is confirmed. Generic financial workers and reconcile endpoints cannot execute these participant-owned operations.
8. Record each actor's own observed state. Bank completion and PSP notification can be separated by delayed or missing messages.
9. Seal evidence after delivery completes, using independently retrieved native journal postings. A successful transfer requires one exact debit and one exact credit; a reversed transfer requires the original debit and a separate compensation.

Device enrolment lasts 300 virtual seconds. Intent approval validity is configurable from 1 to 3,600 seconds. Participant envelopes last 300 seconds. These are Avenra lab choices, not assertions about official network deadlines. Business time may be frozen in a scenario fixture; infrastructure leases and request deadlines use real time.

## Recovery and security boundaries

Requests and financial fingerprints are persisted before dispatch. A financial leg is durably marked uncertain before its external call. An interrupted or missing response reuses the original financial identity and compares observed native postings. A timeout cannot prove that an attempted debit or credit failed.

A blocked account, revoked device or expired approval prevents an unattempted first debit. Expiry does not discard a financial effect that may already have committed. A beneficiary rejection after debit creates a separate reversal from suspense.

Envelopes use distinct participant Ed25519 keys, exact immutable content hashes, environment/profile binding, timestamp checks and retained recipient receipts. Rotation retains old verification keys until explicit revocation. A replay of an accepted envelope returns the original receipt. Altered content or authentication is refused. Recovery can create a new transport attempt signed by the current key while retaining the financial intent and leg identities.

Lab private keys are derived from the installation key and participant/epoch context. They are not independent bank-held keys or HSM custody. Production key management, trust issuance and bank-specific cryptography need separate implementation and acceptance.

Delivery faults include a dropped message, a duplicate, an acknowledgement lost after recipient commit, corrupted authentication and a delay. Accounting response-loss faults use the actual local fault service. Participant pause/resume affects delivery. Superseded expired/rejected messages remain retained. Unapproved intents have a durable expiry job and can close without financial dispatch.

Refunds are separate participant payments linked to the original transfer. The original merchant becomes the payer, with its own device approval. Native pending and completed adjustments reserve the original refundable amount atomically.

The QR decoder implements a bounded `upi://pay` subset: `pa`, `am`, `cu`, with optional `pn`, `tr`, `tn`. INR amounts convert exactly to integer paise. Duplicate, malformed and unsupported parameters are refused. The resolved local beneficiary identity is displayed independently from a QR's suggested name. QR content cannot authorize a debit.

## Scoped API

Prefix: `/api/tenants/{tenant}/environments/{environment}/banking/upi`. Existing API-key/SSO/session, permission and CSRF guards apply.

| Method and suffix | Behaviour |
| --- | --- |
| `GET /` | Profile, explicit qualification gaps, actors, public verification keys, devices, links and payments |
| `POST /participants` | Install actors for configured institutions; body `{}` |
| `POST /participants/{id}/pause` or `/resume` | Change actor availability; body `{}` |
| `POST /participants/{id}/rotate-key` | Create the next signing epoch; body `{}` |
| `POST /participants/{id}/revoke-key` | Revoke an old key using `key_id`; rotate before revoking a current key |
| `POST /devices` | `customer_id`, `device_reference`, `request_key`; returns a synthetic enrolment challenge |
| `POST /devices/{id}/activate` | `device_proof`, `synthetic_verification: true` |
| `POST /devices/{id}/revoke` | Irreversibly revoke this synthetic device; body `{}` |
| `POST /links` | `device_id`, `account_id`, `psp_id` |
| `POST /balance` | Read native funds using `link_id`, `device_proof` |
| `POST /qr` | `payee_vpa`, `amount_paise`, `reference` |
| `POST /qr/decode` | Decode `uri` without authorizing it |
| `POST /payments` | `request_key`, `payer_id`, `payee_id`, `amount_paise`; optional `device_id`, `mode`, `expires_in_seconds`, `financial_fault`, `delivery_fault`, `qr_uri` |
| `POST /payments/{id}/authorize` | `device_id`, `device_proof`, `approved` boolean |
| `GET /payments/{id}` or `/status` | Independent actor views, message delivery, bank legs, events and evidence |
| `POST /payments/{id}/reconcile` | Schedule status recovery using the original financial identity; body `{}` |
| `POST /payments/{id}/refund` | `request_key`, `amount_paise`; optional device/fault settings; requires separate authorization |
| `GET /payments/{id}/evidence` | Completed integrity-checked evidence; pending evidence returns a conflict |
| `POST /messages` | Verify an exact published `envelope` and `signature`; accepted duplicates return retained receipts |

`delivery_fault` has `step`, `type` and optional `seconds`. Steps are exposed in the overview. Supported types are `drop_once`, `duplicate_once`, `lose_ack_once`, `corrupt_once`, `delay_once`. All amounts are integer paise. Request bodies reject unknown fields in the participant APIs and duplicate JSON fields at the shared ingress.

## Scenario plans and independent expectations

The Institution lab supports `upi.enrol`, `upi.prepare`, `upi.authorize`, `upi.route`, `upi.status`, `upi.refund`, `upi.expire`, `upi.assert`, `upi.qr`. Fixture account definitions optionally identify `kind: merchant`. Device proofs remain inside the executor and are excluded from step results and downloadable evidence.

`example_upi_plan()` declares a 21-step fixture: enrol payer/merchant, present collect, authorize, commit credit and lose its actual response, advance business time, recover, partially refund, compare expected balances, reconcile separately supplied participant positions, then expire an unapproved payment and prove balances are unchanged. Scenario evidence binds both the general plan profile and the participant contract hash. A deliberately wrong expected outcome must produce retained failed evidence.

Existing synthetic mandates, disputes and settlement tools remain available. They do not implement the complete official network interfaces for those features. No feature outside the declared participant profile gains an official compatibility claim by appearing in a scenario.

## Serving and qualification

The local launcher now uses `avenra.asgi`. HTTP parsing and connection handling run through an ASGI server. Application limits include bounded concurrent dispatch, 64 KiB bodies, header/framing validation, JSON validation and a body deadline. The shared router retains host/origin, permission and CSRF checks. Its old `http.server` transport is retained for compatibility and is not the local launcher's serving path.

API serving can run with `--mode api`; `python -m avenra.worker --data ...` runs durable work separately against private PostgreSQL configuration. Local process checks exercise two workers, duplicate request acceptance, worker removal and API restart. This does not establish a deployed HA cluster, institutional TLS ingress or a measured production SLO.

Native evidence is generated by `scripts/verify_upi.py` with SQLite and `--postgres`, `scripts/verify_upi_http.py`, and `scripts/verify_asgi.py`. The native participant suite actually kills a worker after committed credit, expires its lease inside the isolated test store, restarts the application and checks no duplicate effect. Retained failures remain under `.local/verification-attempts/`. The control/accounting restore verifier now includes all participant tables and retained signatures.

## Required external inputs and unfinished official scope

NPCI's public [certification support FAQ](https://www.npcisupport.org.in/portal/en/kb/certification/3-cz-certification-testing-tool-for-upi-aeps-imps-faqs) directs TSD downloads to the CZ dashboard and UAT progression to its certification contact. Public [bank certification manuals](https://www.npci.org.in/PDF/npci/certifications-zone/upi/UPI-user-manual-Bank.pdf) describe actor substitution against an older baseline. They do not supply a complete current accepted wire/security contract. Current [operating circulars](https://www.npci.org.in/circulars/upi) also need review for the selected role and effective release.

Still required are the selected current TSD/XSD/security specifications, amendments, response-code/timing/eligibility rules, authorized acceptance vectors, bank test identities/certificates/connectivity and observed acceptance. Official device/PIN encryption, complete mandates/UDIR/dispute and settlement interfaces, specialized UPI product variants and bank-compatible role substitution are unqualified. Public documentation cannot issue participant access or certify our implementation.

Institutional security assessment, service authentication/TLS, operational recovery, off-host retention and workload qualification also remain open. RBI Regulatory Sandbox admission is a separate institutional process. These gaps stay visible in the console and release inventory; no readiness flag promotes them automatically.
