# Base snapshots

[Documentation](../README.md)

A base snapshot saves a booted template's memory and VM state. New sandboxes
restore from the snapshot instead of booting from scratch, which makes creates
much faster. On a single node, you can also keep a small pool of ready
sandboxes built from that snapshot.

This page is for a single worker node run directly with the node managers.
On Kubernetes, use [warm pools](warm-pools.md) instead: local settings on a
Kubernetes-managed node are overwritten by the control plane.

Run the commands below on the worker node. The binaries are in `/avenra/bin`.

## Create a base snapshot

```bash
sudo /avenra/bin/avenra-tmplmgr base-snapshot --template my-template --state true
sudo /avenra/bin/avenra-tmplmgr list
```

The snapshot is ready when the template's `BASE SNAP` column shows `on`. A
snapshot belongs to one template release, so check again after you publish a
new release.

To remove it, run the same command with `--state false`.

## Create a node warm pool (optional)

```bash
sudo /avenra/bin/avenra-tmplmgr create-pool --template my-template --boot-mode snapshot --count 5
sudo /avenra/bin/avenra-tmplmgr pool-status --template my-template
```

Create the base snapshot first so the pool fills from it. Remove the pool with
`avenra-tmplmgr delete-pool --template my-template`.

## Start a sandbox

```bash
sudo /avenra/bin/avenra-sbxmgr create --runtime fc --template my-template \
  --boot-mode pool --blocking
```

| `--boot-mode` | What it does |
|---|---|
| `cold` (default) | Full boot. |
| `base-snapshot` | Restore the base snapshot. |
| `pool` | Use a ready pool sandbox, else restore the snapshot. |

If what you asked for isn't available, the create falls back without an error,
down to a cold boot. Check the `Boot mode:` line in the output to see which one ran.

## Benchmarks

`scripts/benchmarks/bench-sbx-lifecycle.sh` finds the template's base snapshot
and pool automatically. It skips the snapshot and pool cases when they don't
exist.
