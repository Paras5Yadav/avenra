# Institution lab

Implemented 18 October 2026. Open **Banking workspace → Institution lab** in the local console. This is executable synthetic financial testing, with persistent plans, native journal effects and retained evidence. Its participant envelope is an Avenra contract; it is not an implementation of an official UPI/AA wire protocol or a full independent PSP/bank state machine.

## Run an experiment

1. Select or create a banking environment. This is the source workspace; each experiment gets a separate fresh financial fixture.
2. Install the reviewed lab profile. Its body and hash are immutable; it declares nine synthetic rules covering identity, money, recovery, messages, time, consent, mandates, settlement and disputes.
3. Publish a multistep plan. The editor supplies a 15-step example with opening funds and independently declared expected results.
4. Run it in a fresh fixture. Inspect the retained step observations and download the signed evidence.

The example sends and replays an authenticated participant envelope, submits a 50,000-paise transfer, loses the real credit response, reconciles the original financial legs, verifies their authoritative journal postings, refunds 20,000 paise, checks exact final balances, grants consent, checks disclosure, advances virtual time to expiry, verifies denial, rejects an expired message and reconciles separate settlement observations.

Participant messages and financial commands are separate, explicit plan operations. A message receipt does not itself authorize a payment, implement a network switch or establish bank interoperability.

## Bounded operation library

| Operations | Behaviour |
| --- | --- |
| `payment.transfer`, `payment.reconcile` | Existing debit/credit identities, real journal effects and original-identity recovery |
| `payment.refund`, `payment.reversal` | Separate linked financial adjustments and existing amount-reservation constraints |
| `assert.operation` | Declared state plus independent journal reads checking effect identity, exact posting and uniqueness |
| `assert.balances` | Compare authoritative account balances with independently declared integer-paise expectations |
| `account.state`, `risk.rule` | Account restrictions and the reviewed local threshold-policy mechanism |
| `message.send`, `message.replay` | Role-scoped HMAC envelopes, version/scope/time validation, duplicate receipts and changed-content conflicts |
| `clock.advance` | Persistent, monotonic, idempotent advancement of fixture business time |
| `mandate.create`, `.approve`, `.schedule`, `.dispatch`, `.revoke` | Synthetic authorization, amount, occurrence, due-time, expiry and revocation checks |
| `consent.create`, `.approve`, `.read`, `.revoke` | Account-scoped synthetic data permission and expiry/revocation denial |
| `settlement.prepare`, `.reconcile` | Expected positions and separately supplied observed positions, retaining discrepancies |
| `dispute.create`, `.review`, `.resolve` | Existing guarded case transitions, separate from financial adjustments |

Plans contain `clock_epoch`, `banks`, `accounts` and `steps`. Bank/account/step aliases use 2–24 lowercase letters/digits/underscores, starting with a letter. Fixtures allow 1–16 banks and 2–32 accounts; plans allow 1–64 steps and at most 48 KiB of canonical JSON. Each operation has a closed argument set. No expression evaluator, script import or host command is provided.

A step contains `id`, `operation`, `args`, and optionally either `expect` or `expect_error`. Resource references name completed earlier steps; account references name declared fixture accounts. Expected output fields use exact canonical JSON equality. Assertions and expected errors are evaluated independently of the requested outcome. A missing expected error or wrong balance fails and seals the run; later steps do not execute. A failed receipt remains a failure if execution is interrupted before sealing.

## Clocks and identity

The fixture's frozen business clock drives participant-message validity, mandate due/expiry checks and consent validity at disclosure. Infrastructure leases, HTTP timeouts, audit timestamps and actual TCP faults continue to use wall time. These clocks are labelled separately in evidence. Virtual deadlines do not demonstrate production scheduler latency.

Clock advances have stable request keys and fingerprints. Concurrent repeats advance once; changed content is a conflict; negative advancement is rejected. A virtual clock is created only for the isolated experiment fixture.

Financial operations retain stable run/step request identities. Completed steps have immutable receipts. A persisted cursor and run lease allow an interrupted run to continue; the original financial identity is reused when an external effect committed before its step receipt. If a timing-sensitive intermediate observation changed during an interruption, its declared expectation may fail rather than fabricate the missing observation. An interrupted run requires its original executor-source fingerprint; a changed build must use a new run or an executor matching the original build.

## Participant message contract

The reviewed roles are `payer_psp`, `network`, `remitter`, `beneficiary` and `payee_psp`. These are synthetic message identities, not independently qualified external institutions. Envelopes bind format, environment, profile digest, sender, recipient, message key, kind, issue/expiry times and a bounded object payload. Lab signatures are derived from scoped local key material. They do not stand in for official participant PKI/HSM procedures.

An accepted identity returns its original receipt on exact authenticated replay; changed content/authentication conflicts. Rejected attempts do not reserve an accepted identity. An early message may be re-evaluated when business time advances, retaining the earlier rejection and the later accepted receipt. Validity is `issued_at <= business_time < expires_at`; expiry at the exact boundary is rejected. Version mismatch, invalid authentication, not-yet-valid and expired messages have distinct retained reasons. Malformed signatures, invalid schemas and cross-environment messages are rejected before receipt acceptance.

The scoped signing endpoint exists for authorized local test clients. It signs only this synthetic envelope. It does not provide external bank credentials or expose the master key. Evidence contains message signatures and original synthetic envelopes; signing/access secrets are not included. Consent access tokens are removed from step outputs; disclosure retains a data digest and counts.

## Evidence and qualification

Evidence pins the loaded application-source fingerprint, immutable profile, plan/version/digest, fixture and run identities, business time, step inputs by hash, actual outputs, message receipts, errors and completion state. It is sealed with the existing local evidence HMAC. Independent recomputation verifies integrity under that local key; institutional trust, remote attestation and external key custody are separate.

Immutable triggers protect profiles, plans, message/clock/step receipts and sealed run evidence. PostgreSQL and SQLite migrations preserve the older control tables. Backup/restore qualification now includes the new tables and signed experiment evidence.

The following remain unfinished: current official UPI/AA adapters and participant state machines, bank-compatible golden cases, product-to-payment funding bridge, actual Linux/KVM execution qualification, bank-connected acceptance and production hosting/security/operations qualification. The interface displays those boundaries explicitly. RBI Regulatory Sandbox admission remains an institutional process.

Run the retained qualification commands from the project root:

```sh
.local/runtime/bin/python scripts/verify_simulation.py
.local/runtime/bin/python scripts/verify_simulation.py --postgres
.local/runtime/bin/python scripts/verify_simulation_http.py
```

The native-engine suites exercise interruption after a financial effect, a new controller instance, concurrent executors, immutable receipts, exact deadline denial, message authentication/replay, independent wrong expectations and protection against turning an interrupted failure into a pass. They do not assert a full official protocol implementation or VM boot.

## Routed participant journeys

`upi.*` operations extend the bounded runner with explicit synthetic device verification, account links, routed pay/merchant collect/QR, authorization, status recovery, refunds, expiry and independent completed-evidence assertions. Account fixtures may set `kind: merchant`. The console’s Publish UPI journey action starts from a 21-step plan with independently declared financial outcomes. Participant contract hashes are included in scenario manifests; device proofs are excluded from exported results. See [the participant engine guide](UPI_PARTICIPANT_ENGINE.md).
