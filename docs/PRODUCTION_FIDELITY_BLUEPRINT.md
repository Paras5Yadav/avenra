# Avenra production fidelity blueprint

Prepared 18 October 2026. This is an engineering design and acceptance checklist. Writing this document does not enable any additional capability, establish protocol conformance, or qualify a deployment.

The target is a dependable testing product that reproduces the externally observable behaviour of explicitly supported banking and payments profiles. A bank must be able to substitute its own test system for a simulated participant. Exact equivalence to undisclosed bank internals or an unknown network version cannot be established from public materials.

The first executable part of this blueprint is now available as the [Institution lab](INSTITUTION_LAB.md): a reviewed synthetic participant envelope, persistent virtual business time and a bounded multistep financial runner. Official participant adapters/state machines and institutional qualification remain unfinished.

The current implementation is described in [BANKING_IMPLEMENTATION.md](../BANKING_IMPLEMENTATION.md). Preserve its authoritative accounting boundary, persistent identities, independent financial observations, tenant checks and retained evidence while extending its functionality.

## 1. Establish the specification and coverage boundary

Create an immutable profile for each supported participant role and protocol/version combination. Record the governing documents and adopted changes, source and document hashes, effective dates, feature eligibility, jurisdiction/currency, bank-specific extensions, test configuration and known ambiguities. Do not silently combine rules from different versions.

For every known requirement, retain:

| Field | Purpose |
| --- | --- |
| Requirement ID | Stable identifier for a testable obligation |
| Authority and source location | Official document/version/section, participant contract, or explicit simulator assumption |
| Document digest and effective period | Pin the rule that produced a result |
| Participant and feature | Identify where the requirement applies |
| Input/output contract | Fields, types, lengths, signatures, codes, messages and ordering |
| State and financial effects | Permitted transitions, posting identities and balance/position assertions |
| Timing | Deadline, clock, calendar, retry and late-message behaviour |
| Positive and negative cases | Executable coverage with an independent expected result |
| Oracle and provenance | Source of expected messages, outcomes and financial assertions |
| Qualification status | Unspecified, designed, implemented, locally exercised, independently interoperated, partner accepted |
| Known deviation | Explicit incompatibility and its impact |

Report coverage only against the named profile and known requirement inventory. Unknown requirements and unsupported features must remain visible. A skipped or unimplemented mandatory case cannot count as a pass. A percentage is not evidence of coverage of every bank or every network version.

NPCI's public [certification-zone manual](https://www.npci.org.in/PDF/npci/certifications-zone/upi/UPI-user-manual-Bank.pdf) describes a bank performing one participant role while the simulator supplies the remaining roles. Use that substitution concept as an architecture reference. Its stated older TSD baseline is not a current wire specification.

Review current operating changes separately in [NPCI's UPI circulars](https://www.npci.org.in/circulars/upi).

## 2. Build independently observable participant models

Model the client/TPAP where applicable, payer PSP, payee PSP, remitter bank, beneficiary bank and simulated network as distinct actors. An actor needs its own identity, endpoints, accepted protocol profile, durable state, message identities, clock/timing rules, availability and failure controls.

These are logical boundaries. Process boundaries should support independent failures and the declared deployment model; there is no requirement to turn every function into a microservice.

The network actor routes messages and records its view of outcomes and obligations. It must not directly edit a bank's customer balances. Each virtual bank records its own observations and owns its declared accounting domain. Customer balances have one authoritative owner. With an actual bank test system attached, the bank owns that system's balances; Avenra records observations rather than inventing a second editable copy.

Support selecting the system under test by role. Run the same relevant case pack first with all actors simulated and then with the selected actor replaced by the independent test system. Compare observable contract behaviour, financial results and timing under the same profile. The synthetic baseline alone cannot establish external compatibility.

## 3. Match message and API behaviour

Implement the selected interface exactly rather than presenting an internal transfer API as an official bank protocol. For each operation test:

- Endpoint, method, headers, content type, encoding and supported version.
- Mandatory, optional, conditional and forbidden fields; defaults only where specified.
- Types, exact monetary precision, field lengths, allowed characters, case handling, namespaces and enumerations where applicable.
- Business identifier versus transport/message identifier; correlation and original-reference rules.
- Timestamp parsing, permitted skew, expiry, duplicate detection and replay window.
- Request authenticity, participant authorization, signature verification and key selection.
- Successful, pending, rejected, malformed and unsupported-version responses.
- Response codes and their financial meaning; internal errors must not become arbitrary official codes.
- Synchronous acknowledgement versus asynchronous business completion.
- Repeated requests with identical content, changed content under one identity, duplicate notifications, missing notifications and out-of-order delivery.
- Pagination, query semantics, empty results, rate limiting and bounded input/output sizes where the profile defines them.

Keep exact official field names, codes and deadlines in the versioned adapter/profile, backed by their source. Internal state names used by Avenra must be identified as internal. Do not manufacture a wire specification from an architectural booklet.

## 4. Cover customer and device journeys

Build the journeys applicable to the supported profile: registration, account discovery/linking, identifier resolution, device-binding challenges, credential setup/reset, payer authorization, merchant identity/QR or intent handling, limits, and the relevant payment/mandate journeys.

Use synthetic identities, challenge responses and lab credentials. Test invalid/expired/repeated challenges, disabled or changed devices, blocked accounts, restricted recipients, revoked permissions and attempts to use a different customer's identity. Do not collect real customer PINs or reuse production secrets to make a demonstration appear realistic.

Where a profile depends on a certified SDK, secure hardware, SIM, biometric subsystem or bank test credential, model the interface and failure behaviour explicitly. A software emulator does not establish the physical or cryptographic assurance of that external component; qualify its actual integration separately.

## 5. Complete financial state machines and accounting

For each operation specify who can authorize it, which actor owns each step, permitted transitions, terminal/nonterminal outcomes and the financial effect of each transition. Persist financial command identity and payload fingerprint before dispatch.

Cover successful authorization/debit/credit, definitive rejection, uncertain debit, uncertain credit, late success, late rejection, duplicate messages, a status response that conflicts with a local view, account freezes/closures, insufficient available funds, limits and participant outages as applicable.

Implement linked refunds/reversals, mandate lifecycle/execution, disputes and settlement/reconciliation according to the selected profile. Their existence as local business objects is not a substitute for the corresponding participant messages and rules.

Financial acceptance must check:

- Exact amount and currency, account identities and original operation references.
- One authorized financial effect per logical leg; replaying a message cannot create another effect.
- Valid double-entry postings, explicit opening funding and no unexplained creation/loss of value across customer, suspense, fee, refund and clearing accounts.
- Reservations, holds and available/book balance distinctions where required.
- Refund/compensation amount constraints, including concurrent requests.
- Independent observations of debit, credit and recovery, rather than trusting the orchestration status alone.
- Uncertain operations remain identifiable; compensation follows the selected rule and is not inferred solely from a timeout.
- Late results cannot create duplicate credit or conflicting compensation.
- Reconciliation exposes unresolved differences instead of changing a report to match expectations.

Complete the product-to-payment bridge. Native banking products and payment accounting currently have separate scopes. Define the authoritative owner of every account, stable cross-service identities, exact native-unit/minor-unit conversion, funding/suspense entries and recovery at every handoff. Do not fund an account by copying a balance from another system.

## 6. Implement real time, simulated time and scheduled work

Separate wall-clock operation from a controllable scenario clock. Record the clock mode in evidence. Deadlines, limits, cutoffs, grace periods and retries come from the selected profile, not an arbitrary global timeout.

Exercise just-before, exactly-at and just-after boundaries; message arrival before/after expiry; day/month/year transitions; timezone conversion; applicable business calendars; downtime spanning a cutoff; delayed scheduler execution; and clock correction/skew.

Use controlled time to run long mandate/dispute/settlement scenarios quickly, then verify relevant behaviour under real clocks, actual queues and network delays. A virtual-clock pass cannot establish production latency or scheduler performance.

## 7. Make failures happen at the boundary being claimed

Injection controls should name the actor, operation/leg, phase, occurrence, duration and seed. Distinguish a request never sent, a request dropped before processing, a committed operation with a lost response, a queue redelivery and a delayed callback.

Use actual sockets/proxies for network claims; actual process termination for process-recovery claims; actual datastore/broker disruption in an isolated test deployment for persistence/failover claims. An application-level discarded response remains useful, but label it accurately.

Cover connection refusal/reset, connect/read timeout, partial/truncated response, malformed payload, temporary server error, rate limiting, delayed/duplicate/out-of-order delivery, participant partition and recovery, API/worker crash, database failure, broker/cache loss, certificate rollover and storage pressure. Add saturation/hot-account/queue-backlog tests against the declared capacity.

Never execute destructive infrastructure fault cases against unrelated local services or shared production systems. Use the owned qualification environment.

## 8. Add settlement and operational back-office detail

Model participant clearing positions separately from retail customer balances. Generate versioned synthetic settlement cycles, transaction and reconciliation reports, and exception queues with the required file/message formats.

Exercise missing/duplicate records, broken correlation, amount mismatches, late transactions, cutoff crossing, failed cycle jobs, reruns, disputes/refunds spanning cycles and adjustments. Test that independently supplied observations can disagree with expected positions.

Add operator investigation, explicit correction/approval workflows and traceable status history. Never repair a financial discrepancy by editing an old journal entry or deleting failed evidence. These are synthetic settlement positions and reports; no movement in an actual settlement account is asserted.

## 9. Build AA as a separate interoperability profile

Implement the chosen AA/FIP/FIU APIs, their asynchronous notifications, account discovery/linking, consent artefact lifecycle, financial information requests/fetches, permitted data ranges, frequency/use restrictions and FI schemas as applicable. Implement the prescribed signature and encryption/key-exchange procedures for those versions.

Test revoked/expired/not-yet-valid consent, wrong data recipient/purpose/account, invalid signatures, wrong/rotated keys, replayed requests, stale notifications, partial provider responses, schema mismatches and attempted disclosure outside the approved scope. Test access decisions at the disclosure boundary and preserve denied-request evidence.

Keep an explicit role/version matrix, test vectors and independently validated datasets. Resolve the existing quarantined public contract files rather than silently treating malformed or contradictory documents as validated. Adoption/publication metadata must be reconciled with the documents selected for the profile.

ReBIT publishes [role-specific and interoperability specifications](https://api.rebit.org.in/).

## 10. Make scenarios a composable test system

Extend the existing bounded single-payment banking plan to a reviewed multistep operation library. Each plan declares its starting institutions/customers/accounts, source balances, profile/version, actions, independent expected outcomes, actor-specific faults, clock, seed, resource budget and cleanup policy.

Use immutable case revisions. A plan can create fixtures, call supported operations, advance simulated time, inject an approved fault, await an observation and assert results. Arbitrary uploaded scripts need the separately qualified execution layer; do not run them as host processes.

Use golden contract vectors, partner-supplied cases and independent implementation comparisons. Add generated boundary cases and state-machine/property tests. Expected messages and balances must not be copied from the output being tested. A hand-coded simulator talking to another copy of the same code has a shared-defect risk.

Record pass, fail, blocked, unsupported and not-run distinctly. Retain failed and interrupted runs. A passing catalogue of selected cases does not establish coverage of all protocol behaviours.

## 11. Example acceptance scenario

This is a proposed internal test, not an official message sequence or deadline:

1. Start isolated virtual banks A and B with independently declared opening funds.
2. Submit one authorized synthetic payment for 50,000 paise under a fixed profile and business identity.
3. Allow the beneficiary credit to commit, then lose its acknowledgement at that participant's transport boundary.
4. Observe the appropriate unresolved local view without classifying uncertainty as a definitive rejection.
5. Terminate the orchestrating worker and restart it.
6. Deliver a duplicate request and the delayed original response in the chosen order.
7. Recover by the original identities and apply the profile's recovery rules.
8. Assert one debit and one credit, exact customer/suspense/clearing outcomes, valid notifications, retained uncertain history and no duplicate effect.
9. Request a linked partial refund for 20,000 paise; race duplicate requests and verify one authorized refund effect.
10. Reconcile both banks' observations and the cycle positions. Retain an intentional mismatched external observation as an exception.

Repeat with each role replaced by an independent test system. At every financial handoff, vary crash timing, message order and boundary values. Add variants from the adopted specification rather than guessing official code/timing behaviour.

## 12. Qualify security and the simulator's own operation

The banking model is intentionally synthetic; the platform security boundary must actually work. Qualify identity federation and role policies, bank/tenant isolation, least-privilege database/service roles, authenticated/encrypted transport, trusted lab certificate issuance/rotation, secret custody/rotation, controlled egress, resource quotas and administrator audit.

Use an isolated lab trust domain for simulated certificates/keys. Where relevant, test expiry, wrong issuer, bad signature, replay, rotation overlap and revocation semantics according to the selected profile. Test the real credential/HSM integration separately when the supported participant contract requires it.

Keep untrusted VM workers separate from the financial control plane. Verify actual Linux/KVM lifecycle, guest filesystem and network isolation, scoped short-lived run credentials, CPU/memory/disk/output/time limits and cleanup after host or guest failure. Included source and HTTP lifecycle tests are not VM-isolation evidence.

ASGI request serving and separate durable worker processes now execute locally. Complete and qualify the institutional deployment of those services. Qualify controlled schema migrations, fencing/leases, backpressure, queue recovery, graceful shutdown, rolling upgrade/rollback and dependency compatibility.

Declare workload and objectives before benchmarking: concurrency, transaction mix, report size, hot accounts, client behaviour, arrival bursts, latency/error budgets, recovery point and recovery time. Measure those objectives. Do not invent throughput claims from the number of passing tests.

Qualify encrypted off-host retention and restoration on clean infrastructure, including keys, database/journal consistency, message/broker state and unresolved financial commands. Database-only restore evidence is useful but does not establish full application disaster recovery or HA.

## 13. Produce reviewable release evidence

Every qualification report should bind the exact application/engine/container digests, dependency inventory, schema migration version, deployment configuration, protocol/case-pack versions, fixture seed, topology, clock, faults and independent expected outcomes to the observed result.

Retain redacted message traces, actor state transitions, actual timings, journal references/postings, reconciliation findings, recovery/cleanup outcomes and explicit limits. Preserve sensitive test secrets outside downloadable evidence. Use retained signed evidence and independent audit anchors with a documented key trust/rotation policy; a local administrator's signature alone is not independent attestation.

Require independent security assessment and partner acceptance for the applicable externally connected scope. Public documentation cannot issue institution credentials, certificates, network allowlisting or acceptance sign-off. RBI Regulatory Sandbox admission and live participant authorization are separate institutional scopes, not software readiness flags.

Release profiles independently: institutional synthetic hosting, specific bank/UPI UAT integration, AA interoperability, isolated execution and any separately authorized live connector. Each profile passes only on evidence for its released build and deployment. Unqualified features stay unavailable and labelled with their missing requirements.

## 14. Recommended implementation sequence

1. Build the profile/requirement registry and actor interface boundaries alongside production hosting/security work.
2. Complete the product/payment bridge, durable multistep scenarios, clock controls and independent financial assertions.
3. Add the selected UPI participant adapters and case packs, then AA role-specific interoperability. Use synthetic lab credentials until authorized external test identities exist.
4. Extend boundary-level faults and reconciliation; independently qualify VM execution where scripts/clients require it.
5. Qualify capacity, cross-tenant security, full recovery, upgrades and operational procedures on the intended deployment.
6. Run controlled institutional acceptance for the supported roles. Bind release approval to the tested artifacts and explicit supported-version matrix.

No additional open-source repository by itself completes this sequence. The existing engines supply implementation components; Avenra must supply the integrated behavioural model, independent verification and qualified operation.

The executable [participant engine](UPI_PARTICIPANT_ENGINE.md) now connects signed actor delivery to separate bank financial phases and the multistep runner. Its synthetic profile remains distinct from current official wire/security qualification.
