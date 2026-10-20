# Qualification storage

Large local rehearsals must not exhaust the developer host while testing
production recovery. The following entry points acquire a shared volume lease
and check available bytes before creating their QA case or starting services:

| Entry point | Conservative write headroom |
| --- | ---: |
| `scripts/verify_coordinated_recovery.py`, including `--xml` and `--independent` | 6 GiB |
| `scripts/verify_aa_recovery.py` | 6 GiB |
| `scripts/verify_identity_recovery.py` | 6 GiB |
| `scripts/verify_releases.py` | 4 GiB |
| `scripts/verify_capacity.py`, including `--smoke` | 2 GiB |

Each requires this headroom **in addition to a 16 GiB free-space floor**. They
repeat the check before major runtime/capture/restore or load phases. The
complete conservative headroom remains required at every checkpoint; changes
in host free space cannot reliably attribute consumed bytes to one test.

The kernel-held advisory lock is shared by covered rehearsals across checkouts
on the same filesystem volume. A competing rehearsal refuses with
`QUALIFICATION_STORAGE_BUSY`; it does not evict an owner based on PID reuse or
elapsed time. Owner exit releases the lock, including actual process death.
Crash-left metadata is preserved as historical evidence and is not proof of a
live process. The admission files are private regular files; links, hard links
and public lock permissions are refused.

Low space returns `QUALIFICATION_DISK_SPACE`. A refusal before execution records
a failed latest qualification summary rather than leaving an older passing
summary selected. Inaccessible, linked or unwritable admission state is also a
typed refusal, including failure while acquiring or retaining the lease; a
secondary metadata error cannot leak the lock or hide that refusal.
Its earlier full case record remains intact. Per-attempt
admission records are retained under
`~/.local/share/avenra/qualification-storage/`, with observed available bytes,
thresholds and checkpoint outcomes. Completed runs also include the admission
snapshot in their retained report. No credential material is recorded there.

This is a **preflight and checkpoint guard, not a filesystem quota**. Other apps,
uncovered test commands and writes between checkpoints can still consume disk.
The guard measures the checkout's filesystem; separately mounted database or
VM storage needs its own volume controls. It does not establish production
runtime disk isolation or a whole-product retention policy.

There is no automatic deletion of QA cases, failed records, keys, releases,
databases, financial state or VM images. Retention cleanup must preserve active
references and required release/recovery evidence and remain a separately
reviewed operation. The current engineering floor and headroom are conservative
local limits, not an institutional capacity or recovery guarantee.
