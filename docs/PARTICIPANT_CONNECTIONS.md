# Participant connections

Avenra supports immutable connection revisions and a trusted-code adapter boundary. `avenra-local-participant/1` runs the existing synthetic participant engine. `avenra-tls-synthetic-bank/1` executes bank financial legs through separate mutually authenticated HTTPS services using private lab identities. `avenra-external-upi-pending/1` is an institutional preparation record, not a working official bank connector. None asserts official UPI compatibility. See [synthetic bank services](SYNTHETIC_BANKS.md) for executable demonstrations and limits.

The opt-in `avenra-xml-participant/1` and `avenra-xml-tls-synthetic-bank/1` revisions add a source-bound XML/XSD and original-signature validation boundary while reusing the same payment worker. The secure variant retains the existing JSON/mTLS bank command channel. See [the wire contract and qualification limits](UPI_WIRE_CONTRACT.md).

In the console, open **Banking → UPI participants → Connection settings**. Install a participant topology before creating a connection. Create a local revision, check its configuration, then choose **Use for new payments**. The configuration check establishes compatible local configuration, not security assessment or institutional acceptance. Checks bind the revision, profile and application build and are signed with the installation evidence key.

Each newly prepared payment snapshots all five participant adapter choices. Authorization binds that snapshot. New bindings affect subsequent intents; previously accepted requests and recovery keep the original revision. Database triggers prohibit changing the snapshot, connection configuration and retained checks. Existing schema-eight intents retain their historical local adapter path. Local simulation stays available without institutional credentials.

Institutional drafts accept only an HTTPS endpoint, an optional institutional reference and an optional `secret://name` reference. They reject embedded credentials, query parameters, raw token/key fields and filesystem paths. No request, DNS probe, secret resolution, certificate check or bank acceptance is performed for these drafts. A check retains failures and lists missing specifications, implementation, access, acceptance and deployment qualification. Adding a secret reference cannot enable this unfinished institutional adapter. Imported configuration cannot load executable adapters. The separate TLS synthetic adapter requires a provisioned private test identity and performs an actual loopback TLS/signature probe; it cannot use arbitrary external hosts.

## API

All paths are under `/api/tenants/{tenant}/environments/{environment}/banking/upi` and use the existing authentication, permissions and scope guards.

| Method | Path | Behaviour |
| --- | --- | --- |
| GET | `/connections` | Declared profiles, requirements, access routes and scoped connection revisions |
| POST | `/connections` | Create an immutable revision using `name`, `participant_id`, `adapter_id`, `request_key`; external drafts additionally require `endpoint` and optionally accept `secret_reference`, `institution_reference` |
| POST | `/connections/{id}/validate` | Body `{}`; retain a signed configuration check, including failures |
| POST | `/connections/{id}/activate` | Body `{"check_id":"..."}`; bind a locally executable revision after validating the exact revision and current build |

## Authoritative specifications and acceptance

Public sources identify these access routes:

- The [certification support FAQ](https://www.npcisupport.org.in/portal/en/kb/certification/3-cz-certification-testing-tool-for-upi-aeps-imps-faqs) places TSD downloads on the certification dashboard and progression into UAT with the assigned certification contact.
- The [operations support FAQ](https://www.npcisupport.org.in/portal/en/kb/upi/upi) places operating/settlement guidance and some circulars on the bank-facing portal.
- The [vendor access FAQ](https://www.npcisupport.org.in/portal/en/kb/articles/whether-asps-can-have-user-access-to-initiate-track-the-request-for-respective-banks) describes vendor access associated with particular bank requests. It does not grant arbitrary independent access to us.

We have not obtained a complete current role-specific contract or authorized acceptance pack. Public ecosystem descriptions and historical documents are not substitutes for that contract. A bank can engage Avenra as a testing-software vendor; building this simulator is a different scope from becoming a payment participant. Exact eligibility, documents and technical access must be agreed for the intended engagement.

## Work still required

The boundary does not implement official external message codecs, official credential processing, independent institutional key custody, external balance/account authority or bank-connected UAT. Those require separately implemented adapters and tests. The TLS lab adapter resolves private software-held test identities; future institutional secret-manager and signing providers need their own implementations and acceptance. Switching to a bank-owned account must preserve its financial authority and use independent observations; it must not independently mutate a competing local balance.

Institutional security deployment, load/availability testing, encrypted off-host retention, disaster recovery and independent assessment remain separate engineering work. Source-defined external requirements cannot be promoted by entering a token or importing a document.
## Independent PSP and network POC profile

`avenra-xml-tls-participant/1` adds separate loopback mutual-TLS services for
synthetic payer PSP, payee PSP and network inboxes. Each service authenticates
original XML against its explicitly synchronized public registry, persists its
own immutable receipt and returns a signed scope-bound acknowledgement. The
existing coordinator advances only after authenticating that acknowledgement.
Receipts explicitly do not confirm native money movement. Native bank legs and
accounting retain their existing authority and original financial identities.

The same-app POC kills a network process during an uncertain committed credit,
restarts that original service and completes recovery and a linked partial refund.
Activation requires the private scoped identity and an actual authenticated probe.
This is an owned synthetic protocol, not official participant interoperability.

Cold relocation of these new PSP/network services remains unqualified. Their
service refuses recovery-mode startup rather than silently using original
listeners. The existing bank/AA cold-recovery receipts do not qualify this new
profile. Production qualification must add authenticated checkpoint, routing,
credential/registry restoration and original-message recovery for these services.

