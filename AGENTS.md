# Avenra engineering contracts

- All default flows use loopback services and synthetic data. Keep live payment access disabled until a qualified adapter and deployment exist.
- The included accounting engine owns financial postings and balances. The console database stores intent and orchestration state, never a separately mutable balance authority.
- Persist request identity and its financial payload fingerprint before dispatch. Reconciliation must reuse the original identity. An unconfirmed response is not a decline.
- Keep workspace and environment checks on every API operation. Local operator access and scoped API keys have different scopes.
- Preserve licence and copyright notices. Record adapted source under LICENSES/. Product branding and original application documentation use Avenra.
- Regenerate protocol bindings after changing module paths; do not edit encoded descriptors as plain strings.
- Test financial changes against the real local engines. Use scripts/verify.py for the supported synthetic profile. Retain failed checks and their scope in verification records.
- VM execution needs qualified Linux/KVM workers. Helper tests and manifest resolution are not evidence of VM isolation or institutional certification.
- Do not run untrusted guest code as a host process. Disabled or unqualified capabilities must remain visibly unavailable.
- Keep installation data, keys, logs and generated evidence under .local/, excluded from version control.
