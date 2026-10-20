# Security and test material

Avenra's default application flows use synthetic participants, synthetic funds
and loopback services. Live payment access is disabled. A passing local POC is
not institutional production, regulatory, protocol or independent security
acceptance. See `docs/POC_ACCEPTANCE.md` and
`docs/PRODUCTION_COMPLETION_MATRIX.md` for the qualified scope and remaining work.

Included engine source retains its existing public test vectors, sample tokens,
test certificates and development fixture keys. These are source fixtures, not
trusted operational credentials. Do not use them as institutional identities or
production secrets. The application generates/resolves its installation-specific
private identities under `.local/`; that directory is excluded from Git.

Keep real credentials, private deployment configuration, financial state,
databases, VM disks, logs, recovery archives and private evidence out of commits.
Use fresh operator-managed identities when provisioning an installation.

Source publication does not provision native services, the VM worker, selected
public AA contract caches or private deployment state. Follow the product setup
and module-specific documentation before running the acceptance packs.
