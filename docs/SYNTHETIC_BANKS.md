# Synthetic bank services and cryptographic rehearsal

Two separate Avenra bank processes can execute synthetic financial legs through mutually authenticated HTTPS. Request and response documents carry independent Ed25519 signatures. Test certificates are issued by private local certificate authorities. No system trust-store change or disabled certificate validation is required.

The native accounting engine remains the sole balance authority. Each bank service has a separate SQLite command/receipt database and an account-address allowlist. These databases hold identities, immutable request fingerprints, observations and receipts; they do not contain independently mutable customer balances. Logical PSP/network actors remain in the application process. This is an executable synthetic bank-leg contract, not an implementation of a current official UPI wire/security specification or an independent bank core.

## Run the demonstration

```sh
.local/runtime/bin/python scripts/verify_synthetic_banks.py
```

This creates an isolated fixture, runs actual separate services and stops those services after verification. Failed and successful reports are retained under `.local/verification-attempts/`.

To leave a tested demonstration running in the console's PostgreSQL environment:

```sh
.local/runtime/bin/python scripts/verify_synthetic_banks.py --keep-running
.local/runtime/bin/python scripts/synthetic_bank_services.py status
```

In the console, select the environment beginning **Synthetic bank services**, then **Banking workspace → UPI participants → Connection settings**. Four bank-role revisions use `avenra-tls-synthetic-bank/1`. **Check configuration** performs an actual mutual-TLS and signed probe. The supplied identities are synthetic and expire after seven days.

The retained payment demonstrates merchant collect, distinct remitter debit and beneficiary credit, an actual lost HTTPS response after credit commit, signing-identity rotation/revocation, recovery of the original financial leg and a partial refund with reversed bank roles. It also demonstrates insufficient funds, a separate compensating reversal and a beneficiary outage followed by restoration of bank identity/receipt files. Evidence includes native journal observations and signed bank transport attempts, including the uncertain attempt.

Stop only the managed demonstration services with:

```sh
.local/runtime/bin/python scripts/synthetic_bank_services.py stop
```

Stopping services preserves their financial fixtures and evidence. The installation refuses to create another kept-running demonstration until its previous managed processes are stopped. The main console launcher and synthetic bank lifecycle commands manage separate process sets.

## Security boundary

- Connections require literal `https://127.0.0.1:<port>` origins. No arbitrary-host transport or redirect following exists in this adapter.
- A scoped `secret://name` resolves under the installation's private `participant-secrets/` directory. The public API never receives private keys or token values.
- Certificates and keys have private owner-only permissions. Symlinked identity references and unsafe filenames are rejected.
- TLS verifies the server trust chain and hostname; the service requires a client certificate. Application signatures additionally bind the bank, workspace, environment, participant, role, transport profile, request identity and validity interval.
- A repeated transport identity with changed signed content is refused. Each financial leg retains its immutable native reference and posting fingerprint across retries.
- Credential epochs can rotate while the bank authority and accepted financial intent stay pinned. The service reloads its trusted client-key registry, so revoking a signing key takes effect without restarting the service.
- These are software-held test keys on the same host. They are not HSM custody, separate OS-user isolation or official PIN/Common Library processing.

TLS protects the application-to-synthetic-bank boundary. The console and native accounting loopback connections retain their existing deployment configuration; this feature does not claim all application links or disks are encrypted.

## Acceptance evidence

The native suite exercises certificate absence, wrong trust roots, hostname mismatch, an expired client certificate, invalid signatures, expired commands, transport replay/conflict, cross-environment denial, unsupported commands, raw duplicate JSON fields, revocation, credential rotation and concurrent financial replay. It compares exact financial outcomes with independently retrieved native postings. Wrong amounts and foreign account addresses are rejected at the bank service.

The first end-to-end attempt exposed a receipt de-duplication defect. Transport records now use their request identities rather than one shared message kind. The failed attempt remains retained.

The local control/accounting backup now snapshots private bank identities and consistent bank receipt databases. Isolated restoration checks file hashes, private permissions, identity authority bindings and bank receipt counts. The demonstration also stops a bank, restores its identity/receipt checkpoint and resumes a pending payment. This is local component recovery evidence; measured cluster failover, off-host disaster recovery and institutional acceptance remain separate work.

## Future official integration

`avenra-external-upi-pending/1` remains a nonexecuting institutional preparation profile. It cannot inherit official compatibility from the synthetic transport. A future adapter must implement the applicable role/version-specific messages, security components, codes, timing and acceptance cases. The synthetic services remain available for regression testing after an authorized official adapter is added.
