# Independent participant cold recovery

This addition serves the original production sandbox objective. It extends the
existing app's coordinated recovery to synthetic payer PSP, payee PSP and network
services. It does not supply official UPI contracts, production credentials or
institutional acceptance. The deployed POC remains a separate verification
baseline until an explicitly qualified release is selected.

## State preserved

The signed recovery set now includes an `independent-participants` component:

- Original scoped worker and client authorities, TLS certificates, signing keys,
  versioned client credentials, revocation state and public sender registries.
- Consistent standalone SQLite checkpoints of the transport journal, immutable
  inbox receipts, payment observations, phase identities and acceptance events.
- The preceding physical origins, so the next recovery cannot accidentally reuse
  its source participants' addresses.

Capture holds the same exclusive process-lifetime locks as the inbox servers,
alongside the existing control database, accounting, bank and AA fences. Stop the
cooperating processes first. Rejected live-service capture creates no destination.
The component inventory is bounded and verified before materialization. Links,
extra files, modified identities and changed receipt hashes are refused.

The component manifest alone is not an external signature. The enclosing
coordinated recovery inventory authenticates its bytes against an independently
supplied recovery trust root. The application has its separate release signature.
Private keys, journals and recovery archives remain installation secrets under
`.local/`; they must never be committed to the source repository.

## Restore and restart

Use the existing `scripts/recovery.py` capture, stage and import-databases commands
described in [coordinated recovery](COORDINATED_RECOVERY.md). Stage creates an
inactive deployment and restores captured participant state automatically. Start
the owned native services and captured banks/AA roles as applicable, then provide
a private JSON mapping of **every captured participant worker ID** to a distinct,
new, unprivileged loopback port.

```sh
.local/runtime/bin/python scripts/recovery.py peer-start \
  --directory .local/recovery-target \
  --ports .local/recovery-peer-ports.json \
  --recovery-trust .local/recovery-trust.raw \
  --release-trust .local/release-trust.raw \
  --request-id ORIGINAL-UUID \
  --actor recovery-operator
```

Supply an actual UUID and retain it with the original port map and actor. An exact
retry reuses surviving owned processes. Changed attribution or ports cannot reuse
the accepted startup intent. No unrelated listener or reused PID is stopped.

The authenticated `peer-recovery.json` binds new physical listeners to captured
logical authorities, server certificate hashes, the target control database and
the verified offline runtime. Original connection JSON and accepted financial
identities are never rewritten. Each process registers its full OS identity and
executable image before the operator accepts its listener.

Restored inboxes allow signed probes while the installation is inactive. Message
delivery stays blocked. Normal launch without recovery context cannot bypass the
inactive guard or start a recovered server at its original address. Explicit
activation checks all captured services and every retained connection revision,
including inactive revisions referenced by historical work. It pins the routing
file in both deployment configuration and the activation receipt.

After activation, the same startup command can restart a failed owned participant
under its original plan. Current client credentials may rotate without changing
the logical authority. Each client checks recovery routing before connecting;
TLS and signed acknowledgements still authenticate the original recipient.

```sh
.local/runtime/bin/python scripts/recovery.py peer-stop \
  --directory .local/recovery-target
```

Stop API and financial workers before a planned coordinated shutdown. Stop the
participant, bank/AA and native processes through their owned stop commands.

## Release compatibility and verification

Recovered deployments require the literal capability
`peer_routes: ["avenra-peer-recovery-routes/1"]`. Release selection parses the
candidate's signed capability file without importing its code. An older signed
build lacking this capability is refused before changing financial/control rows
or the selected release pointer. A copied signing key is not sufficient evidence
of compatibility.

Run the dedicated qualification against private local services:

```sh
.local/runtime/bin/python scripts/verify_coordinated_recovery.py --independent
```

It uses new control/accounting databases and separate native engines, two bank
services and five PSP/network services. It exercises actual response loss,
operator/process death, credential rotation, immutable identity preservation,
inactive restoration, route tampering, incompatible rollback, new payment work
and two successive cold relocations. Original paths and listeners become
unavailable; observers check for attempted source connections. Receipts remain in
`.local/coordinated-peer-recovery-verification.json` and each attempt's private
QA folder. Failed attempts are retained.

This is local cold-recovery evidence for the declared synthetic protocol. It does
not establish multi-host high availability, broker replay, off-host retention,
institutional recovery targets, independent security assessment or official
participant interoperability. The complete production acceptance matrix still
requires qualification for the final release and deployment.
