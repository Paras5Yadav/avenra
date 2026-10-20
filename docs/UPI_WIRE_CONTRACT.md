# Versioned synthetic UPI wire contract

This adds opt-in XML connection profiles to the existing app, API, durable
worker and accounting engine. Existing accepted payments keep their original
connection snapshots. The original flow profile and financial engines are
unchanged. No new user-facing app or real-money connector is introduced.

`avenra-upi-xml/1` is an owned synthetic contract. Its namespace is
`urn:avenra:upi:wire:1`; its media type is
`application/vnd.avenra.upi.v1+xml`. It is not an NPCI schema or XMLDSIG
implementation. The authentication element carries the original Ed25519
signature over the original canonical JSON payment envelope. Parsing restores
the exact numeric types, financial identity, scope and signed payload.

## Connections

Use the existing `/banking/upi/connections` create, validate and activate APIs:

| Adapter | Roles | Execution |
| --- | --- | --- |
| `avenra-xml-participant/1` | All five existing participant roles | XML encode, strict parse, schema/phase checks, original signature verification, then the existing durable participant handler |
| `avenra-xml-tls-synthetic-bank/1` | Remitter and beneficiary | The same XML boundary plus existing independent mTLS synthetic bank financial legs |

The local XML adapter accepts no remote endpoint or credential fields. The
secure bank variant requires the existing private scoped test identity and an
observed signed mutual-TLS probe before activation. Its bank command channel
continues to use the signed JSON bank-leg contract. XML participant validation
does not turn logical PSP/network actors into separately deployed services.

New accepted intents pin the selected connection and its exact contract hash.
Changing active connections cannot redirect an existing payment. One immutable
`wire.validated` event per message records its schema/contract hash, XML digest,
document digest and signing-key identity. It records validation, not a monetary
confirmation. Financial evidence continues to read native journal postings.

## Scoped API

Append these paths to the authenticated tenant/environment base:

| Method | Path | Result |
| --- | --- | --- |
| GET | `/banking/upi/wire/contract` | Descriptor, contract hash, schema digest and executable XSD |
| GET | `/banking/upi/wire/messages/{message_id}` | Versioned XML representation of an accepted immutable envelope |
| POST | `/banking/upi/wire/messages` | Authenticate XML and inspect the original delivery/receipt |

POST takes raw UTF-8 XML with the advertised media type, not a JSON wrapper.
It requires one Content-Length, a body of at most 8,192 bytes, and no content or
transfer encoding. Generic `application/xml` is deliberately not version
negotiation. There is no automatic protocol fallback.

Ingress follows the existing JSON message inspection semantics: it accepts
only an already registered envelope matching the original immutable intent.
It returns `financial_dispatch: false`; only the existing durable worker
executes financial effects. Authentic changed payloads conflict with the original
message. Duplicate inspection returns its retained receipt and cannot post money.

Parsing refuses unsupported namespaces, versions, fields, duplicate attributes,
wrong phase/kind/outcome combinations, fractional or overflowing paise,
noncanonical/nonfinite numbers, wrapping, additional elements, DTDs, entities,
comments, processing instructions and non-UTF-8 documents. Fields do not select
keys, accounts or environments outside the authenticated scope. No schema imports
or external entity loading is allowed. Parser settings follow the
[lxml parser API](https://lxml.de/apidoc/lxml.etree.html).

## Acceptance and limits

Run `python scripts/verify_upi_wire.py` and the same command with `--postgres`.
Each uses actual local accounting, two separate mutually authenticated bank
processes, the same ASGI app, exact native journal observations and a real child
process death after credit commit. Recovery waits for the actual original lease;
it does not edit the lease or replace the accepted operation. Failed attempts
remain private under `.local/verification-attempts/`.

`scripts/oracles/upi_wire.go` independently parses and authenticates observed
messages using Go's standard library. It imports no application parser, XSD or
signing helper. It verifies positive payment/refund/reversal/decline vectors and
rejects the hostile vector pack. Go is a qualification dependency; it is not an
application runtime dependency. The case-pack hash binds both verifier sources.
The two packs are mandatory local release gates.

`python scripts/verify_coordinated_recovery.py --xml` exercises the same XML
profiles through a full cold deployment restore. It captures the real native
financial state and separate bank receipts, imports new restricted databases,
rebuilds the signed application offline and starts the banks at authenticated
replacement addresses. Original envelopes, signatures, connection snapshots and
financial operation identities are retained. Recovery confirms an uncertain
credit once while the original paths and bank listeners are unavailable. This
mode has its own mandatory receipt; a legacy-mode receipt cannot satisfy it.

Release admission checks every retained connection revision, including inactive
revisions that an earlier payment can still reference. A signed candidate must
declare the exact adapter and contract hash. Capability inspection reads bounded
literal data without importing or executing candidate code. An incompatible
rollback is refused before a release pointer or control row changes; selected
runtime startup and cold capture/activation enforce the same requirement.
The acceptance pack uses a genuine signed predecessor to observe that refusal.

Official schemas, security formats, response codes, amendments and acceptance
vectors remain separate work. Production deployment and independent assessment
are also open. The original local/JSON-bank recovery path remains covered by
its separate acceptance mode. No official compatibility or full
production-readiness claim follows from this feature. NPCI's published
[onboarding FAQ](https://www.npci.org.in/what-we-do/upi/faqs) describes sponsor-bank
onboarding for TPAPs; this local contract does not supply that access.
